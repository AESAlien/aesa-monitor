"""[북마크 추가·편집] 창. 시각(구간)·제목·분류·트랙·메모를 받아 Bookmark를 만든다."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

from aesa_monitor.io.bookmarks import TAGS, Bookmark, BookmarkError, parse_time
from aesa_monitor.ui.style import BACKGROUND, BAD, FONT_BODY, FONT_SMALL, MUTED, TEXT, format_time

NO_TRACK = "-"


class BookmarkDialog:
    """modal 창. 저장을 누르면 on_save(Bookmark)를 부르고 닫는다. 취소하면 아무것도 하지 않는다."""

    def __init__(self, parent: tk.Misc, on_save: Callable[[Bookmark], None], start_ms: Optional[int] = None,
                 track_ids: Optional[List[int]] = None, bookmark: Optional[Bookmark] = None,
                 save_path: str = "") -> None:
        self._on_save = on_save
        self._original = bookmark
        self.window = tk.Toplevel(parent)
        self.window.title("북마크 편집" if bookmark else "북마크 추가")
        self.window.configure(background=BACKGROUND, padx=16, pady=12)
        self.window.transient(parent.winfo_toplevel())
        self.window.resizable(False, False)

        if bookmark is not None:
            start_ms, end_ms = bookmark.start_ms, bookmark.end_ms
        else:
            end_ms = None
        self._start = tk.StringVar(value="" if start_ms is None else format_time(start_ms))
        self._end = tk.StringVar(value="" if end_ms is None else format_time(end_ms))
        self._title = tk.StringVar(value=bookmark.title if bookmark else "")
        self._tag = tk.StringVar(value=bookmark.tag if bookmark else TAGS[0])
        track_text = NO_TRACK if bookmark is None or bookmark.track_id is None else f"#{bookmark.track_id}"
        self._track = tk.StringVar(value=track_text)

        row = 0
        self._label("처리부 시각", row)
        times = tk.Frame(self.window, background=BACKGROUND)
        times.grid(row=row, column=1, sticky="w", pady=3)
        self._start_entry = ttk.Entry(times, textvariable=self._start, width=12)
        self._start_entry.pack(side=tk.LEFT)
        tk.Label(times, text=" ~ ", background=BACKGROUND, foreground=TEXT).pack(side=tk.LEFT)
        ttk.Entry(times, textvariable=self._end, width=12).pack(side=tk.LEFT)
        tk.Label(times, text="  끝을 비우면 한 시점, 예: 01:10.250", background=BACKGROUND, foreground=MUTED,
                 font=FONT_SMALL).pack(side=tk.LEFT)
        row += 1
        self._label("제목", row)
        self._title_entry = ttk.Entry(self.window, textvariable=self._title, width=48)
        self._title_entry.grid(row=row, column=1, sticky="we", pady=3)
        row += 1
        self._label("분류", row)
        ttk.Combobox(self.window, textvariable=self._tag, values=TAGS, state="readonly", width=12).grid(
            row=row, column=1, sticky="w", pady=3)
        row += 1
        self._label("트랙", row)
        values = [NO_TRACK] + [f"#{track_id}" for track_id in (track_ids or [])]
        ttk.Combobox(self.window, textvariable=self._track, values=values, width=12).grid(
            row=row, column=1, sticky="w", pady=3)
        row += 1
        self._label("메모", row, anchor="ne")
        self._note = tk.Text(self.window, width=48, height=5, font=FONT_BODY, wrap=tk.WORD, relief=tk.SOLID,
                             borderwidth=1, background=BACKGROUND, foreground=TEXT)
        self._note.grid(row=row, column=1, sticky="we", pady=3)
        if bookmark is not None and bookmark.note:
            self._note.insert("1.0", bookmark.note)
        row += 1
        if save_path:
            tk.Label(self.window, text=f"저장 위치: {save_path}", background=BACKGROUND, foreground=MUTED,
                     font=FONT_SMALL, anchor="w", justify=tk.LEFT, wraplength=560).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
            row += 1
        self._error = tk.Label(self.window, text="", background=BACKGROUND, foreground=BAD, font=FONT_BODY,
                               anchor="w")
        self._error.grid(row=row, column=0, columnspan=2, sticky="we")
        row += 1
        buttons = tk.Frame(self.window, background=BACKGROUND)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(buttons, text="저장", command=self._save).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="취소", command=self.window.destroy).pack(side=tk.LEFT)

        self.window.bind("<Return>", lambda _event: self._save() if self.window.focus_get() is not self._note
                         else None)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        (self._title_entry if start_ms is not None else self._start_entry).focus_set()
        self.window.grab_set()

    def _label(self, text: str, row: int, anchor: str = "e") -> None:
        tk.Label(self.window, text=text, background=BACKGROUND, foreground=MUTED).grid(
            row=row, column=0, sticky=anchor, padx=(0, 10), pady=3)

    def _save(self) -> None:
        try:
            start_ms = parse_time(self._start.get())
            end_ms = parse_time(self._end.get())
        except BookmarkError as error:
            self._error.configure(text=str(error))
            return
        if start_ms is None:
            self._error.configure(text="시작 시각을 넣어 주세요")
            return
        title = self._title.get().strip()
        if not title:
            self._error.configure(text="제목을 넣어 주세요")
            return
        track_text = self._track.get().strip().lstrip("#")
        if track_text in ("", NO_TRACK):
            track_id = None
        elif track_text.isdigit():
            track_id = int(track_text)
        else:
            self._error.configure(text="트랙은 번호(예: #3)나 '-'로 넣어 주세요")
            return
        bookmark = Bookmark(start_ms=start_ms, end_ms=end_ms, title=title, tag=self._tag.get(), track_id=track_id,
                            note=self._note.get("1.0", tk.END).strip())
        if self._original is not None and self._original.created:
            bookmark.created = self._original.created
        self.window.grab_release()
        self.window.destroy()
        self._on_save(bookmark)
