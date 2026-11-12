"""메인 창: 상단 바(연결·시각·녹화·로그 가져오기·열기·글자 크기), 재생 바, 탭 4개.

실시간 상태(live)는 녹화를 재생하는 동안에도 계속 갱신하고 녹화도 계속한다.
화면은 지금 보는 쪽(실시간 또는 재생)의 MonitorState를 그린다.
"""

from __future__ import annotations

import queue
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from aesa_monitor.core.config import MonitorConfig, TrackingParameters
from aesa_monitor.core.events import PEER_CONSOLE, PEER_SIMULATOR, SOURCE_CAPTURE, Event
from aesa_monitor.core.settings import MonitorSettings, save_settings
from aesa_monitor.core.state import MonitorState
from aesa_monitor.io.processor_log import CATEGORY_REJECTED, LogEvent
from aesa_monitor.io.recording import (
    PROCESSOR_LOG_DIRECTORY, RECORDING_NAME, Recorder, new_session_directory,
)
from aesa_monitor.io.replay import SPEEDS
from aesa_monitor.io.session import Session, load_session
from aesa_monitor.ui.analysis_tab import AnalysisTab
from aesa_monitor.ui.beam_tab import BeamTab
from aesa_monitor.ui.comm_tab import CommTab
from aesa_monitor.ui.fetch_dialog import FetchDialog
from aesa_monitor.ui.style import (
    BACKGROUND, BAD, FONT_BODY, FONT_SMALL, FONT_TITLE, Fonts, MUTED, OK, PANEL, TEXT, UNKNOWN, WARN,
    format_bytes, format_duration, format_scale, format_time,
)
from aesa_monitor.ui.track_tab import TrackTab

REFRESH_MS = 50
MAX_EVENTS_PER_REFRESH = 20000
TAB_REFRESH_SECONDS = {"beam": 0.05, "track": 0.2, "comm": 0.25, "analysis": 0.25}
TAB_TITLES = {"beam": " 빔 ", "track": " 추적 ", "comm": " 통신 ", "analysis": " 분석 "}


class MonitorApp:
    def __init__(self, root: tk.Tk, events: "queue.Queue[Event]", config: Optional[MonitorConfig],
                 settings: MonitorSettings, settings_path: Path, connection=None) -> None:
        self._root = root
        self._events = events
        self._config = config
        self._settings = settings
        self._settings_path = settings_path
        self._connection = connection
        self._live = MonitorState()
        self._session: Optional[Session] = None
        self._replaying = False
        self._recorder: Optional[Recorder] = None
        self._record_dir: Optional[Path] = None
        self._last_frame = time.monotonic()
        self._slider_dragging = False
        parameters = config.tracking if config else TrackingParameters()
        server_text = f"{config.server[0]}:{config.server[1]}" if config else "접속 안 함"

        root.title("AESA 데이터 처리부 모니터")
        root.configure(background=BACKGROUND)
        root.geometry("1440x920")
        root.minsize(1100, 720)
        self.fonts = Fonts(root, settings.font_size)
        self._initial_font_size = settings.font_size

        self._build_top_bar()
        self._build_replay_bar()
        self._notebook = ttk.Notebook(root)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self._tabs = {
            "beam": BeamTab(self._notebook, parameters),
            "track": TrackTab(self._notebook, parameters),
            "comm": CommTab(self._notebook, server_text),
            "analysis": AnalysisTab(self._notebook, self.choose_folder, self.choose_file, self.fetch_logs,
                                    self.focus_event, self.select_run),
        }
        for key, tab in self._tabs.items():
            self._notebook.add(tab.frame, text=TAB_TITLES[key])
        self._last_tab_refresh = {key: 0.0 for key in self._tabs}

        root.bind("<Control-plus>", lambda _event: self.change_font(1))
        root.bind("<Control-equal>", lambda _event: self.change_font(1))
        root.bind("<Control-KP_Add>", lambda _event: self.change_font(1))
        root.bind("<Control-minus>", lambda _event: self.change_font(-1))
        root.bind("<Control-KP_Subtract>", lambda _event: self.change_font(-1))
        root.bind("<Control-Key-0>", lambda _event: self.set_font_size(12))
        root.bind("<space>", self._on_space)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if settings.auto_record and connection is not None:
            root.after(200, self.start_recording)
        root.after(REFRESH_MS, self._refresh)

    # 화면 구성

    def _build_top_bar(self) -> None:
        bar = tk.Frame(self._root, background=BACKGROUND, padx=12, pady=6)
        bar.pack(fill=tk.X)
        self._processor_label = self._status_label(bar, "처리부")
        self._console_label = self._status_label(bar, "운용통제기")
        self._simulator_label = self._status_label(bar, "모의기")
        self._mode_label = tk.Label(bar, text="실시간", font=FONT_TITLE, background=OK, foreground=BACKGROUND,
                                    padx=8)
        self._mode_label.pack(side=tk.LEFT, padx=(8, 8))
        self._time_label = tk.Label(bar, text="처리부 시각 -", background=BACKGROUND, foreground=TEXT,
                                    font=FONT_TITLE)
        self._time_label.pack(side=tk.LEFT, padx=8)

        buttons = tk.Frame(bar, background=BACKGROUND)
        buttons.pack(side=tk.RIGHT)
        ttk.Button(buttons, text="A−", width=3, command=lambda: self.change_font(-1)).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="A+", width=3, command=lambda: self.change_font(1)).pack(side=tk.RIGHT, padx=(8, 2))
        ttk.Button(buttons, text="열기", command=self.choose_folder).pack(side=tk.RIGHT, padx=2)
        ttk.Button(buttons, text="로그 가져오기", command=self.fetch_logs).pack(side=tk.RIGHT, padx=2)
        self._record_button = ttk.Button(buttons, text="● 녹화", command=self.toggle_recording)
        self._record_button.pack(side=tk.RIGHT, padx=2)
        if self._connection is None:
            self._record_button.configure(state=tk.DISABLED)
        self._record_label = tk.Label(buttons, text="", background=BACKGROUND, foreground=MUTED, font=FONT_SMALL)
        self._record_label.pack(side=tk.RIGHT, padx=6)

    def _status_label(self, parent: tk.Widget, name: str) -> tk.Label:
        label = tk.Label(parent, text=f"● {name}", background=BACKGROUND, foreground=UNKNOWN, font=FONT_TITLE)
        label.pack(side=tk.LEFT, padx=(0, 12))
        return label

    def _build_replay_bar(self) -> None:
        self._replay_bar = tk.Frame(self._root, background=PANEL, padx=12, pady=6)
        self._play_button = ttk.Button(self._replay_bar, text="▶ 재생", width=8, command=self._toggle_play)
        self._play_button.pack(side=tk.LEFT)
        ttk.Button(self._replay_bar, text="⏮", width=3, command=lambda: self._seek(0.0)).pack(side=tk.LEFT, padx=4)
        ttk.Button(self._replay_bar, text="−5s", width=4, command=lambda: self._skip(-5)).pack(side=tk.LEFT)
        ttk.Button(self._replay_bar, text="+5s", width=4, command=lambda: self._skip(5)).pack(side=tk.LEFT, padx=4)
        tk.Label(self._replay_bar, text="속도", background=PANEL, foreground=MUTED).pack(side=tk.LEFT, padx=(8, 2))
        self._speed_box = ttk.Combobox(self._replay_bar, state="readonly", width=6,
                                       values=[f"×{format_scale(speed)}" for speed in SPEEDS])
        self._speed_box.current(SPEEDS.index(1.0))
        self._speed_box.bind("<<ComboboxSelected>>", self._on_speed)
        self._speed_box.pack(side=tk.LEFT)
        self._position = tk.DoubleVar(value=0.0)
        self._slider = ttk.Scale(self._replay_bar, from_=0.0, to=1.0, variable=self._position,
                                 command=self._on_slider)
        self._slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12)
        self._slider.bind("<ButtonPress-1>", lambda _event: setattr(self, "_slider_dragging", True))
        self._slider.bind("<ButtonRelease-1>", self._on_slider_release)
        self._replay_label = tk.Label(self._replay_bar, text="", background=PANEL, foreground=TEXT, font=FONT_BODY)
        self._replay_label.pack(side=tk.LEFT)
        ttk.Button(self._replay_bar, text="실시간으로", command=self.show_live).pack(side=tk.LEFT, padx=(12, 0))

    # 상태

    @property
    def display_state(self) -> MonitorState:
        if self._replaying and self._session is not None and self._session.player is not None:
            return self._session.player.state
        return self._live

    def select_tab(self, key: str) -> None:
        self._notebook.select(self._tabs[key].frame)

    def _current_tab(self) -> str:
        selected = self._notebook.select()
        for key, tab in self._tabs.items():
            if str(tab.frame) == selected:
                return key
        return "beam"

    # 글자 크기

    def change_font(self, delta: int) -> None:
        self.set_font_size(self.fonts.size + delta)

    def set_font_size(self, size: int) -> None:
        from dataclasses import replace

        self.fonts.apply(size)
        self._settings = replace(self._settings, font_size=self.fonts.size)
        self._last_tab_refresh = {key: 0.0 for key in self._tabs}

    # 녹화

    def toggle_recording(self) -> None:
        if self._recorder is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        if self._connection is None or self._recorder is not None:
            return
        try:
            self._record_dir = new_session_directory(self._settings.record_path)
            recorder = Recorder(self._record_dir / RECORDING_NAME, self._connection.recording_meta())
        except OSError as error:
            messagebox.showerror("녹화", f"녹화 파일을 만들 수 없습니다:\n{error}", parent=self._root)
            return
        self._recorder = recorder
        self._connection.attach_recorder(recorder)
        self._record_button.configure(text="■ 녹화 중지")

    def stop_recording(self) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        if self._connection is not None:
            self._connection.attach_recorder(None)
        recorder.close()
        self._recorder = None
        self._record_button.configure(text="● 녹화")
        self._record_label.configure(text=f"저장됨: {recorder.path.parent}", foreground=MUTED)

    # 로그 가져오기

    def _fetch_destination(self) -> Path:
        if self._record_dir is not None:
            return self._record_dir / PROCESSOR_LOG_DIRECTORY
        if self._session is not None and self._session.path.is_dir():
            return self._session.path / PROCESSOR_LOG_DIRECTORY
        return new_session_directory(self._settings.record_path) / PROCESSOR_LOG_DIRECTORY

    def fetch_logs(self) -> None:
        try:
            destination = self._fetch_destination()
        except OSError as error:
            messagebox.showerror("로그 가져오기", f"저장 폴더를 만들 수 없습니다:\n{error}", parent=self._root)
            return
        self.fetch_dialog = FetchDialog(self._root, self._settings.fetch, destination, self._save_fetch_settings,
                                        lambda result: self.open_session(result.destination.parent))

    def _save_fetch_settings(self, fetch) -> None:
        from dataclasses import replace

        self._settings = replace(self._settings, fetch=fetch)
        try:
            save_settings(self._settings, self._settings_path)
        except OSError as error:
            messagebox.showwarning("설정 저장", f"monitor.env를 저장하지 못했습니다:\n{error}", parent=self._root)

    # 열기·재생

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self._root, title="세션 폴더 열기",
                                         initialdir=str(self._settings.record_path))
        if folder:
            self.open_session(Path(folder))

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(parent=self._root, title="녹화 또는 처리부 로그 열기",
                                          initialdir=str(self._settings.record_path),
                                          filetypes=(("모니터 녹화·처리부 로그·캡처",
                                                      "*.aesarec *.log *.csv *.pcap *.pcapng *.cap"),
                                                     ("모든 파일", "*")))
        if path:
            self.open_session(Path(path))

    def open_session(self, path: Path) -> None:
        if self._recorder is not None and Path(path).resolve() == self._recorder.path.parent.resolve():
            self._recorder.flush()
        self._root.configure(cursor="watch")
        self._root.update_idletasks()
        try:
            session = load_session(path, self._config.tracking if self._config else TrackingParameters())
        finally:
            self._root.configure(cursor="")
        self._session = session
        self._tabs["analysis"].set_session(session)
        if session.player is not None:
            self._slider.configure(to=max(session.player.duration_s, 0.001))
            # 녹화 첫 빔까지 넣어 둬야 처리부 시각과 연결 상태가 바로 보인다.
            session.player.seek(session.player.first_beam_s())
            self._replaying = True
            self._replay_bar.pack(fill=tk.X, after=self._root.pack_slaves()[0])
        else:
            self.show_live()
        self.select_tab("analysis")

    def select_run(self, index: int) -> None:
        if self._session is None or index < 0:
            return
        self._session.select_run(index)
        self._tabs["analysis"].set_session(self._session)

    def show_live(self) -> None:
        if self._session is not None and self._session.player is not None:
            self._session.player.pause()
        self._replaying = False
        self._replay_bar.pack_forget()

    def _toggle_play(self) -> None:
        if self._session is None or self._session.player is None:
            return
        if not self._replaying:
            self._replaying = True
            self._replay_bar.pack(fill=tk.X, after=self._root.pack_slaves()[0])
        self._session.player.toggle()

    def _on_space(self, event: tk.Event) -> None:
        if self._replaying and not isinstance(event.widget, (tk.Entry, ttk.Entry)):
            self._toggle_play()

    def _on_speed(self, _event: object) -> None:
        if self._session is not None and self._session.player is not None:
            self._session.player.speed = SPEEDS[self._speed_box.current()]

    def _seek(self, position: float) -> None:
        if self._session is not None and self._session.player is not None:
            self._session.player.seek(position)
            self._last_tab_refresh = {key: 0.0 for key in self._tabs}

    def _skip(self, seconds: float) -> None:
        if self._session is not None and self._session.player is not None:
            self._seek(self._session.player.position_s + seconds)

    def _on_slider(self, _value: str) -> None:
        if self._slider_dragging:
            self._seek(self._position.get())

    def _on_slider_release(self, _event: object) -> None:
        self._slider_dragging = False
        self._seek(self._position.get())

    def focus_event(self, event: LogEvent) -> None:
        """분석 탭에서 고른 이벤트의 1초 전으로 재생 위치를 옮긴다."""
        player = self._session.player if self._session else None
        if player is None or event.processor_ms is None:
            return
        position = player.time_for_processor_ms(int(event.processor_ms) - 1000)
        if position is None:
            return
        player.pause()
        self._replaying = True
        self._replay_bar.pack(fill=tk.X, after=self._root.pack_slaves()[0])
        self._seek(position)
        if event.category == CATEGORY_REJECTED and event.details.get("requestId", "").isdigit():
            self._tabs["track"]._on_track_selected(event.details["requestId"])

    # 갱신

    def _refresh(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_frame
        self._last_frame = now
        self._drain_events()
        if self._replaying and self._session is not None and self._session.player is not None:
            self._session.player.advance(elapsed)
        state = self.display_state
        self._draw_top_bar(state)
        self._draw_replay_bar()
        key = self._current_tab()
        if now - self._last_tab_refresh[key] >= TAB_REFRESH_SECONDS[key]:
            self._last_tab_refresh[key] = now
            tab = self._tabs[key]
            tab.refresh(state if key != "analysis" else (state if self._replaying else None))
        self._root.after(REFRESH_MS, self._refresh)

    def _drain_events(self) -> None:
        for _ in range(MAX_EVENTS_PER_REFRESH):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            self._live.apply(event)

    def _draw_top_bar(self, state: MonitorState) -> None:
        if self._replaying:
            self._mode_label.configure(text="재생", background=WARN)
        else:
            live_text = "캡처" if state.source == SOURCE_CAPTURE else "실시간"
            self._mode_label.configure(text=live_text if self._connection else "오프라인",
                                       background=OK if self._connection else UNKNOWN)
        self._set_status(self._processor_label, "처리부", state.connected,
                         "" if state.connected else state.connection_detail)
        status = state.fresh_status()
        self._set_status(self._console_label, "운용통제기", state.peer_connected(PEER_CONSOLE))
        self._set_status(self._simulator_label, "모의기", state.peer_connected(PEER_SIMULATOR))
        speed = ""
        if status is not None:
            speed = "  정지" if status.paused else f"  ×{format_scale(status.time_scale)}"
        elif state.paused():
            speed = "  운용 OFF"
        self._time_label.configure(text=f"처리부 시각 {format_time(state.now_ms())}{speed}  ·  "
                                        f"트랙 {len(state.tracks)}")
        if self._recorder is not None:
            self._record_label.configure(
                text=f"녹화 중 {format_duration(self._recorder.elapsed)} · {format_bytes(self._recorder.bytes_written)}",
                foreground=BAD)

    def _draw_replay_bar(self) -> None:
        if not self._replaying or self._session is None or self._session.player is None:
            return
        player = self._session.player
        if not self._slider_dragging:
            self._position.set(player.position_s)
        self._play_button.configure(text="⏸ 정지" if player.playing else "▶ 재생")
        self._replay_label.configure(
            text=f"{format_time(player.position_s * 1000)} / {format_time(player.duration_s * 1000)}"
                 f"  (처리부 {format_time(player.state.now_ms())})")

    @staticmethod
    def _set_status(label: tk.Label, name: str, connected: Optional[bool], detail: str = "") -> None:
        if connected is None:
            label.configure(text=f"● {name} ?", foreground=UNKNOWN)
        elif connected:
            label.configure(text=f"● {name}", foreground=OK)
        else:
            label.configure(text=f"● {name} 끊김", foreground=BAD)

    # 종료

    def close(self) -> None:
        self.stop_recording()
        if self._settings.font_size != self._initial_font_size:
            # 바꾼 글자 크기는 다음 실행에도 쓰도록 저장한다.
            try:
                save_settings(self._settings, self._settings_path)
            except OSError:
                pass
        self._root.destroy()

    def schedule_exit(self, seconds: float, screenshot: Optional[Path]) -> None:
        def finish() -> None:
            if screenshot is not None:
                self._root.update()
                subprocess.run(["import", "-window", "root", str(screenshot)], check=False)
            self.close()

        self._root.after(int(seconds * 1000), finish)
