"""메인 창: 상단 바(연결·실시간/재생·처리부 시각·녹화·메뉴), 재생 바, 탭 4개.

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
from aesa_monitor.core.problems import LINK_EVENTS, ProblemDetector, level_counts
from aesa_monitor.core.settings import MonitorSettings, save_settings
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.view_clock import ViewClock
from aesa_monitor.io.processor_log import CATEGORY_REJECTED, LogEvent
from aesa_monitor.io.recording import (
    PROCESSOR_LOG_DIRECTORY, RECORDING_NAME, Recorder, new_session_directory,
)
from aesa_monitor.io.replay import SPEEDS, ReplayPosition
from aesa_monitor.io.session import Session, load_session
from aesa_monitor.ui.analysis_tab import AnalysisTab
from aesa_monitor.ui.beam_tab import BeamTab
from aesa_monitor.ui.comm_tab import CommTab
from aesa_monitor.ui.fetch_dialog import FetchDialog
from aesa_monitor.ui.style import (
    ACCENT, ACCENT_SOFT, BACKGROUND, BAD, BORDER, DEFAULT_FONT_SIZE, FONT_BODY, FONT_TITLE, Fonts, MUTED, OK, PANEL, TEXT,
    UNKNOWN, WARN, apply_theme, format_bytes, format_duration, format_scale, format_time,
)
from aesa_monitor.ui.track_tab import TrackTab
from aesa_monitor.ui.widgets import Tooltip
from aesa_monitor.ui.window import initial_window_size

REFRESH_MS = 50
# 실시간 문제 감지 간격(초). 녹화를 다시 계산할 때(core/problems.SCAN_STEP_S)와 같다.
PROBLEM_UPDATE_SECONDS = 0.25
MAX_EVENTS_PER_REFRESH = 20000
TAB_REFRESH_SECONDS = {"beam": 0.05, "track": 0.2, "comm": 0.25, "analysis": 0.25}
TAB_TITLES = {"beam": " 빔 ", "track": " 추적 ", "comm": " 통신 ", "analysis": " 분석 "}
# 상단 바 연결 상태: 키 -> (툴팁에 쓰는 이름, 바에 쓰는 짧은 이름)
CONNECTION_NAMES = {"processor": ("처리부", "처리부"), "console": ("운용통제기", "통제"),
                    "simulator": ("모의기", "모의")}
# 상단 바에서 값이 바뀌는 글자의 흔한 최대 길이. 처음 폭을 이것으로 잡아 값이 바뀌어도 옆 위젯이 흔들리지 않게 하고,
# 더 긴 값(처리부 시각 100분 이상, 녹화 1 GB 이상 등)이 오면 그때 늘린다(줄이지는 않음, _set_label_text).
TIME_SAMPLES = ("88:88.888",)
TIME_DETAIL_SAMPLES = ("×0.25 · 트랙 99", "정지 · 트랙 99", "운용 OFF · 트랙 99")
RECORD_SAMPLES = ("● 녹화 59분 59초 · 999.9 MB", "녹화 꺼짐")
# 상단 바 묶음 사이 최소 간격(px)과 시각 세부 글자 왼쪽 간격(px)
TOP_BAR_GAP = 24
TIME_DETAIL_PAD = 8
# 재생 위치 막대의 한 줄 최소 길이와 좌우 간격(px). 이보다 좁으면 막대를 둘째 줄로 내린다.
REPLAY_SLIDER_MIN = 160
REPLAY_SLIDER_PAD = 12


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
        # 캡처가 메시지를 몰아서 넘겨도 실시간 화면이 고르게 움직이도록 표시 시각을 따로 흘린다.
        self._view_clock = ViewClock()
        self._session: Optional[Session] = None
        self._replaying = False
        self._recorder: Optional[Recorder] = None
        self._record_dir: Optional[Path] = None
        self._last_frame = time.monotonic()
        self._slider_dragging = False
        parameters = config.tracking if config else TrackingParameters()
        # 실시간 문제 자동 기록. 녹화를 열면 녹화에서 다시 계산한 결과(session.problems())를 쓴다.
        self._live_problems = ProblemDetector(parameters)
        self._last_problem_update = 0.0
        self._problem_tab_title = ""
        server_text = f"{config.server[0]}:{config.server[1]}" if config else "접속 안 함"
        self._server_text = server_text

        root.title("AESA 데이터 처리부 모니터")
        root.configure(background=BACKGROUND)
        self._apply_initial_size()
        apply_theme(root)
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
            "analysis": AnalysisTab(self._notebook, self.focus_event, self.select_run, self.focus_time,
                                    self.current_position,
                                    lambda: self._live_problems.problems),
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
        root.bind("<KeyPress-b>", self._on_bookmark_key)
        root.bind("<KeyPress-B>", self._on_bookmark_key)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if settings.auto_record and connection is not None:
            root.after(200, self.start_recording)
        root.after(REFRESH_MS, self._refresh)

    # 화면 구성

    def _apply_initial_size(self) -> None:
        """화면에 맞춰 시작 창 크기를 정한다. 1680×1000이 들어가면 그 크기로 가운데에, 아니면 최대화(1920×1080 이하).

        글자 16에서도 빔 탭 요청 빔 일정 4행과 프레임별 빔 수 막대가 처음부터 보이게 하려는 것이다.
        """
        root = self._root
        size = initial_window_size(root.winfo_screenwidth(), root.winfo_screenheight())
        root.minsize(size.min_width, size.min_height)
        root.geometry(size.geometry)
        if size.maximize:
            self._set_maximized(True)

    def _set_maximized(self, maximized: bool) -> None:
        """창 최대화. Windows·macOS는 state('zoomed'), X11(리눅스)은 -zoomed 속성을 쓴다."""
        root = self._root
        if maximized:
            try:
                root.state("zoomed")
                return
            except tk.TclError:
                pass
            try:
                root.attributes("-zoomed", True)
            except tk.TclError:
                pass  # 창 관리자가 최대화를 지원하지 않으면 계산한 크기 그대로 둔다
            return
        # 풀 때는 두 방식 모두 되돌린다(X11은 state('normal')만으로 -zoomed가 풀리지 않을 수 있음).
        for undo in (lambda: root.attributes("-zoomed", False), lambda: root.state("normal")):
            try:
                undo()
            except tk.TclError:
                pass

    def set_window_geometry(self, geometry: str) -> None:
        """명령줄 --geometry: 최대화를 풀고 주어진 크기로 둔다."""
        self._set_maximized(False)
        self._root.geometry(geometry)

    def _build_top_bar(self) -> None:
        """상단 바: 왼쪽 정보 묶음(연결 상태, 실시간/재생, 처리부 시각), 오른쪽 조작 묶음(녹화 상태, 녹화, 메뉴).

        두 묶음을 grid 두 칸에 두어 서로 밀어 자르지 않게 하고, 너비가 모자라면 _layout_top_bar가 단계적으로 줄인다.
        로그 가져오기·열기·글자 크기는 '메뉴'에 모았다(분석 탭에도 버튼이 있고, 글자 크기는 Ctrl +/−/0).
        """
        # 창 맨 위 CI 파랑 띠. 상태 표시(초록·빨강 글자)는 흰 바탕에 그대로 두고 띠로만 브랜드 색을 보인다.
        tk.Frame(self._root, background=ACCENT, height=4).pack(fill=tk.X)
        bar = tk.Frame(self._root, background=BACKGROUND, padx=12, pady=6)
        bar.pack(fill=tk.X)
        bar.columnconfigure(1, weight=1)
        self._top_bar = bar
        self._top_level = -1
        self._info = tk.Frame(bar, background=BACKGROUND)
        self._controls = tk.Frame(bar, background=BACKGROUND)

        # 1. 연결 상태: 색 점 + 짧은 이름. 끊긴 이유 같은 자세한 내용은 툴팁.
        self._connection_items = {}
        self._connection_tips = {}
        for key, (name, short) in CONNECTION_NAMES.items():
            item = tk.Frame(self._info, background=BACKGROUND)
            item.pack(side=tk.LEFT, padx=(0, 10))
            dot = tk.Label(item, text="●", font=FONT_TITLE, background=BACKGROUND, foreground=UNKNOWN)
            dot.pack(side=tk.LEFT)
            label = tk.Label(item, text=short, font=FONT_TITLE, background=BACKGROUND, foreground=UNKNOWN)
            label.pack(side=tk.LEFT, padx=(2, 0))
            self._connection_items[key] = (dot, label)
            self._connection_tips[key] = f"{name}: 상태 모름"
            Tooltip(item, lambda key=key: self._connection_tips[key])
        tk.Frame(self._info, background=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, padx=(2, 12), pady=2)

        # 2. 실시간/재생/캡처/오프라인
        self._mode_label = tk.Label(self._info, text="실시간", font=FONT_TITLE, background=OK, foreground=BACKGROUND,
                                    padx=8)
        self._mode_label.pack(side=tk.LEFT, padx=(0, 12))

        # 3. 처리부 시각. 배율·트랙 수(세부)는 따로 두어 좁으면 숨긴다(툴팁에는 늘 있다).
        time_frame = tk.Frame(self._info, background=BACKGROUND)
        time_frame.pack(side=tk.LEFT)
        tk.Label(time_frame, text="시각", font=FONT_BODY, background=BACKGROUND,
                 foreground=MUTED).pack(side=tk.LEFT, padx=(0, 4))
        self._time_label = tk.Label(time_frame, text=format_time(None), font=FONT_TITLE, background=BACKGROUND,
                                    foreground=TEXT, anchor="w")
        self._time_label.pack(side=tk.LEFT)
        self._time_detail = tk.Label(time_frame, text="", font=FONT_BODY, background=BACKGROUND, foreground=MUTED,
                                     anchor="w")
        self._time_detail.pack(side=tk.LEFT, padx=(TIME_DETAIL_PAD, 0))
        self._time_tip = ""
        Tooltip(time_frame, lambda: self._time_tip)

        # 4. 녹화 상태(녹화 중·녹화 꺼짐·저장됨). 저장 경로는 툴팁.
        self._record_label = tk.Label(self._controls, text="", font=FONT_BODY, background=BACKGROUND,
                                      foreground=MUTED, anchor="e")
        self._record_label.pack(side=tk.LEFT, padx=(0, 10))
        self._record_tip = ""
        Tooltip(self._record_label, lambda: self._record_tip)

        # 5. 버튼: 녹화, 메뉴. ttk(clam) 버튼은 기본 최소 폭이 11자라 글자에 맞춰 폭을 준다.
        self._record_button = ttk.Button(self._controls, text="● 녹화", width=-6, command=self.toggle_recording)
        self._record_button.pack(side=tk.LEFT, padx=2)
        if self._connection is None:
            self._record_button.configure(state=tk.DISABLED)
        menu_button = ttk.Menubutton(self._controls, text="메뉴", width=-4, direction="below")
        menu_button.pack(side=tk.LEFT, padx=(2, 0))
        menu = tk.Menu(menu_button, tearoff=False, font=FONT_BODY, background=BACKGROUND, foreground=TEXT,
                       activebackground=ACCENT_SOFT, activeforeground=TEXT, disabledforeground=MUTED)
        menu.add_command(label="폴더 열기…", command=self.choose_folder)
        menu.add_command(label="파일 열기…", command=self.choose_file)
        menu.add_command(label="로그 가져오기…", command=self.fetch_logs)
        menu.add_separator()
        menu.add_command(label="", state=tk.DISABLED)
        self._font_menu_index = menu.index(tk.END)
        menu.add_command(label="글자 크게", accelerator="Ctrl +", command=lambda: self.change_font(1))
        menu.add_command(label="글자 작게", accelerator="Ctrl −", command=lambda: self.change_font(-1))
        menu.add_command(label=f"기본 크기 ({DEFAULT_FONT_SIZE})", accelerator="Ctrl 0",
                         command=lambda: self.set_font_size(DEFAULT_FONT_SIZE))
        menu_button.configure(menu=menu)
        self._menu = menu

        self._fit_top_bar_text()
        bar.bind("<Configure>", lambda _event: self._layout_top_bar())

    def _fit_top_bar_text(self) -> None:
        """바뀌는 글자(처리부 시각, 배율·트랙 수, 녹화 상태)의 폭을 가장 긴 경우로 고정한다. 글자 크기를 바꾸면 다시 부른다.

        폭이 값마다 달라지면 옆 위젯이 흔들리고, 너비 판단(_layout_top_bar)이 한 줄과 두 줄 사이를 오갈 수 있다.
        """
        for label, font, samples in ((self._time_label, FONT_TITLE, TIME_SAMPLES),
                                     (self._time_detail, FONT_BODY, TIME_DETAIL_SAMPLES),
                                     (self._record_label, FONT_BODY, RECORD_SAMPLES)):
            label.configure(width=max(self._text_units(sample, font) for sample in samples + (label.cget("text"),)))
        self._menu.entryconfigure(self._font_menu_index, label=f"글자 크기 {self.fonts.size}")

    def _text_units(self, text: str, font: str) -> int:
        """글자가 들어갈 Label width(글자 수 단위, '0' 폭 기준, 올림)."""
        return -(-self.fonts.measure(text, font) // max(self.fonts.measure("0", font), 1))

    def _set_label_text(self, label: tk.Label, text: str, font: str, foreground: Optional[str] = None) -> None:
        """글자(와 색)가 바뀌었을 때만 고친다(매 갱신 50 ms마다 같은 값으로 다시 그리지 않게).

        폭을 고정한 Label이라 더 긴 글자가 오면 잘리므로 그때만 폭을 늘린다. 줄이지 않아 한 줄·두 줄이 오가지 않는다.
        """
        if label.cget("text") == text and (foreground is None or label.cget("foreground") == foreground):
            return
        options = {"text": text}
        if foreground is not None:
            options["foreground"] = foreground
        units = self._text_units(text, font)
        if units > int(label.cget("width")):
            options["width"] = units
        label.configure(**options)

    def _layout_top_bar(self) -> None:
        """상단 바를 너비에 맞춘다. 0단계 한 줄 → 1단계 조작 묶음을 둘째 줄로 → 2단계 시각 세부를 숨김(툴팁에 남음).

        각 묶음이 원하는 너비(winfo_reqwidth)로 판단한다. 우선순위(연결 > 실시간/재생 > 처리부 시각 > 녹화 상태 > 버튼)대로
        아래쪽 묶음부터 자리를 양보하되, 녹화 버튼은 숨기지 않고 둘째 줄로 내린다. 단계가 같으면 아무것도 바꾸지 않는다.
        """
        bar = self._top_bar
        width = bar.winfo_width() - 2 * int(str(bar.cget("padx")))
        if width <= 1:
            return
        detail = 0 if self._time_detail.winfo_manager() else self._time_detail.winfo_reqwidth() + TIME_DETAIL_PAD
        info = self._info.winfo_reqwidth() + detail
        if info + TOP_BAR_GAP + self._controls.winfo_reqwidth() <= width:
            level = 0
        elif info <= width:
            level = 1
        else:
            level = 2
        if level == self._top_level:
            return
        self._top_level = level
        # grid()는 이미 배치된 위젯의 이전 옵션(columnspan, pady)을 이어받으므로 지우고 다시 놓는다.
        self._controls.grid_forget()
        self._info.grid(row=0, column=0, sticky="w")
        if level == 0:
            self._controls.grid(row=0, column=2, sticky="e")
        else:
            self._controls.grid(row=1, column=0, columnspan=3, sticky="e", pady=(6, 0))
        if level == 2:
            self._time_detail.pack_forget()
        elif not self._time_detail.winfo_manager():
            self._time_detail.pack(side=tk.LEFT, padx=(TIME_DETAIL_PAD, 0))

    def _build_replay_bar(self) -> None:
        """재생 바: 왼쪽 조작(재생·처음·±5초·속도), 가운데 재생 위치 막대, 오른쪽 녹화 위치·'실시간으로'.

        좁으면 _layout_replay_bar가 막대를 둘째 줄로 내린다(막대가 버튼을 밀어 자르지 않게).
        """
        bar = tk.Frame(self._root, background=PANEL, padx=12, pady=6)
        self._replay_bar = bar
        self._replay_level = -1
        self._replay_controls = tk.Frame(bar, background=PANEL)
        self._play_button = ttk.Button(self._replay_controls, text="▶ 재생", width=8, command=self._toggle_play)
        self._play_button.pack(side=tk.LEFT)
        ttk.Button(self._replay_controls, text="⏮", width=3,
                   command=lambda: self._seek(0.0)).pack(side=tk.LEFT, padx=4)
        ttk.Button(self._replay_controls, text="−5s", width=4, command=lambda: self._skip(-5)).pack(side=tk.LEFT)
        ttk.Button(self._replay_controls, text="+5s", width=4,
                   command=lambda: self._skip(5)).pack(side=tk.LEFT, padx=4)
        tk.Label(self._replay_controls, text="속도", background=PANEL,
                 foreground=MUTED).pack(side=tk.LEFT, padx=(8, 2))
        self._speed_box = ttk.Combobox(self._replay_controls, state="readonly", width=6,
                                       values=[f"×{format_scale(speed)}" for speed in SPEEDS])
        self._speed_box.current(SPEEDS.index(1.0))
        self._speed_box.bind("<<ComboboxSelected>>", self._on_speed)
        self._speed_box.pack(side=tk.LEFT)
        self._position = tk.DoubleVar(value=0.0)
        self._slider = ttk.Scale(bar, from_=0.0, to=1.0, variable=self._position, command=self._on_slider)
        self._slider.bind("<ButtonPress-1>", lambda _event: setattr(self, "_slider_dragging", True))
        self._slider.bind("<ButtonRelease-1>", self._on_slider_release)
        self._replay_right = tk.Frame(bar, background=PANEL)
        self._replay_label = tk.Label(self._replay_right, text="", background=PANEL, foreground=TEXT, font=FONT_BODY)
        self._replay_label.pack(side=tk.LEFT)
        ttk.Button(self._replay_right, text="실시간으로", command=self.show_live).pack(side=tk.LEFT, padx=(12, 0))
        bar.bind("<Configure>", lambda _event: self._layout_replay_bar())

    def _layout_replay_bar(self) -> None:
        """재생 바를 너비에 맞춘다. 0단계 한 줄 → 1단계 막대만 둘째 줄 → 2단계 막대와 녹화 위치·'실시간으로'를 둘째 줄."""
        bar = self._replay_bar
        width = bar.winfo_width() - 2 * int(str(bar.cget("padx")))
        if width <= 1:
            return
        controls = self._replay_controls.winfo_reqwidth()
        right = self._replay_right.winfo_reqwidth()
        if controls + right + 2 * REPLAY_SLIDER_PAD + REPLAY_SLIDER_MIN <= width:
            level = 0
        elif controls + right + TOP_BAR_GAP <= width:
            level = 1
        else:
            level = 2
        if level == self._replay_level:
            return
        self._replay_level = level
        for widget in (self._replay_controls, self._slider, self._replay_right):
            widget.grid_forget()
        for column in range(3):
            bar.columnconfigure(column, weight=0)
        # 조작 묶음은 첫 줄 전체를 쓴다(2단계에서 둘째 줄 오른쪽 칸 폭만큼 좁아지지 않게).
        self._replay_controls.grid(row=0, column=0, columnspan=3 if level == 2 else 1, sticky="w")
        if level == 0:
            bar.columnconfigure(1, weight=1)
            self._slider.grid(row=0, column=1, sticky="ew", padx=REPLAY_SLIDER_PAD)
            self._replay_right.grid(row=0, column=2, sticky="e")
        elif level == 1:
            bar.columnconfigure(1, weight=1)
            self._replay_right.grid(row=0, column=2, sticky="e")
            self._slider.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        else:
            bar.columnconfigure(0, weight=1)
            self._slider.grid(row=1, column=0, sticky="ew", padx=(0, REPLAY_SLIDER_PAD), pady=(6, 0))
            self._replay_right.grid(row=1, column=1, columnspan=2, sticky="e", pady=(6, 0))

    def _show_replay_bar(self) -> None:
        """재생 바를 상단 바 아래(탭 바로 위)에 보인다. 처리부 시각은 상단 바에만 쓴다."""
        self._replay_bar.pack(fill=tk.X, after=self._top_bar)

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
        self._fit_top_bar_text()
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
        self._record_button.configure(text="■ 중지")
        self._record_tip = f"녹화 중: {self._record_dir}"

    def stop_recording(self) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        if self._connection is not None:
            self._connection.attach_recorder(None)
        recorder.close()
        self._recorder = None
        self._record_button.configure(text="● 녹화")
        self._record_label.configure(text="저장됨", foreground=MUTED)
        self._record_tip = f"저장됨: {recorder.path.parent}"

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
            self._show_replay_bar()
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
            self._show_replay_bar()
        self._session.player.toggle()

    def _on_space(self, event: tk.Event) -> None:
        if self._replaying and not isinstance(event.widget, (tk.Entry, ttk.Entry)):
            self._toggle_play()

    def current_position(self) -> Optional[ReplayPosition]:
        """지금 재생 위치. 재생 중이 아니면(실시간) None이다. 어느 탭이 보이든 같은 값을 준다."""
        if self._replaying and self._session is not None and self._session.player is not None:
            return self._session.player.current_position()
        return None

    def _on_bookmark_key(self, event: tk.Event) -> None:
        """B 키: 세션을 열었으면 어느 탭에서든 지금 재생 위치로 북마크 추가 창을 연다. 글자 입력 칸에서는 무시한다."""
        if self._session is None or isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox)):
            return
        if event.widget.winfo_toplevel() is not self._root:
            return
        if self._session.player is not None:
            self._session.player.pause()
        self._tabs["analysis"].add_bookmark()

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
        if event.processor_ms is None:
            return
        track_id = None
        if event.category == CATEGORY_REJECTED and event.details.get("requestId", "").isdigit():
            track_id = int(event.details["requestId"])
        self.focus_time(int(event.processor_ms) - 1000, track_id)

    def focus_time(self, processor_ms: Optional[int], track_id: Optional[int] = None,
                   recording_s: Optional[float] = None, segment: Optional[int] = None) -> None:
        """재생 위치를 옮기고 멈춘다. track_id를 주면 추적 탭에서 그 트랙을 고른다.

        recording_s(녹화 위치)가 있으면 그곳으로 간다. 없으면 처리부 시각으로 찾는데, 처리부가 녹화 중 다시
        시작해 같은 처리부 시각이 여러 번 나오므로 segment(처리부 실행 구간)를 주면 그 구간 안에서 찾는다.
        """
        player = self._session.player if self._session else None
        if player is None:
            return
        if recording_s is not None:
            position: Optional[float] = min(max(recording_s, 0.0), player.duration_s)
        elif processor_ms is not None:
            position = player.time_for_processor_ms(int(processor_ms), segment)
        else:
            position = None
        if position is None:
            return
        player.pause()
        self._replaying = True
        self._show_replay_bar()
        self._seek(position)
        if track_id is not None:
            self._tabs["track"]._on_track_selected(str(track_id))

    # 갱신

    def _refresh(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_frame
        self._last_frame = now
        self._drain_events()
        self._live.view_limit_ms = self._view_clock.update(self._live.latest_ms(), now, self._live.time_scale())
        if self._replaying and self._session is not None and self._session.player is not None:
            self._session.player.advance(elapsed)
        if self._connection is not None and now - self._last_problem_update >= PROBLEM_UPDATE_SECONDS:
            self._last_problem_update = now
            self._live_problems.update(self._live)
        state = self.display_state
        self._draw_top_bar(state)
        self._draw_problem_label()
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
            if isinstance(event, LINK_EVENTS):
                # 연결 변화는 감지 간격(0.25 s)보다 짧게 끊겼다 이어질 수 있어 사건마다 넘긴다.
                self._live_problems.observe(event, self._live)

    def _draw_top_bar(self, state: MonitorState) -> None:
        if self._replaying:
            self._mode_label.configure(text="재생", background=WARN)
        else:
            live_text = "캡처" if state.source == SOURCE_CAPTURE else "실시간"
            self._mode_label.configure(text=live_text if self._connection else "오프라인",
                                       background=OK if self._connection else UNKNOWN)
        # 재생 중에는 녹화 당시 연결 상태를 보인다(화면의 다른 값과 같은 시점).
        processor_detail = self._server_text if state.connected else state.connection_detail
        self._set_status("processor", state.connected, processor_detail)
        self._set_status("console", state.peer_connected(PEER_CONSOLE))
        self._set_status("simulator", state.peer_connected(PEER_SIMULATOR))

        status = state.fresh_status()
        speed = ""
        if status is not None:
            speed = "정지" if status.paused else f"×{format_scale(status.time_scale)}"
        elif state.paused():
            speed = "운용 OFF"
        now = format_time(state.now_ms())
        tracks = f"트랙 {len(state.tracks)}"
        detail = f"{speed} · {tracks}" if speed else tracks
        self._set_label_text(self._time_label, now, FONT_TITLE)
        self._set_label_text(self._time_detail, detail, FONT_BODY)
        self._time_tip = f"처리부 시각 {now}{' (재생 위치)' if self._replaying else ''}\n{detail}"
        if self._recorder is not None:
            self._set_label_text(
                self._record_label,
                f"● 녹화 {format_duration(self._recorder.elapsed)} · {format_bytes(self._recorder.bytes_written)}",
                FONT_BODY, BAD)
        self._layout_top_bar()

    def _draw_problem_label(self) -> None:
        """자동 기록한 문제 수를 분석 탭 제목에 쓴다(탭 제목은 상단 바와 너비를 다투지 않고 어느 탭에서든 보인다).

        재생 중이면 녹화 전체, 실시간이면 모니터를 켠 뒤 문제 수다. 실시간인데 녹화가 꺼져 있으면 녹화 버튼 옆에
        '녹화 꺼짐'을 띄운다(녹화가 없으면 나중에 그 위치로 돌아가 볼 수 없음).
        """
        if self._replaying and self._session is not None:
            counts = level_counts(self._session.problems())
            warn_record = False
        else:
            counts = level_counts(self._live_problems.problems)
            warn_record = self._recorder is None and self._connection is not None
        total = counts["warn"] + counts["bad"]
        title = f"{TAB_TITLES['analysis'].rstrip()} ⚑ {total} " if total else TAB_TITLES["analysis"]
        if self._problem_tab_title != title:
            self._problem_tab_title = title
            self._notebook.tab(self._tabs["analysis"].frame, text=title)
        current = self._record_label.cget("text")
        if total and warn_record and not current.startswith("저장됨"):
            if current != "녹화 꺼짐":
                self._record_label.configure(text="녹화 꺼짐", foreground=WARN)
                self._record_tip = "녹화가 꺼져 있다. 녹화하지 않으면 문제가 난 위치로 돌아가 볼 수 없다."
        elif current == "녹화 꺼짐":
            self._record_label.configure(text="")
            self._record_tip = ""

    def _draw_replay_bar(self) -> None:
        if not self._replaying or self._session is None or self._session.player is None:
            return
        self._layout_replay_bar()
        player = self._session.player
        if not self._slider_dragging:
            self._position.set(player.position_s)
        self._play_button.configure(text="⏸ 정지" if player.playing else "▶ 재생")
        self._replay_label.configure(
            text=f"{format_time(player.position_s * 1000)} / {format_time(player.duration_s * 1000)}")

    def _set_status(self, key: str, connected: Optional[bool], detail: str = "") -> None:
        """연결 상태 점·이름 색과 툴팁. 연결됨 초록 점, 끊김 빨강 점·이름, 모름 회색."""
        name, _short = CONNECTION_NAMES[key]
        dot, label = self._connection_items[key]
        if connected is None:
            dot_color, text_color, tip = UNKNOWN, UNKNOWN, f"{name}: 상태 모름"
        elif connected:
            dot_color, text_color, tip = OK, TEXT, f"{name}: 연결됨"
        else:
            dot_color, text_color, tip = BAD, BAD, f"{name}: 끊김"
        if detail:
            tip += f" · {detail}"
        if self._replaying:
            tip += "\n(재생 중: 녹화 당시 상태)"
        self._connection_tips[key] = tip
        if dot.cget("foreground") != dot_color:
            dot.configure(foreground=dot_color)
        if label.cget("foreground") != text_color:
            label.configure(foreground=text_color)

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
