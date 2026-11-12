"""[로그 가져오기] 창. 설정을 확인·수정하고 io/log_fetch.py를 백그라운드에서 실행한다."""

from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Optional

from aesa_monitor.core.settings import FetchSettings
from aesa_monitor.io.log_fetch import FetchError, FetchResult, fetch_logs
from aesa_monitor.ui.style import BACKGROUND, BAD, FONT_BODY, FONT_SMALL, FONT_TITLE, MUTED, OK, TEXT


class FetchDialog:
    def __init__(self, parent: tk.Misc, settings: FetchSettings, destination: Path,
                 on_saved: Callable[[FetchSettings], None],
                 on_done: Callable[[FetchResult], None]) -> None:
        self._on_saved = on_saved
        self._on_done = on_done
        self._destination = destination
        self._result: Optional[FetchResult] = None
        self.window = tk.Toplevel(parent)
        self.window.title("처리부 로그 가져오기")
        self.window.configure(background=BACKGROUND, padx=16, pady=12)
        self.window.transient(parent.winfo_toplevel())

        self._source = tk.StringVar(value=settings.source)
        self._local_dir = tk.StringVar(value=settings.local_dir)
        self._target = tk.StringVar(value=settings.ssh_target)
        self._port = tk.StringVar(value=str(settings.ssh_port))
        self._remote_dir = tk.StringVar(value=settings.remote_dir)
        self._options = tk.StringVar(value=settings.ssh_options)
        self._save = tk.BooleanVar(value=True)
        self._settings = settings

        tk.Label(self.window, text="처리부가 남긴 adp_*.log(메시지 로그)와 adp_detection_*.csv(탐지 CSV)를 가져옵니다.",
                 font=FONT_BODY, background=BACKGROUND, foreground=TEXT, anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w")
        row = 1
        ttk.Radiobutton(self.window, text="이 PC (처리부를 이 PC에서 실행)", value="local", variable=self._source,
                        command=self._update_state).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 2))
        row += 1
        tk.Label(self.window, text="로그 폴더", background=BACKGROUND).grid(row=row, column=0, sticky="e", padx=6)
        self._local_entry = ttk.Entry(self.window, textvariable=self._local_dir, width=52)
        self._local_entry.grid(row=row, column=1, sticky="we")
        self._browse = ttk.Button(self.window, text="찾기", command=self._choose_folder)
        self._browse.grid(row=row, column=2, padx=4)
        row += 1
        ttk.Radiobutton(self.window, text="보드 (SSH, scp)", value="ssh", variable=self._source,
                        command=self._update_state).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 2))
        self._ssh_entries = []
        for label, variable in (("사용자@주소", self._target), ("포트", self._port),
                                ("보드 로그 폴더", self._remote_dir), ("ssh 옵션", self._options)):
            row += 1
            tk.Label(self.window, text=label, background=BACKGROUND).grid(row=row, column=0, sticky="e", padx=6)
            entry = ttk.Entry(self.window, textvariable=variable, width=52)
            entry.grid(row=row, column=1, columnspan=2, sticky="we", pady=1)
            self._ssh_entries.append(entry)
        row += 1
        tk.Label(self.window, text="보드에 SSH 공개키가 등록되어 있어야 합니다(버튼에서는 비밀번호를 입력할 수 없음).",
                 font=FONT_SMALL, background=BACKGROUND, foreground=MUTED).grid(row=row, column=1, columnspan=2,
                                                                                 sticky="w")
        row += 1
        tk.Label(self.window, text=f"저장 위치: {destination}", font=FONT_SMALL, background=BACKGROUND,
                 foreground=MUTED, anchor="w").grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row += 1
        ttk.Checkbutton(self.window, text="이 설정을 monitor.env에 저장", variable=self._save).grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1
        self._status = tk.Label(self.window, text="", font=FONT_TITLE, background=BACKGROUND, foreground=TEXT,
                                anchor="w", justify=tk.LEFT, wraplength=620)
        self._status.grid(row=row, column=0, columnspan=3, sticky="we", pady=(10, 4))
        row += 1
        buttons = tk.Frame(self.window, background=BACKGROUND)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e")
        self._run_button = ttk.Button(buttons, text="가져오기", command=self._start)
        self._run_button.pack(side=tk.LEFT, padx=4)
        self._open_button = ttk.Button(buttons, text="분석 탭에서 열기", command=self._open, state=tk.DISABLED)
        self._open_button.pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="닫기", command=self.window.destroy).pack(side=tk.LEFT, padx=4)
        self.window.columnconfigure(1, weight=1)
        self._update_state()

    def _choose_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self.window, title="처리부 logs 폴더",
                                         initialdir=self._local_dir.get() or str(Path.home()))
        if folder:
            self._local_dir.set(folder)

    def _update_state(self) -> None:
        local = self._source.get() == "local"
        self._local_entry.configure(state=tk.NORMAL if local else tk.DISABLED)
        self._browse.configure(state=tk.NORMAL if local else tk.DISABLED)
        for entry in self._ssh_entries:
            entry.configure(state=tk.DISABLED if local else tk.NORMAL)

    def _current_settings(self) -> FetchSettings:
        try:
            port = int(self._port.get())
        except ValueError:
            port = 22
        return replace(self._settings, source=self._source.get(), local_dir=self._local_dir.get().strip(),
                       ssh_target=self._target.get().strip(), ssh_port=port,
                       remote_dir=self._remote_dir.get().strip(), ssh_options=self._options.get().strip())

    def _start(self) -> None:
        settings = self._current_settings()
        if self._save.get():
            self._on_saved(settings)
        self._run_button.configure(state=tk.DISABLED)
        self._status.configure(text="가져오는 중...", foreground=TEXT)

        def report(text: str) -> None:
            self.window.after(0, lambda: self._status.configure(text=text, foreground=TEXT))

        def work() -> None:
            try:
                result = fetch_logs(settings, self._destination, report)
            except FetchError as error:
                message = str(error)
                self.window.after(0, lambda: self._finish(None, message))
                return
            except OSError as error:
                message = f"파일 복사 실패: {error}"
                self.window.after(0, lambda: self._finish(None, message))
                return
            self.window.after(0, lambda: self._finish(result, ""))

        threading.Thread(target=work, name="log-fetch", daemon=True).start()

    def _finish(self, result: Optional[FetchResult], error: str) -> None:
        if not self.window.winfo_exists():
            return
        self._run_button.configure(state=tk.NORMAL)
        if result is None:
            self._status.configure(text=f"실패: {error}", foreground=BAD)
            return
        self._result = result
        self._status.configure(text=f"완료: {result.describe()}", foreground=OK)
        self._open_button.configure(state=tk.NORMAL)

    def _open(self) -> None:
        if self._result is not None:
            self._on_done(self._result)
        self.window.destroy()
