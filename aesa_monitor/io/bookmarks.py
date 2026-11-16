"""분석 북마크: 사람이 찾은 문제 시점(또는 구간)에 제목·분류·트랙·메모를 붙여 세션과 함께 저장한다.

시각은 처리부 시각(ms)이다. 녹화와 처리부 로그가 같은 기준을 쓰므로 이벤트 타임라인과 바로 겹쳐 그릴 수 있다.

저장 위치
- 세션 폴더를 열었으면 그 폴더의 bookmarks.json
- 파일 하나(녹화, 로그, 캡처)를 열었으면 같은 폴더의 '<파일 이름>.bookmarks.json'

파일 형식(JSON): {"version": 1, "bookmarks": [{"start_ms": 29150, "end_ms": null, "title": "...",
"tag": "이슈", "track_id": 3, "note": "...", "created": "2026-11-16T23:10:00"}, ...]}
"""

from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

BOOKMARK_FILE = "bookmarks.json"
FORMAT_VERSION = 1
TAGS = ("이슈", "확인 필요", "참고")
DEFAULT_TAG = TAGS[0]


class BookmarkError(Exception):
    """북마크 파일을 읽거나 시각을 해석하지 못했다."""


@dataclass
class Bookmark:
    start_ms: int
    title: str
    end_ms: Optional[int] = None
    tag: str = DEFAULT_TAG
    track_id: Optional[int] = None
    note: str = ""
    created: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        if self.end_ms is not None and self.end_ms < self.start_ms:
            self.start_ms, self.end_ms = self.end_ms, self.start_ms
        if self.end_ms == self.start_ms:
            self.end_ms = None

    @property
    def is_range(self) -> bool:
        return self.end_ms is not None


def bookmark_path(session_path: Path) -> Path:
    """세션 경로(폴더나 파일)에 맞는 북마크 파일 경로."""
    session_path = Path(session_path)
    if session_path.is_dir():
        return session_path / BOOKMARK_FILE
    return session_path.with_name(f"{session_path.name}.{BOOKMARK_FILE}")


def load_bookmarks(path: Path) -> List[Bookmark]:
    """북마크 파일을 읽는다. 파일이 없으면 빈 목록이다."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data["bookmarks"] if isinstance(data, dict) else data
        bookmarks = []
        for item in items:
            bookmarks.append(Bookmark(
                start_ms=int(item["start_ms"]),
                end_ms=None if item.get("end_ms") is None else int(item["end_ms"]),
                title=str(item.get("title", "")),
                tag=str(item.get("tag", DEFAULT_TAG)),
                track_id=None if item.get("track_id") is None else int(item["track_id"]),
                note=str(item.get("note", "")),
                created=str(item.get("created", "")),
            ))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BookmarkError(f"{path.name}을(를) 읽지 못함: {error}") from error
    return sort_bookmarks(bookmarks)


def save_bookmarks(path: Path, bookmarks: List[Bookmark]) -> None:
    """북마크 파일을 쓴다. 임시 파일에 다 쓴 뒤 바꿔 넣어 쓰다 끊겨도 기존 파일이 깨지지 않게 한다."""
    path = Path(path)
    data = {"version": FORMAT_VERSION, "bookmarks": [asdict(bookmark) for bookmark in sort_bookmarks(bookmarks)]}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sort_bookmarks(bookmarks: List[Bookmark]) -> List[Bookmark]:
    return sorted(bookmarks, key=lambda bookmark: (bookmark.start_ms, bookmark.end_ms or bookmark.start_ms))


_TIME_PATTERN = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d*)?)\s*$")


def parse_time(text: str) -> Optional[int]:
    """'01:10.250', '1:02:03.5', '70.25'(초)를 ms로 바꾼다. 빈 글자는 None이다."""
    if not text or not text.strip():
        return None
    match = _TIME_PATTERN.match(text)
    if match is None:
        raise BookmarkError(f"시각 형식이 아닙니다: {text!r} (예: 01:10.250 또는 70.25)")
    first, second, seconds = match.groups()
    if second is not None:
        hours, minutes = int(first), int(second)
    else:
        hours, minutes = 0, int(first) if first is not None else 0
    return int(round(((hours * 60 + minutes) * 60 + float(seconds)) * 1000))
