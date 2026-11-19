"""[처리부 제어] 창. 보드의 aesactl(시작, 중지, 상태, 보드 끄기)을 ssh로 실행한다(io/board_control.py)."""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Callable, List, Optional

from aesa_monitor.core.settings import FetchSettings
from aesa_monitor.io.board_control import ACTIONS, BoardError, BoardResult, run_action
from aesa_monitor.ui.style import BACKGROUND, BAD, FONT_BODY, FONT_FIXED, FONT_SMALL, MUTED, OK, TEXT

BUTTON_ORDER = ("status", "start", "restart", "stop", "settime")
CONFIRM = {
    "restart": "처리부를 다시 시작합니다. 연결된 운용통제기·모의기 연결이 끊깁니다. 계속할까요?",
    "stop": "처리부를 중지합니다. 로그는 보드 SD로 복사됩니다. 계속할까요?",
    "poweroff": "처리부를 멈추고 보드를 끕니다. 다시 켜려면 보드 전원을 껐다 켜야 합니다. 계속할까요?",
}


class BoardDialog:
    def __init__(self, parent: tk.Misc, settings: Callable[[], FetchSettings],
                 open_fetch: Callable[[], None]) -> None:
        self._settings = settings
        self._open_fetch = open_fetch
        self._busy = False
        self.window = tk.Toplevel(parent)
        self.window.title("처리부 제어")
        self.window.configure(background=BACKGROUND, padx=16, pady=12)
        self.window.transient(parent.winfo_toplevel())

        self._target = tk.Label(self.window, font=FONT_BODY, background=BACKGROUND, foreground=TEXT, anchor="w")
        self._target.grid(row=0, column=0, sticky="we")
        tk.Label(self.window, text="[시작]·[재시작]·[시각 맞추기]는 이 노트북의 시각을 보드에 넣습니다(보드에 시계가 없음). "
                                   "접속 정보는 [로그 가져오기] 창에서 바꿉니다.",
                 font=FONT_SMALL, background=BACKGROUND, foreground=MUTED, anchor="w", justify=tk.LEFT,
                 wraplength=640).grid(row=1, column=0, sticky="we", pady=(2, 8))

        buttons = tk.Frame(self.window, background=BACKGROUND)
        buttons.grid(row=2, column=0, sticky="we")
        self._buttons: List[ttk.Button] = []
        for name in BUTTON_ORDER:
            button = ttk.Button(buttons, text=ACTIONS[name].label, command=lambda value=name: self.run(value))
            button.pack(side=tk.LEFT, padx=(0, 4))
            self._buttons.append(button)
        fetch = ttk.Button(buttons, text="로그 가져오기…", command=self._open_fetch)
        fetch.pack(side=tk.LEFT, padx=(8, 4))
        power = ttk.Button(buttons, text=ACTIONS["poweroff"].label, command=lambda: self.run("poweroff"))
        power.pack(side=tk.RIGHT)
        self._buttons.append(power)

        self._status = tk.Label(self.window, text="", font=FONT_BODY, background=BACKGROUND, foreground=TEXT,
                                anchor="w", justify=tk.LEFT, wraplength=640)
        self._status.grid(row=3, column=0, sticky="we", pady=(10, 4))
        frame = tk.Frame(self.window, background=BACKGROUND)
        frame.grid(row=4, column=0, sticky="nsew")
        self._output = tk.Text(frame, height=16, width=90, font=FONT_FIXED, wrap=tk.NONE, state=tk.DISABLED,
                               background=BACKGROUND, foreground=TEXT, relief=tk.SOLID, borderwidth=1)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._output.yview)
        self._output.configure(yscrollcommand=scroll.set)
        self._output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Button(self.window, text="닫기", command=self.window.destroy).grid(row=5, column=0, sticky="e", pady=(8, 0))
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(4, weight=1)
        self.run("status")

    def run(self, name: str) -> None:
        if self._busy:
            return
        question = CONFIRM.get(name)
        if question and not messagebox.askyesno("처리부 제어", question, parent=self.window):
            return
        settings = self._settings()
        action = ACTIONS[name]
        self._busy = True
        for button in self._buttons:
            button.configure(state=tk.DISABLED)
        self._target.configure(text=f"보드: {settings.ssh_target} (포트 {settings.ssh_port})")
        self._status.configure(text=f"{action.label} 실행 중...", foreground=TEXT)

        def work() -> None:
            try:
                result: Optional[BoardResult] = run_action(settings, name)
                error = ""
            except BoardError as failure:
                result, error = None, str(failure)
            except Exception as failure:  # 예상하지 못한 오류도 창에 보이고 버튼을 되살린다
                result, error = None, f"예상하지 못한 오류: {failure!r}"
            self.window.after(0, lambda: self._finish(action.label, result, error))

        threading.Thread(target=work, name=f"board-{name}", daemon=True).start()

    def _finish(self, label: str, result: Optional[BoardResult], error: str) -> None:
        if not self.window.winfo_exists():
            return
        self._busy = False
        for button in self._buttons:
            button.configure(state=tk.NORMAL)
        stamp = datetime.now().strftime("%H:%M:%S")
        if result is None:
            self._status.configure(text=f"{label} 실패: {error.splitlines()[0]}", foreground=BAD)
            self._append(f"[{stamp}] {label} 실패\n{error}\n")
            return
        self._status.configure(text=f"{label} 완료", foreground=OK)
        self._append(f"[{stamp}] {label}\n{result.output}\n")

    def _append(self, text: str) -> None:
        self._output.configure(state=tk.NORMAL)
        self._output.insert(tk.END, text + "\n")
        self._output.see(tk.END)
        self._output.configure(state=tk.DISABLED)
