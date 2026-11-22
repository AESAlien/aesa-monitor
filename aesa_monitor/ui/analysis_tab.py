"""분석 탭 — 녹화와 처리부 로그로 "무슨 일이 있었나?"를 본다.

맨 위 한 줄에 세션 요약과 처리부 실행 선택을 두고, 아래를 하위 탭 [문제 확인] [통계] [트랙]으로 나눈다(#6).
한 화면에 모두 넣으면 1366×768·글자 16에서 문제 목록과 통계 표가 가려졌다.

- 통계 요약: 빔 스케줄, 추적, 통신, 처리부 로그 항목별 값·기준·판정. 항목을 누르면 분포를 그린다.
  빔 점유율·탐색 프레임별 점유율 항목은 분포 대신 탐색 프레임별 빔 수(프레임 시간 띠 + 확인·추적 막대)를 그린다.
  기본은 묶음 줄과 주의·이상 항목만 보이고 [전체 항목 보기]로 모두 펼친다.
- 트랙별 요약: 처리부 실행 구간, 시작, 수명, 미탐, 빔-탐지 차이 평균.
- 이벤트 타임라인(문제 확인): 메시지 로그의 운용·자세·요청 거부·경고를 처리부 시각에 놓는다.
  처리부 실행이 여럿이면 고른 실행 구간만 그린다(처리부 시각이 실행마다 0부터라 겹치므로).
  이벤트를 누르면 녹화 재생 위치를 그 시각 직전으로 옮긴다.
- 문제: 녹화를 열 때 처음부터 다시 계산해 자동으로 기록한 문제 사건(core/problems.py)과 처리부 로그의
  요청 거부·경고·오류. 누르면 재생 위치를 그 1초 전으로 옮긴다. 세션이 없으면 실시간으로 감지한 문제를 보여준다.
- 북마크(보조): 자동 기록이 못 잡은 것이나 원인 메모를 사람이 남긴다. 세션 옆 bookmarks.json에 저장한다.
  타임라인 맨 위 줄과 오른쪽 [북마크] 목록에 보이고, 누르면 재생 위치를 그 시각으로 옮긴다.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

from aesa_monitor.core.problems import BAD as LEVEL_BAD, Problem, level_counts
from aesa_monitor.core.state import MonitorState
from aesa_monitor.io.processor_log import (
    CATEGORY_ATTITUDE, CATEGORY_LIFECYCLE, CATEGORY_OPERATION, CATEGORY_OTHER, CATEGORY_PROBLEM,
    CATEGORY_REJECTED, LogEvent,
)
from aesa_monitor.io.bookmarks import Bookmark, sort_bookmarks
from aesa_monitor.io.replay import ReplayPosition
from aesa_monitor.io.session import Session
from aesa_monitor.ui.bookmark_dialog import BookmarkDialog
from aesa_monitor.ui.style import (
    blend, BACKGROUND, BAD, CHART_COLOR, FONT_BODY, FONT_SMALL, FONT_TITLE, GRID_LINE, LEVEL_COLORS, LEVEL_NAMES, MUTED,
    OK, PANEL, TEXT, WARN, format_time,
)
from aesa_monitor.ui.widgets import (
    HelpButton, LabelPlacer, Table, Tooltip, canvas_size, empty_message, frame_request_chart, histogram, nice_ticks,
    section_title,
)

TIMELINE_LANES = (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM, CATEGORY_LIFECYCLE)
EVENT_FILTERS = {
    "중요": (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM),
    "전체": (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM, CATEGORY_LIFECYCLE,
           CATEGORY_OTHER),
    "거부만": (CATEGORY_REJECTED,),
    "경고·오류만": (CATEGORY_PROBLEM,),
}
HISTOGRAM_REFERENCES = {"연속 빔 간격": 10.0, "연속 탐지 간격": 10.0}
BOOKMARK_COLORS = {"이슈": BAD, "확인 필요": WARN, "참고": MUTED}
BOOKMARK_TAGS = {"이슈": "bad", "확인 필요": "warn", "참고": "text"}
PROBLEM_FILTERS = ("전체", "이상만", "빔", "추적", "통신", "처리부 로그")
PROBLEM_LEAD_S = 1.0  # 문제를 누르면 이만큼 앞에서 재생을 시작해 직전 상황을 본다
HELP_PROBLEMS = (
    "모니터가 문제 상황을 자동으로 기록한 목록입니다. 사람이 누르지 않아도 남습니다.\n\n"
    "• 기준은 실시간 판정과 같습니다: 빠진 빔, 빔 없음, 탐색 프레임 시간·추적 주기·탐색→확인 지연 이탈, 트랙 미탐·종료, "
    "확인 실패, 처리부 연결 끊김·상대 연결 안 됨, 처리부 재시작, 처리부 로그의 요청 거부·경고·오류.\n"
    "• 모니터 추정이 섞인 값(예측-측정 잔차, 연관 탐지 선택과 그에 기댄 빔 안 위치)은 기록하지 않습니다.\n"
    "• 같은 종류(트랙 문제는 같은 트랙)가 2초 안에 다시 생기면 한 건으로 합치고 '횟수'에 셉니다.\n"
    "• 녹화를 열 때 처음부터 다시 계산합니다. 기준을 고치면 예전 녹화에도 그대로 적용됩니다.\n"
    "• 행이나 타임라인 '문제' 줄을 누르면 재생 위치를 그 1초 전으로 옮기고, 트랙 문제면 추적 탭에서 그 트랙을 고릅니다.\n"
    "• 오른쪽 클릭 → '북마크로 남기기'로 원인 메모를 달아 둘 수 있습니다.\n"
    "• 세션을 열지 않았으면 모니터를 켠 뒤 실시간으로 감지한 문제를 보여줍니다(녹화해 두어야 나중에 그 위치로 갈 수 있음)."
)
HELP_BOOKMARKS = (
    "자동 기록(문제 목록)을 돕는 기능입니다. 자동 기록이 못 잡은 시점(또는 구간)이나 원인 메모를 직접 남깁니다.\n\n"
    "• 추가: [+ 북마크] 버튼이나 B 키(글자 입력 칸 밖에서, 한글 입력 상태가 아닐 때). 시작 시각은 지금 재생 위치로 채워집니다.\n"
    "• 구간: 끝 시각도 넣으면 타임라인에 띠로 그립니다.\n"
    "• 이동: 목록 행이나 타임라인 ▼·띠를 누르면 재생 위치를 그 시각으로 옮기고, 트랙을 넣었으면 추적 탭에서 그 트랙을 고릅니다.\n"
    "• 편집: 목록 행을 두 번 누르거나 오른쪽 클릭 → 편집. 삭제: 오른쪽 클릭 → 삭제, 또는 Delete 키.\n"
    "• 저장: 세션 폴더의 bookmarks.json(파일 하나를 열었으면 '<파일 이름>.bookmarks.json')에 바로 씁니다. "
    "녹화 폴더와 함께 넘기면 다른 사람도 같은 북마크를 봅니다.\n\n"
    "시각은 처리부 시각입니다(이벤트 타임라인과 같은 기준)."
)

GUIDE = (
    "분석할 데이터를 여세요. 여는 메뉴는 오른쪽 위 [메뉴]에 있습니다.\n\n"
    "• 모니터 녹화: 위쪽 [● 녹화]로 녹화하면 ~/aesa-records/<시각>/ 에 저장됩니다. 재생과 모든 통계가 됩니다.\n"
    "• 처리부 로그: [메뉴] → 로그 가져오기로 처리부의 adp_*.log, adp_detection_*.csv를 가져옵니다.\n"
    "  로그만 있으면 빔 방향과 트랙 목록이 없어서 일부 통계만 나옵니다.\n"
    "• [메뉴] → 폴더 열기로 세션 폴더(녹화+로그)를 한 번에 엽니다.\n\n"
    "세션을 열지 않았으면 [문제 확인]에 모니터를 켠 뒤 실시간으로 감지한 문제가 보입니다."
)
NO_SESSION = "세션 없음 — [메뉴] → 폴더 열기·파일 열기"
PROBLEM_NOTE_EMPTY = "목록이나 타임라인의 문제를 누르면 설명이 여기에 나오고, 재생 위치가 그 직전으로 옮겨집니다."
STATS_SHOW_ALL = "전체 항목 보기"
STATS_FLAGGED_ONLY = "주의·이상만"


def run_label(index: int, count: int, label: str) -> str:
    """처리부 실행 콤보 상자 글자: '실행 2/2 · 16:54:37'. 로그 묶음 이름(날짜 시각 (종류))에서 시각만 쓴다."""
    parts = label.split()
    clock = parts[1] if len(parts) > 1 and ":" in parts[1] else parts[0] if parts else label
    return f"실행 {index + 1}/{count} · {clock}"


def event_level(event: LogEvent) -> str:
    if event.level in ("error", "critical"):
        return "bad"
    if event.level in ("warning", "warn"):
        return "warn"
    return "ok" if event.category == CATEGORY_OPERATION else "info"


class AnalysisTab:
    def __init__(self, parent: tk.Widget, focus_event: Callable[[LogEvent], None],
                 select_run: Callable[[int], None], focus_time: Callable[..., None],
                 current_position: Callable[[], Optional[ReplayPosition]],
                 live_problems: Callable[[], List[Problem]]) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND, padx=12, pady=8)
        self._focus_event = focus_event
        self._focus_time = focus_time
        self._current_position = current_position
        self._live_problems = live_problems
        self._problems: List[Problem] = []
        self._problem_signature: tuple = ()
        self._problem_filter = tk.StringVar(value=PROBLEM_FILTERS[0])
        self._problem_points: List[tuple] = []
        self._bookmark_points: List[tuple] = []
        self._select_run = select_run
        self._session: Optional[Session] = None
        self._events: List[LogEvent] = []
        self._timeline_points: List[tuple] = []
        self._filter = tk.StringVar(value="중요")
        self._current_ms: Optional[int] = None
        self._current_segment: Optional[int] = None

        # 세션 한 줄: 이름·녹화 길이 · (?) 자세히 · 처리부 실행. 폴더·파일 열기, 로그 가져오기는 상단 [메뉴]에 있다.
        header = tk.Frame(self.frame, background=PANEL, padx=10, pady=4)
        header.pack(fill=tk.X)
        self._info = tk.Label(header, text=NO_SESSION, font=FONT_BODY, background=PANEL, foreground=TEXT, anchor="w")
        self._info.pack(side=tk.LEFT)
        HelpButton(header, "세션 정보", self._session_text, background=PANEL).pack(side=tk.LEFT, padx=6)
        self._run_box = ttk.Combobox(header, state="readonly", width=22)
        self._run_box.pack(side=tk.RIGHT)
        self._run_box.bind("<<ComboboxSelected>>", lambda _event: self._select_run(self._run_box.current()))
        self._run_label = tk.Label(header, text="처리부 실행", background=PANEL, foreground=MUTED)
        self._run_label.pack(side=tk.RIGHT, padx=(0, 6))
        Tooltip(self._run_label, "한 폴더에 처리부를 여러 번 실행한 로그가 있으면 여기서 고릅니다.\n"
                "통계와 이벤트 타임라인이 그 실행 기준으로 바뀝니다.")

        # 하위 탭: [문제 확인] [통계] [트랙]. 한 화면에 다 넣으면 1366×768에서 목록·표가 가려진다(#6).
        self._pages = ttk.Notebook(self.frame)
        self._pages.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._check_page = tk.Frame(self._pages, background=BACKGROUND, padx=8, pady=6)
        self._stats_page = tk.Frame(self._pages, background=BACKGROUND, padx=8, pady=6)
        self._track_page = tk.Frame(self._pages, background=BACKGROUND, padx=8, pady=6)
        self._pages.add(self._check_page, text=" 문제 확인 ")
        self._pages.add(self._stats_page, text=" 통계 ")
        self._pages.add(self._track_page, text=" 트랙 ")
        self._build_check_page(self._check_page)
        self._build_stats_page(self._stats_page)
        self._build_track_page(self._track_page)
        self._fill_stats()
        self._show_run_box(False)

    def _build_check_page(self, page: tk.Frame) -> None:
        """문제 확인: 왼쪽 이벤트 타임라인 + 고른 문제 설명, 오른쪽 [문제|이벤트|북마크] 목록(세로 전체)."""
        page.columnconfigure(0, weight=5, uniform="check")
        page.columnconfigure(1, weight=4, uniform="check")
        page.rowconfigure(0, weight=1)
        left = tk.Frame(page, background=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        timeline_header = tk.Frame(left, background=BACKGROUND)
        timeline_header.pack(fill=tk.X)
        self._timeline_title = section_title(
            timeline_header, "이벤트 타임라인",
            "처리부 메시지 로그(adp_*.log)의 이벤트를 처리부 시각에 놓았습니다.\n"
            "처리부 시각 0은 로그의 워커 시작 줄('… worker threads started')입니다(수 ms 오차, 추정).\n"
            "녹화 중 처리부가 다시 시작해 실행이 여럿이면, 위 '처리부 실행'에서 고른 실행 구간만 그립니다.\n"
            "맨 위 회색 띠는 녹화가 있는 구간, 세로 선은 현재 재생 위치입니다. "
            "점이나 목록 행을 누르면 재생 위치를 그 이벤트 1초 전으로 옮깁니다.")
        self._timeline_title.master.pack(side=tk.LEFT)
        HelpButton(timeline_header, "북마크", HELP_BOOKMARKS).pack(side=tk.RIGHT)
        ttk.Button(timeline_header, text="+ 북마크 (B)", command=self.add_bookmark).pack(side=tk.RIGHT, padx=4)
        event_box = ttk.Combobox(timeline_header, textvariable=self._filter, values=list(EVENT_FILTERS),
                                 state="readonly", width=9)
        event_box.pack(side=tk.RIGHT, padx=4)
        event_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_events())
        Tooltip(event_box, "타임라인에 크게 그리고 [이벤트] 목록에 보일 처리부 로그 이벤트")
        self._timeline = tk.Canvas(left, background=BACKGROUND, highlightthickness=0, height=200)
        self._timeline.pack(fill=tk.BOTH, expand=True)
        self._timeline.bind("<Button-1>", self._on_timeline_click)
        self._timeline.bind("<Configure>", lambda _event: self._draw_timeline())
        detail = tk.Frame(left, background=PANEL, padx=10, pady=6)
        detail.pack(fill=tk.X, pady=(6, 0))
        tk.Label(detail, text="고른 문제", font=FONT_TITLE, background=PANEL, foreground=TEXT,
                 anchor="w").pack(fill=tk.X)
        self._problem_note = tk.Label(detail, text=PROBLEM_NOTE_EMPTY, font=FONT_BODY, background=PANEL,
                                      foreground=MUTED, anchor="nw", justify=tk.LEFT, height=3)
        self._problem_note.pack(fill=tk.X)
        self._problem_note.bind("<Configure>", lambda event: self._problem_note.configure(
            wraplength=max(event.width - 8, 100)))

        # 오른쪽 목록: [문제 | 이벤트 | 북마크]
        self._lists = ttk.Notebook(page)
        self._lists.grid(row=0, column=1, sticky="nsew")
        self._problem_page = tk.Frame(self._lists, background=BACKGROUND)
        self._lists.add(self._problem_page, text="문제")
        problem_bar = tk.Frame(self._problem_page, background=BACKGROUND)
        problem_bar.pack(fill=tk.X)
        ttk.Combobox(problem_bar, textvariable=self._problem_filter, values=PROBLEM_FILTERS, state="readonly",
                     width=10).pack(side=tk.LEFT)
        self._problem_filter.trace_add("write", lambda *_args: self._fill_problems(force=True))
        self._problem_summary = tk.Label(problem_bar, text="", font=FONT_SMALL, background=BACKGROUND,
                                         foreground=MUTED, anchor="w")
        self._problem_summary.pack(side=tk.LEFT, padx=8)
        HelpButton(problem_bar, "문제 자동 기록", HELP_PROBLEMS).pack(side=tk.RIGHT)
        self._problem_table = Table(self._problem_page, (
            ("time", "처리부 시각", 125, tk.E), ("level", "수준", 85, tk.CENTER), ("category", "분류", 65, tk.CENTER),
            ("title", "내용", 240, tk.W), ("count", "횟수", 55, tk.E),
        ), height=3, on_select=self._on_problem_selected)
        self._problem_table.frame.pack(fill=tk.BOTH, expand=True)
        self._problem_menu = tk.Menu(self.frame, tearoff=False)
        self._problem_menu.add_command(label="북마크로 남기기 (원인 메모)", command=self._bookmark_selected_problem)
        self._problem_table.tree.bind("<Button-3>", self._show_problem_menu)

        self._event_table = Table(self._lists, (
            ("time", "처리부 시각", 100, tk.E), ("category", "분류", 80, tk.CENTER), ("text", "내용", 320, tk.W),
        ), height=4, on_select=self._on_event_selected)
        self._lists.add(self._event_table.frame, text="이벤트")
        bookmark_page = tk.Frame(self._lists, background=BACKGROUND)
        self._bookmark_page = bookmark_page
        self._lists.add(bookmark_page, text="북마크")
        # 버튼 줄은 두지 않는다(낮은 창에서 목록이 가려짐). 추가는 위 [+ 북마크]·B 키, 편집은 두 번 누르기,
        # 편집·삭제는 오른쪽 클릭 메뉴와 Delete 키로 한다.
        self._bookmark_note = tk.Label(bookmark_page, text="", font=FONT_SMALL, background=BACKGROUND,
                                       foreground=MUTED, anchor="w", justify=tk.LEFT)
        self._bookmark_note.pack(side=tk.BOTTOM, fill=tk.X)
        self._bookmark_note.bind("<Configure>", lambda event: self._bookmark_note.configure(
            wraplength=max(event.width - 8, 100)))
        self._bookmark_table = Table(bookmark_page, (
            ("time", "처리부 시각", 150, tk.E), ("tag", "분류", 70, tk.CENTER), ("title", "제목", 220, tk.W),
            ("track", "트랙", 50, tk.CENTER),
        ), height=3, on_select=self._on_bookmark_selected, on_double=lambda _iid: self._edit_selected_bookmark())
        self._bookmark_table.frame.pack(fill=tk.BOTH, expand=True)
        self._bookmark_menu = tk.Menu(self.frame, tearoff=False)
        self._bookmark_menu.add_command(label="편집", command=self._edit_selected_bookmark)
        self._bookmark_menu.add_command(label="삭제", command=self._delete_selected_bookmark)
        self._bookmark_menu.add_separator()
        self._bookmark_menu.add_command(label="새 북마크 추가", command=self.add_bookmark)
        tree = self._bookmark_table.tree
        tree.bind("<Button-3>", self._show_bookmark_menu)
        tree.bind("<Delete>", lambda _event: self._delete_selected_bookmark())

    def _build_stats_page(self, page: tk.Frame) -> None:
        """통계: 왼쪽 표(묶음 + 주의·이상 항목만 / 전체 항목), 오른쪽 고른 항목의 설명과 분포."""
        page.columnconfigure(0, weight=7, uniform="stats")
        page.columnconfigure(1, weight=3, uniform="stats")
        page.rowconfigure(0, weight=1)
        left = tk.Frame(page, background=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        header = tk.Frame(left, background=BACKGROUND)
        header.pack(fill=tk.X)
        section_title(header, "통계 요약", "녹화(또는 탐지 CSV) 전체를 훑어 계산합니다. 판정 기준은 실시간 탭과 같습니다.\n"
                      "처음에는 묶음 줄과 주의·이상 항목만 보입니다. [전체 항목 보기]로 모든 항목을 펼칩니다.\n"
                      "묶음 줄의 ▸를 눌러 그 묶음만 펼칠 수도 있습니다. 항목을 누르면 오른쪽에 값의 분포와 설명을 보여줍니다."
                      ).master.pack(side=tk.LEFT)
        self._stats_all = False
        self._stats_toggle = ttk.Button(header, text=STATS_SHOW_ALL, command=self._toggle_stats)
        self._stats_toggle.pack(side=tk.RIGHT)
        self._stats = Table(left, (
            ("name", "항목", 200, tk.W), ("value", "값", 330, tk.W), ("reference", "기준", 120, tk.W),
            ("level", "판정", 60, tk.CENTER),
        ), height=3, on_select=self._on_stat_selected)
        # 묶음을 접고 펼치는 ▸ 칸(#0)을 보인다.
        self._stats.tree.configure(show=("tree", "headings"))
        self._stats.tree.column("#0", width=28, minwidth=28, stretch=False)
        self._stats.tree.tag_configure("group", font="AesaBold", background=PANEL)
        self._stats.frame.pack(fill=tk.BOTH, expand=True)
        right = tk.Frame(page, background=BACKGROUND)
        right.grid(row=0, column=1, sticky="nsew")
        section_title(right, "값의 분포")
        self._note = tk.Label(right, text="", font=FONT_SMALL, background=BACKGROUND, foreground=MUTED,
                              anchor="w", justify=tk.LEFT)
        self._note.pack(fill=tk.X)
        self._note.bind("<Configure>", lambda event: self._note.configure(wraplength=max(event.width - 8, 100)))
        self._histogram = tk.Canvas(right, background=BACKGROUND, highlightthickness=0, height=80)
        self._histogram.pack(fill=tk.BOTH, expand=True)
        self._histogram.bind("<Configure>", lambda _event: self._draw_histogram())
        self._selected_stat: Optional[int] = None

    def _build_track_page(self, page: tk.Frame) -> None:
        header = tk.Frame(page, background=BACKGROUND)
        header.pack(fill=tk.X)
        section_title(header, "트랙별 요약", "확인·추적 빔을 받은 요청 ID별 요약입니다. 시작은 첫 확인(또는 추적) 빔의 처리부 시각입니다.\n"
                      "실행: 녹화 중 처리부가 다시 시작하면 트랙 ID가 처음부터 다시 쓰이므로 처리부 실행 구간 번호로 나눕니다.\n"
                      "상태: 진행(녹화 끝까지 빔이 나감), 종료(중간에 빔이 끊김), 확인 실패(추적 빔이 나간 적 없음).\n"
                      "행을 두 번 누르면 재생 위치를 그 트랙의 마지막 빔 직전으로 옮깁니다(그 실행 구간 안에서).").master.pack(
            side=tk.LEFT)
        self._tracks = Table(page, (
            ("run", "실행", 60, tk.CENTER), ("id", "ID", 60, tk.CENTER), ("start", "시작", 90, tk.E),
            ("life", "수명", 80, tk.E), ("beams", "추적 빔", 90, tk.E), ("misses", "미탐/최대연속", 140, tk.E),
            ("difference", "차이 평균", 110, tk.E), ("state", "상태", 100, tk.CENTER),
        ), height=12, on_double=self._on_track_double)
        self._tracks.frame.pack(fill=tk.BOTH, expand=True)

    # 세션

    def _session_text(self) -> str:
        if self._session is None:
            return GUIDE
        return "\n".join(self._session.describe())

    def set_session(self, session: Optional[Session]) -> None:
        new_session = session is not self._session
        self._session = session
        self._show_run_box(session is not None and bool(session.runs))
        if session is None:
            self._info.configure(text=NO_SESSION, foreground=TEXT)
            self._run_box.configure(values=())
            self._run_box.set("")
            self._set_page_titles()
            self._fill_stats()
            self._tracks.set_rows([])
            self._fill_bookmarks()
            self._fill_problems(force=True)
            self._refresh_events()
            return
        recording = session.recording
        text = session.path.name
        if recording is not None:
            # 녹화가 없으면 처리부 로그(탐지 CSV)로 만든 재생이다(io/log_replay.py).
            kind = "로그 재생(녹화 없음)" if session.is_log_replay else "녹화"
            text += f" · {kind} {recording.duration_s:.1f} s"
        if not session.runs:
            text += " · 처리부 로그 없음"
        # 자세한 내용은 (?)에 두지만, 놓치면 안 되는 경고는 한 줄에 남긴다(예전에는 세션 정보 4~5줄에 늘 보였다).
        warnings = []
        if recording is not None and recording.truncated:
            warnings.append("녹화 끝이 잘림")
        if session.player is not None and session.player.decode_errors:
            warnings.append(f"해석 못 한 프레임 {session.player.decode_errors}개")
        if recording is not None and recording.meta.get("gaps"):
            warnings.append(f"캡처 놓친 구간 {recording.meta['gaps']}")
        if session.errors:
            warnings.append(f"읽기 오류 {len(session.errors)}건")
        if warnings:
            text += " · ⚠ " + ", ".join(warnings) + " (? 참고)"
        self._info.configure(text=text, foreground=WARN if warnings else TEXT)
        runs = session.runs
        self._run_box.configure(values=[run_label(index, len(runs), run.label) for index, run in enumerate(runs)])
        if session.run is not None:
            self._run_box.current(runs.index(session.run))
        else:
            self._run_box.set("(처리부 로그 없음)")
        self._fill_stats()
        self._fill_tracks()
        self._fill_bookmarks()
        self._fill_problems(force=True)
        self._refresh_events()
        self._set_page_titles()
        if new_session:
            # 처음은 문제 확인. 녹화가 없으면(로그·CSV만) 재생 위치로 갈 수 없으므로 통계부터 보인다.
            # 같은 세션에서 처리부 실행만 바꾸면 보던 하위 탭을 그대로 둔다.
            self._problem_note.configure(text=PROBLEM_NOTE_EMPTY, foreground=MUTED)
            self._pages.select(self._check_page if session.player is not None else self._stats_page)

    def _show_run_box(self, show: bool) -> None:
        """처리부 실행 선택은 로그가 있는 세션에서만 보인다(실시간·녹화만 열었을 때 빈 상자를 두지 않음)."""
        if show and not self._run_box.winfo_manager():
            self._run_box.pack(side=tk.RIGHT)
            self._run_label.pack(side=tk.RIGHT, padx=(0, 6))
        elif not show and self._run_box.winfo_manager():
            self._run_box.pack_forget()
            self._run_label.pack_forget()

    def _set_page_titles(self) -> None:
        source = self._source_problems()
        counts = level_counts(source)
        total = counts["bad"] + counts["warn"]
        self._pages.tab(self._check_page, text=f" 문제 확인 ⚑ {total} " if total else " 문제 확인 ")
        report = self._session.report if self._session else None
        flagged = sum(item.level in ("warn", "bad") for item in report.items) if report is not None else 0
        self._pages.tab(self._stats_page, text=f" 통계 ▲ {flagged} " if flagged else " 통계 ")
        tracks = len(report.tracks) if report is not None else 0
        self._pages.tab(self._track_page, text=f" 트랙 {tracks} " if tracks else " 트랙 ")

    def _toggle_stats(self) -> None:
        self._stats_all = not self._stats_all
        self._stats_toggle.configure(text=STATS_FLAGGED_ONLY if self._stats_all else STATS_SHOW_ALL)
        self._fill_stats()

    def _fill_stats(self) -> None:
        """묶음 줄 아래에 항목을 단다. 기본은 주의·이상이 있는 묶음만 펼치고 그 항목만 보인다(1366×768에서 한 화면).

        [전체 항목 보기]면 모든 묶음을 펼치고 모든 항목을 보인다. 행 iid는 항목 번호(report.items 순서)다.
        """
        tree = self._stats.tree
        tree.delete(*tree.get_children(""))
        report = self._session.report if self._session else None
        if report is None:
            text = "녹화나 탐지 CSV가 필요합니다" if self._session is not None else "세션을 열면 계산합니다"
            tree.insert("", tk.END, iid="none", values=("통계 없음", text, "", ""), tags=("muted",))
        else:
            groups: Dict[str, List[int]] = {}
            for index, item in enumerate(report.items):
                groups.setdefault(item.group, []).append(index)
            for group, indexes in groups.items():
                items = [report.items[index] for index in indexes]
                bad = sum(item.level == "bad" for item in items)
                warn = sum(item.level == "warn" for item in items)
                summary = " · ".join(text for text in (f"이상 {bad}" if bad else "", f"주의 {warn}" if warn else "")
                                     if text) or "모두 정상"
                parent = f"group-{indexes[0]}"
                flagged = bool(bad or warn)
                tree.insert("", tk.END, iid=parent, values=(group, summary, f"{len(items)}개 항목", ""),
                            tags=("group",), open=flagged or self._stats_all)
                for index, item in zip(indexes, items):
                    if flagged and not self._stats_all and item.level not in ("warn", "bad"):
                        continue
                    tree.insert(parent, tk.END, iid=str(index), values=(
                        item.name, item.value, item.reference,
                        LEVEL_NAMES.get(item.level, "") if item.level != "idle" else ""),
                        tags=(item.level if item.level != "idle" else "text",))
        self._histogram.delete("all")
        self._selected_stat = None
        self._draw_histogram()
        self._note.configure(text="")

    def _fill_tracks(self) -> None:
        report = self._session.report if self._session else None
        rows = []
        if report is not None:
            for index, track in enumerate(report.tracks):
                if not track.confirmed:
                    state, tag = "확인 실패", "muted"
                elif track.ended:
                    state, tag = "종료", "warn" if track.max_consecutive_misses else "text"
                else:
                    state, tag = "진행", "ok"
                rows.append((str(index), (
                    track.segment + 1, f"#{track.track_id}", f"{track.first_ms / 1000:.1f} s",
                    f"{track.lifetime_s:.0f} s", track.tracking_beams,
                    f"{track.misses} / {track.max_consecutive_misses}",
                    "-" if track.mean_angle_error_m is None else f"{track.mean_angle_error_m:.1f} m", state,
                ), tag))
        self._tracks.set_rows(rows)

    def _on_stat_selected(self, iid: str) -> None:
        report = self._session.report if self._session else None
        if report is None or not iid.isdigit():
            return
        self._selected_stat = int(iid)
        self._note.configure(text=report.items[self._selected_stat].note)
        self._draw_histogram()

    def _draw_histogram(self) -> None:
        canvas = self._histogram
        canvas.delete("all")
        report = self._session.report if self._session else None
        if report is None or self._selected_stat is None:
            empty_message(canvas, "통계 항목을 누르면 값의 분포를 여기에 그립니다")
            return
        item = report.items[self._selected_stat]
        if item.chart == "frames":
            frame_request_chart(canvas, report.frames, title=f"탐색 프레임 {len(report.frames)}개")
            return
        if not item.samples:
            empty_message(canvas, "이 항목은 분포가 없습니다")
            return
        reference = HISTOGRAM_REFERENCES.get(item.name)
        if reference is None and item.reference:
            try:
                reference = float(item.reference.split()[0])
            except ValueError:
                reference = None
        histogram(canvas, item.samples, item.unit, reference,
                  LEVEL_COLORS.get(item.level, CHART_COLOR) if item.level != "idle" else CHART_COLOR,
                  f"{item.name} 분포 ({len(item.samples)}개)")

    def _on_track_double(self, iid: str) -> None:
        report = self._session.report if self._session else None
        if report is None:
            return
        track = report.tracks[int(iid)]
        # 처리부가 다시 시작한 녹화에서는 같은 처리부 시각이 실행마다 나오므로 그 트랙의 실행 구간 안에서 찾는다.
        self._focus_time(track.last_ms - 1000, track.track_id, None, track.segment)

    # 북마크

    @property
    def bookmarks(self) -> List[Bookmark]:
        return self._session.bookmarks if self._session is not None else []

    def _fill_bookmarks(self) -> None:
        rows = []
        for index, bookmark in enumerate(self.bookmarks):
            time_text = format_time(bookmark.start_ms) if not bookmark.is_range else (
                f"{format_time(bookmark.start_ms)}~{format_time(bookmark.end_ms)}")
            rows.append((str(index), (time_text, bookmark.tag, bookmark.title,
                                      "-" if bookmark.track_id is None else f"#{bookmark.track_id}"),
                         BOOKMARK_TAGS.get(bookmark.tag, "text")))
        if not rows:
            text = ("세션을 열면 북마크를 달 수 있습니다" if self._session is None else
                    "북마크 없음 — [+ 북마크]나 B 키로 추가")
            rows.append(("none", ("", "", text, ""), "muted"))
        self._bookmark_table.set_rows(rows)
        self._lists.tab(self._bookmark_page, text=f"북마크 ({len(self.bookmarks)})" if self.bookmarks else "북마크")
        self._bookmark_note.configure(text="")

    def _show_bookmark_menu(self, event: tk.Event) -> None:
        tree = self._bookmark_table.tree
        row = tree.identify_row(event.y)
        if row and row.isdigit():
            tree.selection_set(row)
        state = tk.NORMAL if self._selected_bookmark() is not None else tk.DISABLED
        self._bookmark_menu.entryconfigure("편집", state=state)
        self._bookmark_menu.entryconfigure("삭제", state=state)
        self._bookmark_menu.tk_popup(event.x_root, event.y_root)

    def _selected_bookmark(self) -> Optional[int]:
        selection = self._bookmark_table.tree.selection()
        if selection and selection[0].isdigit():
            return int(selection[0])
        return None

    def add_bookmark(self) -> None:
        """지금 재생 위치(없으면 빈 칸)로 북마크 추가 창을 연다."""
        if self._session is None:
            messagebox.showinfo("북마크", "녹화나 처리부 로그를 연 뒤에 북마크를 달 수 있습니다.",
                                parent=self.frame)
            return
        # 어느 탭에서 불렀든(B 키) 지금 재생 위치를 그때 묻는다. 분석 탭이 마지막으로 그려진 시각을 쓰면 안 된다.
        position = self._current_position()
        start_ms = position.processor_ms if position is not None else None

        def saved(bookmark: Bookmark) -> None:
            self._place(bookmark, position)
            self._add_saved(bookmark)

        BookmarkDialog(self.frame, saved, start_ms=start_ms, track_ids=self._track_ids(),
                       save_path=str(self._session.bookmark_file))

    def _place(self, bookmark: Bookmark, position: Optional[ReplayPosition] = None,
               recording_s: Optional[float] = None, segment: Optional[int] = None) -> None:
        """북마크에 녹화 위치와 처리부 실행 구간을 채운다(처리부 재시작이 있어도 위치가 하나로 정해지게).

        창에서 시각을 바꾸지 않았으면 열 때의 녹화 위치를 그대로 쓰고, 바꿨으면 같은 구간 안에서 처리부 시각으로 다시 찾는다.
        """
        player = self._session.player if self._session else None
        if position is not None:
            recording_s, segment = position.recording_s, position.segment
            if bookmark.start_ms != position.processor_ms:
                recording_s = None
        if player is None:
            return
        if segment is None and recording_s is not None:
            segment = player.segment_at(recording_s)
        bookmark.segment = segment
        bookmark.recording_s = (recording_s if recording_s is not None else
                                player.time_for_processor_ms(bookmark.start_ms, segment))
        bookmark.end_recording_s = (None if bookmark.end_ms is None else
                                    player.time_for_processor_ms(bookmark.end_ms, segment))

    def _track_ids(self) -> List[int]:
        report = self._session.report if self._session else None
        return sorted({track.track_id for track in report.tracks}) if report is not None else []

    def _add_saved(self, bookmark: Bookmark) -> None:
        self._store(self.bookmarks + [bookmark], bookmark)

    def _edit_selected_bookmark(self) -> None:
        index = self._selected_bookmark()
        if index is None or self._session is None:
            return
        original = self.bookmarks[index]

        def saved(bookmark: Bookmark) -> None:
            # 시각을 그대로 두었으면 원래 녹화 위치를, 바꿨으면 같은 처리부 실행 구간 안에서 다시 찾는다.
            same = bookmark.start_ms == original.start_ms
            self._place(bookmark, recording_s=original.recording_s if same else None, segment=original.segment)
            if same and bookmark.end_ms == original.end_ms:
                bookmark.end_recording_s = original.end_recording_s
            bookmarks = list(self.bookmarks)
            bookmarks[index] = bookmark
            self._store(bookmarks, bookmark)

        BookmarkDialog(self.frame, saved, track_ids=self._track_ids(), bookmark=original,
                       save_path=str(self._session.bookmark_file))

    def _delete_selected_bookmark(self) -> None:
        index = self._selected_bookmark()
        if index is None or self._session is None:
            return
        bookmark = self.bookmarks[index]
        if not messagebox.askyesno("북마크 삭제", f"'{bookmark.title}' 북마크를 지울까요?", parent=self.frame):
            return
        self._store([item for position, item in enumerate(self.bookmarks) if position != index], None)

    def _store(self, bookmarks: List[Bookmark], select: Optional[Bookmark]) -> None:
        """목록을 바꾸고 바로 파일에 쓴다. 쓰지 못하면 화면 목록은 바꾼 채로 두고 알린다."""
        session = self._session
        session.bookmarks = sort_bookmarks(bookmarks)
        try:
            session.save_bookmarks()
        except OSError as error:
            messagebox.showerror("북마크 저장 실패", f"{session.bookmark_file}\n{error}\n\n"
                                 "화면의 북마크는 남아 있지만 프로그램을 끄면 사라집니다.", parent=self.frame)
        self._fill_bookmarks()
        self._pages.select(self._check_page)
        self._lists.select(self._bookmark_page)
        if select is not None and select in session.bookmarks:
            self._bookmark_table.select(str(session.bookmarks.index(select)))
        self._draw_timeline()

    def _on_bookmark_selected(self, iid: str) -> None:
        if not iid.isdigit():
            return
        bookmark = self.bookmarks[int(iid)]
        self._bookmark_note.configure(text=bookmark.note or "(메모 없음)")
        self._focus_time(bookmark.start_ms, bookmark.track_id, bookmark.recording_s, bookmark.segment)

    # 문제 (자동 기록)

    def _source_problems(self) -> List[Problem]:
        if self._session is not None:
            return self._session.problems()
        return list(self._live_problems())

    def _filtered(self, problems: List[Problem]) -> List[Problem]:
        choice = self._problem_filter.get()
        if choice == "이상만":
            return [problem for problem in problems if problem.level == LEVEL_BAD]
        if choice in ("빔", "추적", "통신", "처리부 로그"):
            return [problem for problem in problems if problem.category == choice]
        return problems

    def _fill_problems(self, force: bool = False) -> None:
        source = self._source_problems()
        signature = (id(self._session), len(source),
                     tuple((problem.count, problem.level, problem.end_ms) for problem in source[-5:]))
        if not force and signature == self._problem_signature:
            return
        self._problem_signature = signature
        self._problems = self._filtered(source)
        rows = []
        for index, problem in enumerate(self._problems):
            time_text = format_time(problem.start_ms) if problem.start_ms is not None else "-"
            if problem.is_range:
                time_text += f" +{problem.duration_ms / 1000:.1f}s"
            level = "■ 이상" if problem.level == LEVEL_BAD else "▲ 주의"
            segment = f"[실행 {problem.segment + 1}] " if problem.segment else ""
            rows.append((str(index), (time_text, level, problem.category, segment + problem.title,
                                      problem.count), "bad" if problem.level == LEVEL_BAD else "warn"))
        if not rows:
            if self._session is None:
                text = "실시간으로 감지한 문제 없음" if not source else "고른 분류의 문제 없음"
            else:
                text = "문제 없음" if not source else "고른 분류의 문제 없음"
            rows.append(("none", ("", "", "", text, ""), "muted"))
        self._problem_table.set_rows(rows)
        counts = level_counts(source)
        title = f"문제 ({counts['bad'] + counts['warn']})" if source else "문제"
        self._lists.tab(self._problem_page, text=title)
        if self._session is not None and self._session.is_log_replay:
            origin = "처리부 로그로 만든 재생에서 다시 계산"
        else:
            origin = "녹화에서 다시 계산" if self._session is not None and self._session.player is not None else (
                "처리부 로그" if self._session is not None else "실시간")
        self._problem_summary.configure(text=f"이상 {counts['bad']} · 주의 {counts['warn']} · {origin}")
        self._set_page_titles()
        self._draw_timeline()

    def _selected_problem(self) -> Optional[Problem]:
        selection = self._problem_table.tree.selection()
        if selection and selection[0].isdigit() and int(selection[0]) < len(self._problems):
            return self._problems[int(selection[0])]
        return None

    def _on_problem_selected(self, iid: str) -> None:
        if not iid.isdigit() or int(iid) >= len(self._problems):
            return
        problem = self._problems[int(iid)]
        # 목록 열이 좁아 잘리는 내용도 여기서 다 보이게 한 줄 머리를 단다.
        level = "이상" if problem.level == LEVEL_BAD else "주의"
        when = format_time(problem.start_ms) if problem.start_ms is not None else "시각 없음"
        head = f"{when} · {level} · {problem.category} · {problem.title}"
        lines = [problem.detail] if problem.detail else []
        if problem.count > 1:
            lines.append(f"{problem.count}회 합침")
        if self._session is None:
            lines.append("실시간 기록 — 녹화해 둔 세션을 열면 이 위치로 이동할 수 있습니다")
        elif problem.start_s is None:
            lines.append("녹화가 없어 재생 위치로 이동할 수 없습니다")
        segment = self._timeline_segment()
        if segment is not None and problem.segment != segment:
            lines.append(f"실행 {problem.segment + 1} 구간의 문제라 타임라인에는 없습니다"
                         f"(위 '처리부 실행'을 실행 {problem.segment + 1}로 바꾸면 보임)")
        self._problem_note.configure(text=head + ("\n" + " · ".join(lines) if lines else ""), foreground=TEXT)
        if self._session is not None:
            recording_s = None if problem.start_s is None else max(problem.start_s - PROBLEM_LEAD_S, 0.0)
            lead_ms = None if problem.start_ms is None else problem.start_ms - int(PROBLEM_LEAD_S * 1000)
            self._focus_time(lead_ms, problem.track_id, recording_s, problem.segment)

    def _show_problem_menu(self, event: tk.Event) -> None:
        tree = self._problem_table.tree
        row = tree.identify_row(event.y)
        if row and row.isdigit():
            tree.selection_set(row)
        state = tk.NORMAL if self._selected_problem() is not None and self._session is not None else tk.DISABLED
        self._problem_menu.entryconfigure(0, state=state)
        self._problem_menu.tk_popup(event.x_root, event.y_root)

    def _bookmark_selected_problem(self) -> None:
        problem = self._selected_problem()
        if problem is None or self._session is None or problem.start_ms is None:
            return
        template = Bookmark(problem.start_ms, problem.title, end_ms=problem.end_ms if problem.is_range else None,
                            tag="이슈" if problem.level == LEVEL_BAD else "확인 필요", track_id=problem.track_id,
                            note=problem.detail)

        def saved(bookmark: Bookmark) -> None:
            same = bookmark.start_ms == problem.start_ms
            self._place(bookmark, recording_s=problem.start_s if same else None, segment=problem.segment)
            self._add_saved(bookmark)

        dialog = BookmarkDialog(self.frame, saved, track_ids=self._track_ids(), bookmark=template,
                                save_path=str(self._session.bookmark_file))
        dialog.window.title("문제를 북마크로 남기기")

    # 이벤트

    def _visible_events(self) -> List[LogEvent]:
        log = self._session.message_log if self._session else None
        if log is None:
            return []
        categories = EVENT_FILTERS[self._filter.get()]
        return [event for event in log.events if event.category in categories]

    def _refresh_events(self) -> None:
        self._events = self._visible_events()
        rows = []
        for index, event in enumerate(self._events):
            rows.append((str(index), (format_time(event.processor_ms), event.category, event.summary),
                         {"bad": "bad", "warn": "warn", "ok": "ok"}.get(event_level(event), "text")))
        if not rows:
            text = "메시지 로그 없음" if self._session is None or self._session.message_log is None else "해당 이벤트 없음"
            rows.append(("none", ("", "", text), "muted"))
        self._event_table.set_rows(rows)
        self._draw_timeline()

    def _on_event_selected(self, iid: str) -> None:
        if iid.isdigit():
            self._focus_event(self._events[int(iid)])

    def _on_timeline_click(self, event: tk.Event) -> None:
        hits = [item for item in self._problem_points if item[0] <= event.x <= item[2] and item[1] <= event.y <= item[3]]
        if hits:
            index = min(hits, key=lambda item: abs((item[0] + item[2]) / 2 - event.x))[4]
            self._lists.select(self._problem_page)
            self._problem_table.select(str(index))
            self._on_problem_selected(str(index))
            return
        for left, top, right, bottom, index in self._bookmark_points:
            if left - 4 <= event.x <= right + 4 and top <= event.y <= bottom:
                self._lists.select(self._bookmark_page)
                self._bookmark_table.select(str(index))
                self._on_bookmark_selected(str(index))
                return
        if not self._timeline_points:
            return
        x, y, index = min(self._timeline_points, key=lambda item: abs(item[0] - event.x) + abs(item[1] - event.y))
        if abs(x - event.x) <= 12 and abs(y - event.y) <= 16:
            self._event_table.select(str(index))
            self._focus_event(self._events[index])

    def refresh(self, state: Optional[MonitorState]) -> None:
        """재생 위치 선을 갱신한다. 세션이 없으면 실시간 문제 목록을 갱신한다."""
        if self._session is None:
            self._fill_problems()
        current = state.now_ms() if state is not None else None
        position = self._current_position() if state is not None else None
        segment = position.segment if position is not None else None
        if current != self._current_ms or segment != self._current_segment:
            self._current_ms = current
            self._current_segment = segment
            self._draw_timeline()

    def _timeline_segment(self) -> Optional[int]:
        """타임라인에 그릴 녹화의 처리부 실행 구간. 나눌 필요가 없거나 짝을 모르면 None(전부 그림).

        로그 실행 수와 녹화 구간 수가 같을 때만 순서대로 짝짓는다(Session.problems와 같은 추정).
        """
        session = self._session
        player = session.player if session else None
        if session is None or player is None or session.run is None or player.segment_count < 2:
            return None
        if len(session.runs) != player.segment_count:
            return None
        return session.runs.index(session.run)

    def _draw_timeline(self) -> None:
        canvas = self._timeline
        canvas.delete("all")
        width, height = canvas_size(canvas, 200, 100)
        self._timeline_points = []
        session = self._session
        segment = self._timeline_segment()
        self._timeline_title.configure(text="이벤트 타임라인" if segment is None else f"이벤트 타임라인 · 실행 {segment + 1}")
        all_events = session.message_log.events if session and session.message_log else []
        times = [event.processor_ms for event in all_events if event.processor_ms is not None]
        recording_range = session.player.processor_range_ms(segment) if session and session.player else None
        if recording_range:
            times.extend(recording_range)
        self._bookmark_points = []
        self._problem_points = []
        # (원래 목록 번호, 문제): 타임라인을 누르면 목록의 같은 행을 고르도록 번호를 지킨다.
        problems = [(index, problem) for index, problem in enumerate(self._problems if session is not None else [])
                    if segment is None or problem.segment == segment]
        for _, problem in problems:
            if problem.start_ms is not None:
                times.append(problem.start_ms)
        for bookmark in self._timeline_bookmarks(segment):
            times.append(bookmark.start_ms)
            if bookmark.end_ms is not None:
                times.append(bookmark.end_ms)
        if not times:
            empty_message(canvas, "처리부 메시지 로그를 열면 이벤트를 시간축에 보여줍니다")
            return
        low, high = min(times), max(times)
        if high - low < 1000:
            high = low + 1000
        # 줄 이름 폭과 맨 위 세 줄(녹화·북마크·문제) 높이는 글자 크기에 맞춘다(고정 80/14/16 px는 글자 16에서 겹쳤다).
        small = tkfont.nametofont(FONT_SMALL)
        line = small.metrics("linespace")
        label_width = max(small.measure(text) for text in (*TIMELINE_LANES, "북마크", "녹화", "문제")) + 16
        left, right = label_width, width - 12
        band = max(14, line)
        mark_lane = max(16, line + 2)
        problem_lane = max(16, line + 2)
        lanes_top = band + mark_lane + problem_lane
        lane_height = (height - 24 - lanes_top) / len(TIMELINE_LANES)

        def to_x(value: float) -> float:
            return left + (value - low) / (high - low) * (right - left)

        if recording_range:
            canvas.create_rectangle(to_x(recording_range[0]), 2, to_x(recording_range[1]), band - 2,
                                    fill=GRID_LINE, outline="")
            canvas.create_text(left - 8, band / 2, text="로그" if self._session is not None and self._session.is_log_replay
                               else "녹화", anchor="e", fill=MUTED, font=FONT_SMALL)
        for index, lane in enumerate(TIMELINE_LANES):
            top = lanes_top + index * lane_height
            canvas.create_rectangle(left, top + 1, right, top + lane_height - 1, fill=PANEL, outline="")
            canvas.create_text(left - 8, top + lane_height / 2, text=lane, anchor="e", fill=MUTED, font=FONT_SMALL)
        for tick in nice_ticks(low / 1000, high / 1000, max(3, int((right - left) / 100))):
            x = to_x(tick * 1000)
            canvas.create_line(x, height - 22, x, height - 18, fill=GRID_LINE)
            canvas.create_text(x, height - 17, text=format_time(tick * 1000)[:-4], anchor="n", fill=MUTED,
                               font=FONT_SMALL)
        visible = {id(event): index for index, event in enumerate(self._events)}
        colors = {"bad": BAD, "warn": WARN, "ok": OK, "info": TEXT}
        self._draw_bookmarks(canvas, to_x, left, band, mark_lane, height - 22, segment)
        self._draw_problem_lane(canvas, problems, to_x, left, right, band + mark_lane, problem_lane)
        placer = LabelPlacer(canvas, (left, lanes_top, right, height - 22))
        for event in all_events:
            if event.processor_ms is None or event.category not in TIMELINE_LANES:
                continue
            lane = TIMELINE_LANES.index(event.category)
            x = to_x(event.processor_ms)
            y = lanes_top + lane * lane_height + lane_height / 2
            color = colors[event_level(event)]
            selected = id(event) in visible
            radius = 5 if selected else 3
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color if selected else PANEL,
                               outline=color, width=2)
            if selected:
                self._timeline_points.append((x, y, visible[id(event)]))
                if event.category == CATEGORY_REJECTED:
                    placer.place(x, y, f"#{event.details.get('requestId', '?')}", gap=7, color=TEXT)
        same_segment = segment is None or self._current_segment is None or self._current_segment == segment
        if self._current_ms is not None and same_segment and low <= self._current_ms <= high:
            x = to_x(self._current_ms)
            canvas.create_line(x, 0, x, height - 22, fill=TEXT, width=2)

    def _draw_problem_lane(self, canvas: tk.Canvas, problems: List[Tuple[int, Problem]],
                           to_x: Callable[[float], float], left: float, right: float, top: float, lane: float) -> None:
        """타임라인 '문제' 줄: 자동 기록한 문제를 수준 색으로. 구간은 막대, 한 시점은 짧은 세로 막대.

        problems는 (목록 번호, 문제). 고른 실행 구간의 문제만 오지만 번호는 목록 그대로다.
        """
        canvas.create_text(left - 8, top + lane / 2, text="문제", anchor="e", fill=MUTED, font=FONT_SMALL)
        canvas.create_rectangle(left, top + 1, right, top + lane - 1, fill=PANEL, outline="")
        selected = self._selected_problem()
        for index, problem in problems:
            if problem.start_ms is None:
                continue
            color = BAD if problem.level == LEVEL_BAD else WARN
            x0 = to_x(problem.start_ms)
            x1 = max(to_x(problem.end_ms), x0 + 3) if problem.end_ms is not None else x0 + 3
            outline = TEXT if problem is selected else ""
            canvas.create_rectangle(x0, top + 3, x1, top + lane - 3, fill=color, outline=outline,
                                    width=2 if outline else 1)
            self._problem_points.append((x0 - 2, top, x1 + 2, top + lane, index))

    def _timeline_bookmarks(self, segment: Optional[int]) -> List[Bookmark]:
        return [bookmark for bookmark in self.bookmarks
                if segment is None or bookmark.segment is None or bookmark.segment == segment]

    def _draw_bookmarks(self, canvas: tk.Canvas, to_x: Callable[[float], float], left: float, band: float,
                        lane: float, bottom: float, segment: Optional[int] = None) -> None:
        """타임라인 맨 위 '북마크' 줄: 한 시점은 ▼와 세로 점선, 구간은 막대와 옅은 띠. 번호는 목록 순서(B1, B2…).

        segment가 있으면 그 실행 구간의 북마크만 그린다(구간을 모르는 북마크는 그린다).
        """
        canvas.create_text(left - 8, band + lane / 2, text="북마크", anchor="e", fill=MUTED, font=FONT_SMALL)
        middle = band + lane / 2
        shown = self._timeline_bookmarks(segment)
        for number, bookmark in enumerate(self.bookmarks, 1):
            if bookmark not in shown:
                continue
            color = BOOKMARK_COLORS.get(bookmark.tag, MUTED)
            x0 = to_x(bookmark.start_ms)
            if bookmark.end_ms is not None:
                x1 = max(to_x(bookmark.end_ms), x0 + 3)
                # 줄 바탕(회색) 위, 이벤트 점 아래에 오도록 줄 바탕을 그린 뒤·이벤트를 그리기 전에 그린다.
                canvas.create_rectangle(x0, band + lane, x1, bottom, fill=blend(color, 0.12, BACKGROUND), outline="")
                canvas.create_rectangle(x0, band + 4, x1, band + lane - 4, fill=color, outline="")
                label_x = x1 + 3
                box = (x0, band, x1, bottom)
            else:
                canvas.create_line(x0, band + lane, x0, bottom, fill=color, dash=(3, 3))
                canvas.create_polygon(x0, band + lane - 2, x0 - 6, band + 3, x0 + 6, band + 3, fill=color, outline="")
                label_x = x0 + 8
                box = (x0 - 6, band, x0 + 6, band + lane)
            canvas.create_text(label_x, middle, text=f"B{number}", anchor="w", fill=color, font=FONT_SMALL)
            self._bookmark_points.append((*box, number - 1))


def stat_colors() -> Dict[str, str]:
    return dict(LEVEL_COLORS)


