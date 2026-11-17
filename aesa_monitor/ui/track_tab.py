"""추적 탭 — "표적을 잘 따라가고 있나?" (실시간 감시)

왼쪽: 전시기(부채꼴 PPI). 거리 고리, 방위 눈금, 추적 영역, 탐색·확인·추적 빔 부채꼴, 트랙 위치·꼬리·속도 벡터.
오른쪽 위: 트랙별 상태 줄. 사실 값만(최근 결과, 연속 미탐, 빔 안 위치, 마지막 갱신 뒤 지난 시간).
오른쪽 아래: 고른 트랙의 잔차(예측-측정) 추이.
사이클 상세(① 예측 ② 관측·연관 ③ 결과, 빔 단면)는 모니터 추정이 섞여 있어 접어 두고, 열면 전시기 칸만 덮는다(사후 분석용).

처리부 내부 값(예측 거리, 공분산, NDS 점수)은 받지 않으므로 잔차와 연관 탐지는 모니터의 추정이다.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Sequence, Tuple

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.geometry import wrap_degrees
from aesa_monitor.core.protocol import BeamType
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.tracking import (
    BEAM_OFFSET_WARN, OUTCOME_NAMES, Outcome, TrackStatusRow, TrackingCycle, beam_offset_level, track_position,
    track_status_rows,
)
from aesa_monitor.core.verdict import track_verdict
from aesa_monitor.ui.beam_tab import COVERAGE_AZIMUTH
from aesa_monitor.ui.style import (
    ACCENT, ACCENT_SOFT, BACKGROUND, BAD, BEAM_COLORS, BORDER, CANDIDATE_COLOR, DETECTED_COLOR, ESTIMATE_COLOR, FONT_BODY,
    FONT_SMALL, FONT_TITLE, FONT_VERDICT, GRID_LINE, LEVEL_COLORS, MUTED, OK, PANEL, PREDICTED_COLOR, TEXT, UNKNOWN,
    WARN, beam_name, blend, format_number, format_time, track_color,
)
from aesa_monitor.ui.widgets import (
    ChartArea, HelpButton, LabelPlacer, Tooltip, VerdictBar, canvas_size, empty_message, nice_ticks, section_title,
    text_height, text_width,
)

OUTCOME_COLORS = {Outcome.PENDING: UNKNOWN, Outcome.UPDATED: OK, Outcome.MISSED: WARN, Outcome.ENDED: BAD}
MAP_TRAIL_CYCLES = 30
TREND_CYCLES = 60            # 추이 그래프 사이클 수
SECTOR_HALF_SPAN = 70.0      # 섹터 보기: 레이더 정면 ±70°
SEARCH_GLOW_MS = 50          # 탐색 빔 잔상. 10 ms마다 약 4°씩 움직여 길게 두면 넓은 부채꼴이 화면을 덮는다
REQUEST_GLOW_MS = 2000       # 확인·추적 빔 잔상
VECTOR_SECONDS = 60          # 속도 벡터 길이(이 시간 뒤 위치)
RESIDUAL_AXIS_GATES = 3.0    # 잔차 세로축 위쪽 한계 = 게이트 하한 × 이 값. 넘는 점은 위 끝에 ▲로 그린다
STATUS_ROWS = 4              # 상태 줄에 한 번에 보이는 행 수(넘치면 휠)
STATUS_ROWS_SMALL = 3        # 오른쪽 칸이 낮을 때
COVERAGE_FILL = blend(MUTED, 0.06, PANEL)

HELP_MAP = (
    "레이더를 가운데 두고 위에서 본 그림(PPI)입니다.\n"
    "• 섹터: 레이더 정면(yaw)을 위로 두고 ±70°만 그립니다(같은 크기에서 더 크게 보임). 섹터 밖 트랙은 아래에 개수만 적습니다.\n"
    "• 360°: 북쪽을 위로 둔 전체 원판입니다.\n\n"
    "• 점선: 추적 영역(확인·추적 빔을 보낼 수 있는 안테나 기준 방위 ±60°). 360° 보기에서는 옅게 칠합니다.\n"
    "• 부채꼴: 탐색(파랑, 빔 폭 6°)·확인(노랑 3°)·추적(초록 3°) 빔. 최근일수록 진합니다. "
    f"탐색은 최근 {SEARCH_GLOW_MS} ms, 확인·추적은 최근 {REQUEST_GLOW_MS / 1000:g}초를 그립니다.\n"
    f"• ■ 트랙(처리부 트랙 목록 위치), 꼬리는 최근 {MAP_TRAIL_CYCLES}사이클의 갱신 위치, "
    f"선은 {VECTOR_SECONDS}초 뒤 위치(속도 벡터). 글자는 #ID 거리(경사 거리)·속력입니다. × 는 종료된 트랙입니다.\n"
    "• 고각은 표현하지 않습니다(빔 탭의 빔 방향 지도에서 봅니다).\n\n"
    "트랙을 누르면 그 트랙을 고릅니다."
)
HELP_STATUS = (
    "트랙마다 처리부 결과와 실제 빔·탐지에서 바로 나오는 값만 보여줍니다.\n\n"
    "• 최근 결과: 오른쪽이 최신. ● 갱신, × 미탐, ■ 종료, ○ 결과 대기. 처리부 트랙 목록의 트랙 시각이 "
    "빔 송신 시각과 같으면 갱신입니다. 칸이 좁으면 최근 것만 그립니다.\n"
    "• 연속 미탐: 지금까지 이어진 미탐 수 / 한도(TARGET_MAX_CONSECUTIVE_TRACKING_MISSES). 한도에 닿으면 트랙이 "
    "지워집니다. 한도−1 이상이면 빨강, 1 이상이면 주황입니다.\n"
    "• 빔 안 위치: 마지막 확인·추적 빔에서 탐지가 빔 중심(0)에서 가장자리(1)까지 어디에 있었는지. 모의기의 빔 안 판정식 "
    "(2Δ방위/폭)² + (2Δ고각/폭)² ≤ 1 과 같은 값입니다. 처리부가 실제로 쏜 빔과 모의기 실제 탐지의 기하 관계라 추정이 "
    "거의 없습니다(탐지가 여러 개면 빔 축에 가장 가까운 것을 씁니다). 1에 가까워지면 다음 빔에서 표적이 빔 밖으로 나가 "
    "미탐이 날 수 있습니다. 1을 넘으면 빨강"
    + (f", {BEAM_OFFSET_WARN:g} 이상이면 주황(임시 기준, 실제 연동 데이터로 정할 값)" if BEAM_OFFSET_WARN else "")
    + "입니다. 막대의 세로 눈금이 1(빔 가장자리)입니다.\n"
    "• 마지막 갱신: 마지막으로 갱신된 뒤 지난 처리부 시간입니다.\n\n"
    "순서: 문제 있는 트랙(미탐 중, 마지막 빔에 탐지 없음, 빔 안 위치 주의 이상. 연속 미탐 많은 순) → 나머지 트랙(ID 순) → "
    "최근 1분 안에 종료된 트랙(3개까지). 문제가 생긴 트랙은 위로 올라옵니다.\n"
    f"한 번에 {STATUS_ROWS}행(창이 낮으면 {STATUS_ROWS_SMALL}행)을 보이고, 나머지는 마우스 휠로 넘깁니다. "
    "넘겨서 가려진 행에 문제 트랙이 있으면 아래에 '⚠ 가려진 문제 트랙 n개'를 띄웁니다. 행을 누르면 그 트랙을 고릅니다."
)
HELP_TREND = (
    f"고른 트랙의 최근 {TREND_CYCLES}사이클 잔차입니다. 가로축은 처리부 시각(초)입니다.\n\n"
    "잔차 = 처리부 예측 방향(빔 축)과 모의기 탐지 사이 수직 거리(m). 예측(추정)과 측정(잡음 포함)의 차이이며 "
    "참값 대비 오차가 아닙니다.\n"
    "• 점선: 연관 게이트 하한(√NDS 임계값 × 측정 잡음, 값은 오른쪽 여백). 이보다 아래에서 오르내리는 것은 측정 잡음 수준이고, "
    "잔차가 게이트 쪽으로 올라가는 추세가 경고 신호입니다. 실제 게이트는 처리부 공분산(P)만큼 이보다 넓습니다.\n"
    f"• 세로축은 게이트 하한의 {RESIDUAL_AXIS_GATES:g}배까지만 그립니다. 트랙 시작 직후처럼 이보다 큰 값은 위 끝에 ▲로 "
    "그리고, 개수와 최댓값을 오른쪽 위에 적습니다.\n"
    "• × 미탐(주황): 탐지가 있었는데 연관되지 않았으면 그 잔차 자리에, 탐지가 없었으면 0에 그립니다. "
    "축보다 크면 위 끝에 주황 ▲입니다. 탐지가 여러 개면 빔 축에 가장 가까운 것을 씁니다(모니터 추정).\n\n"
    "빔 안 위치(빔 폭 기준으로 나눈 값)는 따로 그리지 않고 상태 줄 막대로 봅니다. 빔 안 위치 ≈ 잔차 ÷ (거리 × tan(빔 폭/2))라 "
    "같은 정보이고, 정상일 때는 0 근처에 붙어 있어 그래프로는 읽을 것이 적습니다.\n\n"
    "그래프를 누르면 그 사이클을 사이클 상세 패널(전시기 자리)에서 보여주고, 세로 점선으로 표시합니다."
)
DETAIL_NOTE = "모니터 추정 포함 · 사후 분석용"


def algorithm_text(p: TrackingParameters) -> str:
    gate_m = math.sqrt(p.position_nds_threshold) * p.measurement_standard_deviation_m
    return (
        "처리부의 추적 계산 (설정값은 .env의 TARGET_* 값)\n\n"
        "1. 확인\n"
        f"탐색 빔에서 표적을 찾으면 {p.confirmation_delay_ms} ms 뒤 같은 방향으로 확인 빔을 보낸다. "
        f"두 탐지 사이 속력 ≤ {p.max_association_speed_mps:g} m/s, 도플러 차 ≤ "
        f"{p.max_initial_doppler_difference_mps:g} m/s이면 트랙을 만든다.\n\n"
        "2. 예측\n"
        "등가속도 칼만 필터(위치·속도·가속도)로 다음 송신 시각"
        f"(+{p.tracking_interval_ms} ms)의 위치를 예측하고 그 방향으로 추적 빔을 보낸다. "
        f"가속도 잡음 σ = {p.process_acceleration_standard_deviation_mps2:g} m/s².\n\n"
        "3. 연관\n"
        "잔차 r = 탐지 − 예측 위치, NDS = rᵀ(P + R)⁻¹r. "
        f"NDS ≤ {p.position_nds_threshold:g}인 탐지 중 가장 작은 것을 고른다. "
        f"R = ({p.measurement_standard_deviation_m:g} m)² 등방이므로 P가 0이어도 게이트 반경은 약 {gate_m:.0f} m다.\n\n"
        "4. 갱신\n칼만 이득으로 예측과 탐지를 섞어 상태와 공분산을 갱신한다.\n\n"
        "5. 미탐\n"
        f"연관된 탐지가 없으면 마지막 상태로 계속 예측하고, 연속 {p.max_consecutive_tracking_misses}회면 "
        "트랙을 삭제한다. 결과가 오지 않는 트랙도 같은 시간이 지나면 삭제한다.\n\n"
        "모니터 표시의 한계\n"
        "처리부는 예측 거리·공분산·NDS를 보내지 않는다. 그래서 예측은 빔 방향뿐이고, "
        "'잔차'는 빔 축과 탐지 사이의 수직 거리(각도 차이 × 탐지 거리)다. 참값 대비 오차가 아니다. "
        "연관된 탐지는 빔 축에 가장 가까운 탐지로 추정하므로 처리부가 고른 것과 다를 수 있다.\n"
        "미탐·갱신은 트랙 목록의 트랙 시각이 빔 송신 시각과 같은지로 판단한다(사실 값)."
    )


# 결과 기호 ---------------------------------------------------------------------

def draw_outcome(canvas: tk.Canvas, x: float, y: float, outcome: str, size: float) -> None:
    """결과 기호 하나를 (x, y) 가운데에 그린다. ● 갱신, × 미탐, ■ 종료, ○ 대기."""
    half = size / 2
    color = OUTCOME_COLORS[outcome]
    if outcome == Outcome.UPDATED:
        canvas.create_oval(x - half, y - half, x + half, y + half, fill=color, outline="")
    elif outcome == Outcome.MISSED:
        canvas.create_line(x - half, y - half, x + half, y + half, fill=color, width=2)
        canvas.create_line(x - half, y + half, x + half, y - half, fill=color, width=2)
    elif outcome == Outcome.ENDED:
        canvas.create_rectangle(x - half, y - half, x + half, y + half, fill=color, outline="")
    else:
        canvas.create_oval(x - half, y - half, x + half, y + half, fill="", outline=color, width=1)


# 트랙별 상태 줄 -----------------------------------------------------------------

class TrackStatusList:
    """트랙별 상태 줄(캔버스). 행을 누르면 on_select(track_id)."""

    def __init__(self, parent: tk.Widget, on_select: Callable[[int], None]) -> None:
        self.canvas = tk.Canvas(parent, background=BACKGROUND, highlightthickness=0, height=120)
        self._on_select = on_select
        self._row_boxes: List[Tuple[float, float, int]] = []
        self._first = 0
        self._row_count = 0
        self._visible = 0
        self._ensured: Optional[int] = None
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", lambda event: self._scroll(-1 if event.delta > 0 else 1))
        self.canvas.bind("<Button-4>", lambda _event: self._scroll(-1))
        self.canvas.bind("<Button-5>", lambda _event: self._scroll(1))

    def _click(self, event: tk.Event) -> None:
        for top, bottom, track_id in self._row_boxes:
            if top <= event.y < bottom:
                self._on_select(track_id)
                return

    def _scroll(self, delta: int) -> None:
        self._first = max(0, min(self._first + delta, max(self._row_count - self._visible, 0)))

    @staticmethod
    def row_height() -> int:
        return max(text_height(FONT_TITLE), text_height(FONT_BODY)) + 8

    @staticmethod
    def header_height() -> int:
        return text_height(FONT_SMALL) + 6

    def height_for(self, visible_rows: int, overflow: bool) -> int:
        return self.header_height() + 4 + self.row_height() * max(visible_rows, 1) + (
            text_height(FONT_SMALL) + 6 if overflow else 0)

    def draw(self, rows: Sequence[TrackStatusRow], selected: Optional[int], limit: int, visible_rows: int) -> None:
        """visible_rows개 행을 보인다. 넘치면 아래 한 줄에 '▲ n · ▼ n개 더'를 쓰고 휠로 넘긴다."""
        canvas = self.canvas
        canvas.delete("all")
        width = canvas_size(canvas, 200, 60)[0]
        row_height, header = self.row_height(), self.header_height()

        self._row_count = len(rows)
        visible = self._visible = min(len(rows), visible_rows)
        # 고른 트랙이 바뀌었을 때만 그 행이 보이게 넘긴다(휠로 넘긴 위치를 매번 되돌리지 않게).
        if selected != self._ensured:
            self._ensured = selected
            index = next((i for i, row in enumerate(rows) if row.track_id == selected), None)
            if index is not None and not self._first <= index < self._first + visible:
                self._first = index if index < self._first else index - visible + 1
        self._first = max(0, min(self._first, max(len(rows) - visible, 0)))
        shown = rows[self._first:self._first + visible]
        more = len(rows) - len(shown)
        desired = self.height_for(len(shown), bool(more))
        if abs(int(canvas.cget("height")) - desired) > 1:
            canvas.configure(height=desired)

        # 열 너비는 글자 크기에 맞춰 잰다. 결과 점이 10개 안 들어가면 머리글을 줄인다.
        gap = 10
        mark = max(8, round(text_height(FONT_SMALL) * 0.42))
        step = mark + 4
        bar_width = max(text_width("0.00") + 16, 44)
        for titles in (("트랙", "연속 미탐", "빔 안 위치", "마지막 갱신"), ("트랙", "미탐", "빔 위치", "갱신 후")):
            id_width = 16 + max(text_width("#88", FONT_TITLE), text_width(titles[0]))
            miss_width = max(text_width(titles[1]), text_width(f"{limit}/{limit}", FONT_TITLE))
            offset_width = max(text_width(titles[2]), bar_width + 6 + text_width("0.00"), text_width("탐지 없음"))
            last_width = max(text_width(titles[3]), text_width("99.9 s 전", FONT_BODY))
            fixed = 6 + id_width + gap + miss_width + gap + offset_width + gap + last_width + 4
            dots_width = width - fixed - gap
            if dots_width >= step * 10:
                break
        dot_count = max(int(dots_width // step), 0)
        x_id = 6
        x_dots = x_id + id_width + gap
        x_miss = x_dots + dots_width + gap
        x_offset = x_miss + miss_width + gap
        x_last = x_offset + offset_width + gap

        dots_title = next((title for title in ("최근 결과 (→ 최신)", "최근 결과 →", "결과 →", "결과")
                           if text_width(title) <= dots_width), "")
        for text, x in ((titles[0], x_id), (dots_title, x_dots), (titles[1], x_miss), (titles[2], x_offset),
                        (titles[3], x_last)):
            canvas.create_text(x, 2, text=text, anchor="nw", fill=MUTED, font=FONT_SMALL)
        canvas.create_line(0, header, width, header, fill=GRID_LINE)

        self._row_boxes = []
        if not rows:
            canvas.create_text(width / 2, header + 4 + row_height / 2, text="트랙 없음", fill=MUTED, font=FONT_BODY)
            return
        y = header + 2
        for row in shown:
            middle = y + row_height / 2
            if row.track_id == selected:
                canvas.create_rectangle(0, y, width, y + row_height, fill=ACCENT_SOFT, outline="")
            color = track_color(row.track_id) if row.alive else CANDIDATE_COLOR
            canvas.create_rectangle(x_id, middle - 5, x_id + 10, middle + 5, fill=color, outline="")
            # 종료는 회색 이름, 끝의 ■, 마지막 열 '종료'로 보인다.
            canvas.create_text(x_id + 16, middle, text=f"#{row.track_id}",
                               anchor="w", fill=TEXT if row.alive else MUTED, font=FONT_TITLE)

            # 최근 결과: 오른쪽이 최신. 종료된 트랙의 마지막 사이클이 갱신이면(결과 시간 초과 등) 끝에 ■를 붙인다.
            outcomes = list(row.outcomes)
            if not row.alive and (not outcomes or outcomes[-1] != Outcome.ENDED):
                outcomes.append(Outcome.ENDED)
            outcomes = outcomes[-dot_count:] if dot_count else []
            x = x_dots + dots_width - step * len(outcomes) + step / 2
            for outcome in outcomes:
                draw_outcome(canvas, x, middle, outcome, mark)
                x += step

            misses = row.consecutive_misses
            level = "bad" if misses >= max(limit - 1, 1) else ("warn" if misses else "")
            canvas.create_text(x_miss, middle, text=f"{misses}/{limit}", anchor="w",
                               fill=LEVEL_COLORS[level] if level else (TEXT if row.alive else MUTED),
                               font=FONT_TITLE)

            # 빔 안 위치 막대: 0~1.2, 세로 눈금 = 1(빔 가장자리). 값이 없으면 막대 대신 이유를 쓴다.
            level = beam_offset_level(row.beam_offset)
            if row.beam_offset is not None:
                top, bottom = middle - 4, middle + 4
                canvas.create_rectangle(x_offset, top, x_offset + bar_width, bottom, fill=PANEL, outline=GRID_LINE)
                if BEAM_OFFSET_WARN:
                    warn_x = x_offset + bar_width * BEAM_OFFSET_WARN / 1.2
                    canvas.create_line(warn_x, middle - 5, warn_x, middle + 5, fill=WARN, dash=(2, 2))
                edge = x_offset + bar_width / 1.2
                canvas.create_line(edge, middle - 7, edge, middle + 7, fill=MUTED)
                filled = x_offset + min(row.beam_offset, 1.2) / 1.2 * bar_width
                bar_color = {"bad": BAD, "warn": WARN}.get(level, PREDICTED_COLOR)
                canvas.create_rectangle(x_offset, top, max(filled, x_offset + 1), bottom, fill=bar_color, outline="")
                canvas.create_text(x_offset + bar_width + 6, middle, text=f"{row.beam_offset:.2f}", anchor="w",
                                   fill={"bad": BAD, "warn": WARN}.get(level, TEXT), font=FONT_SMALL)
            else:
                canvas.create_text(x_offset, middle, text=row.offset_note or "-", anchor="w",
                                   fill=WARN if row.offset_note == "탐지 없음" else MUTED, font=FONT_SMALL)

            if row.alive:
                since = "-" if row.since_update_ms is None else f"{row.since_update_ms / 1000:.1f} s 전"
                canvas.create_text(x_last, middle, text=since, anchor="w", fill=TEXT, font=FONT_BODY)
            else:
                canvas.create_text(x_last, middle, text="종료", anchor="w", fill=MUTED, font=FONT_BODY)
            self._row_boxes.append((y, y + row_height, row.track_id))
            y += row_height
        if more:
            above = self._first
            below = more - above
            parts = [f"▲ {above}개" if above else "", f"▼ {below}개" if below else ""]
            problems = sum(row.problem for row in rows) - sum(row.problem for row in shown)
            warning = f"⚠ 가려진 문제 트랙 {problems}개" if problems else ""
            room = width - x_id - (text_width(warning) + 16 if warning else 0)
            head = " · ".join(part for part in parts if part)
            text = next((item for item in (head + " 더 — 마우스 휠로 넘기기", head + " 더 (휠)", head)
                         if text_width(item) <= room), head)
            canvas.create_text(x_id, y + 3, anchor="nw", fill=MUTED, font=FONT_SMALL, text=text)
            if problems:
                # 가려진 행에 문제 트랙이 있으면 알린다(문제 트랙은 위로 올리지만 휠로 넘겼을 수 있다).
                canvas.create_text(width - 4, y + 3, anchor="ne", fill=WARN, font=FONT_SMALL, text=warning)


# 사이클 상세 창 -----------------------------------------------------------------

class StepCard:
    """사이클 한 단계: 색 제목, 큰 값, 설명 줄."""

    def __init__(self, parent: tk.Widget, title: str, color: str) -> None:
        self.frame = tk.Frame(parent, background=PANEL, highlightthickness=2, highlightbackground=color)
        self._title = tk.Label(self.frame, text=title, font=FONT_TITLE, background=color, foreground=BACKGROUND,
                               anchor="w", padx=8, pady=3)
        self._title.pack(fill=tk.X)
        # 큰 값 글꼴(본문 × 1.75)은 전시기 칸 안의 카드 세 개에서 '350.82° / 12.35°'가 잘려 판정 글꼴(본문 + 3)을 쓴다.
        self._value = tk.Label(self.frame, text="-", font=FONT_VERDICT, background=PANEL, foreground=TEXT,
                               anchor="w", padx=8)
        self._value.pack(fill=tk.X)
        self._lines = tk.Label(self.frame, text="", font=FONT_SMALL, background=PANEL, foreground=TEXT,
                               anchor="nw", justify=tk.LEFT, padx=8, pady=4)
        self._lines.pack(fill=tk.BOTH, expand=True)
        self.frame.bind("<Configure>", lambda event: self._lines.configure(wraplength=max(event.width - 20, 80)))

    def set(self, value: str, lines: List[str], value_color: str = TEXT) -> None:
        self._value.configure(text=value, foreground=value_color)
        self._lines.configure(text="\n".join(line for line in lines if line))


class CycleDetailPanel:
    """사이클 상세(① 예측 → ② 관측·연관 → ③ 결과, 빔 단면). 모니터 추정이 섞여 있어 사후 분석용으로 접어 둔다.

    추적 탭 안에서 전시기 칸만 덮는 패널로 띄운다(별도 창은 다른 프로그램처럼 보였다). 오른쪽 상태 줄과 잔차 그래프는
    그대로 보이므로 그래프를 눌러 사이클을 옮겨 가며 볼 수 있다. ✕나 Esc로 닫는다.
    """

    def __init__(self, area: tk.Widget, parameters: TrackingParameters, on_change: Callable[[], None]) -> None:
        self._area = area
        self._parameters = parameters
        self._on_change = on_change
        self.track_id: Optional[int] = None
        self.cycle_time_ms: Optional[int] = None
        self._cycle_index: Optional[int] = None
        self._follow_latest = tk.BooleanVar(master=area, value=True)
        # 덮는 패널임을 알 수 있게 테두리를 두른다.
        frame = self.frame = tk.Frame(area, background=BACKGROUND, highlightthickness=2,
                                      highlightbackground=ACCENT, highlightcolor=ACCENT)
        header = tk.Frame(frame, background=ACCENT, padx=10, pady=4)
        header.pack(fill=tk.X)
        self._title = tk.Label(header, text="사이클 상세", font=FONT_TITLE, background=ACCENT, foreground=BACKGROUND)
        self._title.pack(side=tk.LEFT)
        HelpButton(header, "사이클 상세", (
            "고른 트랙의 사이클 하나를 처리부 추적 계산 순서로 보여줍니다.\n\n"
            "① 예측: 처리부가 보낸 빔 방향(사실 값).\n"
            "② 관측·연관: 모의기 탐지와 빔 축 사이 차이. 탐지가 여러 개면 빔 축에 가장 가까운 것을 연관 탐지로 "
            "추정합니다(처리부는 공분산을 쓴 NDS로 고르므로 다를 수 있음). 차이는 예측(추정)과 측정(잡음 포함)의 "
            "차이이며 참값 대비 오차가 아닙니다.\n"
            "③ 결과: 처리부 트랙 목록의 갱신·미탐·종료(사실 값).\n\n"
            "실시간 판단은 오른쪽 상태 줄(사실 값)로 하고, 이 패널은 문제가 난 사이클을 따져 볼 때 씁니다. "
            "오른쪽 잔차 그래프를 누르면 그 사이클로 옮깁니다(세로 점선이 지금 보는 사이클). ✕나 Esc로 닫으면 전시기가 다시 보입니다."
        ), background=ACCENT).pack(side=tk.LEFT, padx=6)
        close = tk.Label(header, text="✕ 닫기", font=FONT_BODY, background=ACCENT, foreground=BACKGROUND,
                         cursor="hand2", padx=4)
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda _event: self.close())

        body = tk.Frame(frame, background=BACKGROUND, padx=10, pady=6)
        body.pack(fill=tk.BOTH, expand=True)
        navigation = tk.Frame(body, background=BACKGROUND)
        navigation.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(navigation, text="◀", width=3, command=lambda: self._step(-1)).pack(side=tk.LEFT)
        ttk.Button(navigation, text="▶", width=3, command=lambda: self._step(1)).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(navigation, text="최신 따라가기", variable=self._follow_latest,
                        command=self._on_change).pack(side=tk.LEFT, padx=8)
        self._cycle_label = tk.Label(navigation, text="", font=FONT_TITLE, background=BACKGROUND, foreground=TEXT,
                                     anchor="w", justify=tk.LEFT)
        self._cycle_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._cycle_label.bind("<Configure>", lambda event: self._cycle_label.configure(
            wraplength=max(event.width - 2, 80)))

        cards = tk.Frame(body, background=BACKGROUND)
        cards.pack(fill=tk.X)
        self._predict_card = StepCard(cards, "① 예측 (처리부 빔)", PREDICTED_COLOR)
        self._observe_card = StepCard(cards, "② 관측·연관 (모의기)", DETECTED_COLOR)
        self._update_card = StepCard(cards, "③ 결과 (처리부 트랙)", ESTIMATE_COLOR)
        for index, card in enumerate((self._predict_card, self._observe_card, self._update_card)):
            card.frame.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0))
            cards.columnconfigure(index, weight=1, uniform="step")

        section_title(body, "빔 단면 — 예측 방향을 가운데 두고 본 탐지 (m)",
                      "예측 빔 방향(가운데 ○)을 기준으로 탐지(●)와 갱신 결과(■)가 어디 있는지 "
                      "미터 단위로 그립니다. 오른쪽이 방위 증가, 위쪽이 고각 증가 방향입니다. "
                      "점선 원은 연관 게이트 하한입니다.")
        self._cross = tk.Canvas(body, background=BACKGROUND, highlightthickness=0, height=120)
        self._cross.pack(fill=tk.BOTH, expand=True)
        area.winfo_toplevel().bind("<Escape>", lambda _event: self.close() if self.is_open else None, add="+")

    @property
    def is_open(self) -> bool:
        return bool(self.frame.winfo_manager())

    def open(self) -> None:
        self.frame.place(in_=self._area, x=0, y=0, relwidth=1, relheight=1)
        self.frame.lift()

    def close(self) -> None:
        if self.is_open:
            self.frame.place_forget()
            self.cycle_time_ms = None
            self._on_change()

    def focus(self, track_id: Optional[int], cycle_index: Optional[int] = None) -> None:
        """보여줄 트랙과 사이클. cycle_index가 None이면 최신 사이클을 따라간다."""
        self.track_id = track_id
        self._cycle_index = cycle_index
        self._follow_latest.set(cycle_index is None)

    def _step(self, delta: int) -> None:
        self._follow_latest.set(False)
        if self._cycle_index is not None:
            self._cycle_index = max(0, self._cycle_index + delta)
        self._on_change()

    def refresh(self, state: MonitorState) -> None:
        history = state.tracking
        cycles = history.cycles(self.track_id) if self.track_id is not None else []
        self._title.configure(text="사이클 상세" if self.track_id is None else f"사이클 상세 — 트랙 #{self.track_id}")
        if not cycles:
            self.cycle_time_ms = None
            self._cycle_label.configure(text="트랙을 기다리는 중 (확인·추적 빔의 탐지 결과가 필요합니다)",
                                        foreground=MUTED)
            self._cross.delete("all")
            for card in (self._predict_card, self._observe_card, self._update_card):
                card.set("-", [])
            return
        if self._follow_latest.get() or self._cycle_index is None:
            self._cycle_index = len(cycles) - 1
        self._cycle_index = min(self._cycle_index, len(cycles) - 1)
        cycle = cycles[self._cycle_index]
        self.cycle_time_ms = cycle.time_ms
        self._cycle_label.configure(
            text=f"사이클 {self._cycle_index + 1}/{len(cycles)} · {beam_name(cycle.beam_type)} 빔 "
                 f"{format_time(cycle.time_ms)} → {OUTCOME_NAMES[cycle.outcome]}",
            foreground=OUTCOME_COLORS[cycle.outcome])
        self._draw_cards(cycles, cycle)
        self._draw_cross_section(cycle)

    def _draw_cards(self, cycles: List[TrackingCycle], cycle: TrackingCycle) -> None:
        previous = cycle.previous_estimate
        if cycle.predicted is None:
            self._predict_card.set("빔 없음", ["빔 명령을 받지 못했습니다", "(모니터 큐에서 버려졌을 수 있음)"],
                                   UNKNOWN)
        else:
            elapsed = cycle.elapsed_ms
            lines = [
                f"+{elapsed} ms 뒤 위치 예측" if elapsed is not None else
                ("탐색 탐지 방향으로 확인 빔" if cycle.beam_type == BeamType.CONFIRMATION else "직전 추정 없음"),
                "" if cycle.beam_width is None else f"빔 폭 {cycle.beam_width[0]:.1f}°",
            ]
            if previous is not None:
                lines.append(f"직전 {previous.slant_range_km:.2f} km · {previous.speed_mps:.0f} m/s")
            self._predict_card.set(f"{cycle.predicted[0]:.2f}° / {cycle.predicted[1]:.2f}°", lines,
                                   PREDICTED_COLOR)

        selected = cycle.selected
        if selected is None:
            self._observe_card.set("탐지 없음", ["표적이 빔 밖에 있었습니다", "→ 이번 사이클은 미탐"], WARN)
        elif cycle.predicted is None:
            plot = selected.plot
            self._observe_card.set(f"탐지 {len(cycle.plots)}개", [
                f"거리 {plot.slant_range_km:.3f} km", "예측 방향이 없어 차이를 계산할 수 없음"], TEXT)
        else:
            plot = selected.plot
            delta_azimuth = wrap_degrees(plot.azimuth_deg - cycle.predicted[0])
            delta_elevation = plot.elevation_deg - cycle.predicted[1]
            offset = selected.beam_offset
            self._observe_card.set(f"차이 {selected.angle_error_m:.1f} m", [
                f"탐지 {len(cycle.plots)}개 중 빔 축에 가장 가까운 것 (추정)" if len(cycle.plots) > 1
                else "탐지 1개",
                f"Δ방위 {delta_azimuth:+.3f}°  Δ고각 {delta_elevation:+.3f}°",
                f"거리 {plot.slant_range_km:.3f} km  도플러 {plot.doppler_mps:.0f} m/s",
                f"빔 안 위치 {offset:.2f}",
            ], DETECTED_COLOR)

        limit = self._parameters.max_consecutive_tracking_misses
        misses = 0
        position = cycles.index(cycle)
        while position >= 0 and cycles[position].outcome == Outcome.MISSED:
            misses += 1
            position -= 1
        next_beam = cycle.next_beam
        next_line = ("다음 빔 대기 중" if next_beam is None else
                     f"다음 빔 +{next_beam.timestamp_ms - cycle.time_ms} ms")
        if cycle.outcome == Outcome.UPDATED and cycle.estimate is not None:
            estimate = cycle.estimate
            self._update_card.set("갱신", [
                f"거리 {estimate.slant_range_km:.3f} km  방위 {estimate.azimuth_deg:.3f}°",
                f"속력 {estimate.speed_mps:.0f} m/s  진행 {estimate.heading_deg:.0f}°",
                f"탐지와 차이 {format_number(cycle.correction_m(), 1, ' m')}",
                next_line,
            ], OK)
        elif cycle.outcome == Outcome.MISSED:
            self._update_card.set(f"미탐 {misses}/{limit}", [
                "연관된 탐지가 없어 트랙을 갱신하지 않음", "마지막 상태로 다음 빔을 예측", next_line], WARN)
        elif cycle.outcome == Outcome.ENDED:
            reason = ("확인 실패 (트랙을 만들지 않음)" if cycle.beam_type == BeamType.CONFIRMATION else
                      f"연속 미탐 {limit}회, 영역 밖 거부 또는 결과 없음")
            self._update_card.set("종료", ["처리부 트랙 목록에서 사라짐", reason], BAD)
        else:
            self._update_card.set("대기", ["트랙 목록을 기다리는 중"], UNKNOWN)

    def _draw_cross_section(self, cycle: TrackingCycle) -> None:
        canvas = self._cross
        canvas.delete("all")
        width, height = canvas_size(canvas, 200, 120)
        if cycle.predicted is None:
            empty_message(canvas, "빔 명령이 없어 단면을 그릴 수 없습니다")
            return
        points = [(plot.cross_m, plot.up_m) for plot in cycle.plots]
        estimate = cycle.estimate_offset()
        if estimate is not None:
            points.append(estimate[:2])
        gate_m = math.sqrt(self._parameters.position_nds_threshold) * self._parameters.measurement_standard_deviation_m
        finite = [abs(value) for point in points for value in point if math.isfinite(value)]
        extent = max(finite + [gate_m * 1.3, 30.0]) * 1.2
        # 오른쪽에 범례 자리를 남긴다(글자 크기에 맞춰).
        legend_width = text_width("● 연관 탐지  999.9 m", FONT_SMALL) + 24
        available = width - legend_width if width > legend_width * 2 else width
        center_x, center_y = available / 2, height / 2
        scale = (min(available, height) / 2 - 14) / extent

        def to_canvas(cross: float, up: float) -> Tuple[float, float]:
            return center_x + cross * scale, center_y - up * scale

        half = extent * scale
        canvas.create_rectangle(center_x - half, center_y - half, center_x + half, center_y + half, fill=PANEL,
                                outline="")
        canvas.create_line(center_x - half, center_y, center_x + half, center_y, fill=GRID_LINE)
        canvas.create_line(center_x, center_y - half, center_x, center_y + half, fill=GRID_LINE)
        radius = gate_m * scale
        canvas.create_oval(center_x - radius, center_y - radius, center_x + radius, center_y + radius,
                           outline=PREDICTED_COLOR, dash=(4, 3))
        # 범위(±m)는 칸이 작으면 '방위 +'와 겹쳐 범례로 옮겼다.
        for text, x, y, anchor in (("방위 +", center_x + half - 4, center_y + 4, "ne"),
                                   ("고각 +", center_x + 4, center_y - half + 4, "nw")):
            canvas.create_text(x, y, text=text, fill=MUTED, anchor=anchor, font=FONT_SMALL)

        selected = cycle.selected
        for index, plot in enumerate(cycle.plots):
            if not (math.isfinite(plot.cross_m) and math.isfinite(plot.up_m)):
                continue
            x, y = to_canvas(plot.cross_m, plot.up_m)
            if abs(x - center_x) > half or abs(y - center_y) > half:
                continue
            color = DETECTED_COLOR if index == cycle.selected_index else CANDIDATE_COLOR
            if index == cycle.selected_index:
                canvas.create_line(center_x, center_y, x, y, fill=DETECTED_COLOR, width=2, arrow=tk.LAST)
            canvas.create_oval(x - 6, y - 6, x + 6, y + 6, fill=color, outline=BACKGROUND, width=2)
        if estimate is not None:
            x, y = to_canvas(*estimate[:2])
            canvas.create_rectangle(x - 6, y - 6, x + 6, y + 6, fill=ESTIMATE_COLOR, outline=BACKGROUND, width=2)
        canvas.create_oval(center_x - 7, center_y - 7, center_x + 7, center_y + 7, outline=PREDICTED_COLOR, width=3)

        # 점이 가운데에 몰리므로 이름은 점 옆이 아니라 그림 오른쪽에 범례로 쓴다.
        legend_x = center_x + half + 16
        legend = [("○ 예측 방향 (가운데)", PREDICTED_COLOR)]
        if selected is not None and math.isfinite(selected.angle_error_m):
            legend.append((f"● 연관 탐지  {selected.angle_error_m:.1f} m", DETECTED_COLOR))
        others = len(cycle.plots) - (1 if selected is not None else 0)
        if others > 0:
            legend.append((f"● 다른 탐지 {others}개", CANDIDATE_COLOR))
        if estimate is not None:
            legend.append((f"■ 갱신 결과  {math.hypot(*estimate[:2]):.1f} m", ESTIMATE_COLOR))
        legend.append((f"┅ 게이트 하한 {gate_m:.0f} m", PREDICTED_COLOR))
        legend.append((f"□ 범위 ±{extent:.0f} m", MUTED))
        if legend_x + legend_width - 16 > width:
            legend_x = 8
        # 칸이 낮으면 범례를 여러 열로 나눈다(1366×768·글자 16에서 단면 칸이 130 px까지 낮아진다).
        line = text_height(FONT_SMALL) + 4
        top = max(center_y - half, 2)
        column = max(text_width(text, FONT_SMALL) for text, _ in legend) + 16
        x, y = legend_x, top
        for text, color in legend:
            if y + line > height and y > top:
                x, y = x + column, top
            if x + text_width(text, FONT_SMALL) > width:
                break
            canvas.create_text(x, y, text=text, fill=color, anchor="nw", font=FONT_SMALL)
            y += line


# 추적 탭 -----------------------------------------------------------------------

class TrackTab:
    def __init__(self, parent: tk.Widget, parameters: TrackingParameters) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND, padx=12, pady=8)
        self._parameters = parameters
        self._track_id: Optional[int] = None
        self._map_points: List[Tuple[float, float, int]] = []
        self._map_mode = tk.StringVar(master=self.frame, value="sector")
        self._trend_area: Optional[ChartArea] = None
        self._trend_cycles: List[Tuple[float, int]] = []
        self._state: Optional[MonitorState] = None
        self._rows: List[TrackStatusRow] = []

        top = tk.Frame(self.frame, background=BACKGROUND)
        top.pack(fill=tk.X)
        self._verdict = VerdictBar(top, "표적을 잘 따라가고 있나?")
        self._verdict.frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        HelpButton(top, "처리부 추적 계산", lambda: algorithm_text(self._parameters)).pack(side=tk.LEFT, padx=(8, 0))

        body = tk.Frame(self.frame, background=BACKGROUND)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        # 왼쪽:오른쪽 = 3:2. 1:1이면 1366에서 오른쪽 글자가 잘리고, 2:3이면 부채꼴이 작다.
        body.columnconfigure(0, weight=3, uniform="track")
        body.columnconfigure(1, weight=2, uniform="track")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, background=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        map_title = section_title(left, "전시기 (위에서 본 빔·트랙)", HELP_MAP)
        for text, value in (("360°", "full"), ("섹터", "sector")):
            ttk.Radiobutton(map_title.master, text=text, value=value, variable=self._map_mode,
                            command=self._redraw).pack(side=tk.RIGHT, padx=(6, 0))
        self._map = tk.Canvas(left, background=BACKGROUND, highlightthickness=0, height=300)
        self._map.pack(fill=tk.BOTH, expand=True)
        self._map.bind("<Button-1>", self._on_map_click)
        # 사이클 상세는 전시기 칸(제목 줄 포함)만 덮는다. 오른쪽 상태 줄과 잔차 그래프는 그대로 보인다.
        self._detail = CycleDetailPanel(left, parameters, self._redraw)

        right = self._right = tk.Frame(body, background=BACKGROUND)
        right.grid(row=0, column=1, sticky="nsew")
        section_title(right, "트랙별 상태 (사실 값)", HELP_STATUS)
        self._status = TrackStatusList(right, self._select)
        self._status.canvas.pack(fill=tk.X)
        self._trend_title = section_title(right, "잔차 추이 (예측−측정, 참값 오차 아님)", HELP_TREND)
        detail = tk.Frame(right, background=PANEL, padx=8, pady=4)
        detail.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        detail_button = ttk.Button(detail, text="사이클 상세 ▸", command=self.open_detail)
        detail_button.pack(side=tk.LEFT)
        Tooltip(detail_button, "① 예측 → ② 관측·연관 → ③ 결과와 빔 단면을 전시기 자리에 띄웁니다.\n"
                               "잔차 그래프를 누르면 그 사이클을 엽니다. ✕나 Esc로 닫습니다.")
        note = tk.Label(detail, text=DETAIL_NOTE, font=FONT_SMALL, background=PANEL, foreground=MUTED, anchor="w",
                        justify=tk.LEFT)
        note.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        note.bind("<Configure>", lambda event: note.configure(wraplength=max(event.width - 2, 60)))
        self._trend = tk.Canvas(right, background=BACKGROUND, highlightthickness=0, height=160)
        self._trend.pack(fill=tk.BOTH, expand=True)
        self._trend.bind("<Button-1>", self._on_trend_click)

    # 조작

    def _select(self, track_id: int) -> None:
        if track_id == self._track_id:
            return
        self._track_id = track_id
        self._detail.focus(track_id)
        self._redraw()

    def _on_track_selected(self, iid: str) -> None:
        """다른 탭(분석 탭 문제 목록·북마크)에서 트랙을 고를 때 부른다."""
        self._select(int(iid))

    def _on_map_click(self, event: tk.Event) -> None:
        if not self._map_points:
            return
        x, y, track_id = min(self._map_points, key=lambda item: math.hypot(item[0] - event.x, item[1] - event.y))
        if math.hypot(x - event.x, y - event.y) <= max(20, text_height()):
            self._select(track_id)

    def _on_trend_click(self, event: tk.Event) -> None:
        if self._trend_area is None or not self._trend_cycles:
            return
        time_s = self._trend_area.x_value(event.x)
        _, index = min(self._trend_cycles, key=lambda item: abs(item[0] - time_s))
        self.open_detail(index)

    def open_detail(self, cycle_index: Optional[int] = None) -> None:
        self._detail.focus(self._track_id, cycle_index)
        self._detail.open()
        self._redraw()

    def _redraw(self) -> None:
        if self._state is not None:
            self._draw(self._state)

    # 갱신

    def refresh(self, state: MonitorState) -> None:
        self._state = state
        self._verdict.set(track_verdict(state, self._parameters))
        history = state.tracking
        self._rows = track_status_rows(history, state.now_ms())
        shown = [row.track_id for row in self._rows]
        previous = self._track_id
        # 고른 트랙이 상태 줄에서 빠지면(종료 뒤 1분, 재생 위치 이동) 맨 위 살아 있는 트랙으로 옮긴다.
        if self._track_id not in shown:
            active = [row.track_id for row in self._rows if row.alive]
            track_ids = history.track_ids()
            if active or shown:
                self._track_id = active[0] if active else shown[0]  # 맨 위(문제 트랙이 있으면 그것)
            elif self._track_id not in track_ids:
                self._track_id = track_ids[-1] if track_ids else None
        if self._track_id != previous:
            # 열려 있는 사이클 상세도 새 트랙으로 옮긴다(안 하면 왼쪽은 옛 트랙, 오른쪽은 새 트랙을 보인다).
            self._detail.focus(self._track_id)
        self._draw(state)

    def _draw(self, state: MonitorState) -> None:
        # 패널을 먼저 갱신해야 잔차 그래프가 패널이 보는 사이클(세로 점선)을 안다.
        if self._detail.is_open:
            if self._detail.track_id is None:
                self._detail.focus(self._track_id)
            self._detail.refresh(state)
        else:
            self._draw_map(state)  # 패널이 덮고 있으면 그리지 않는다
        self._draw_status(state)
        self._draw_trend(state)

    def _draw_status(self, state: MonitorState) -> None:
        # 4행 고정(넘치면 휠). 오른쪽 칸이 낮아 잔차 그래프 최소 높이를 못 남기면 3행.
        rows = self._rows
        visible = STATUS_ROWS
        right_height = self._right.winfo_height()
        if right_height > 50 and len(rows) > STATUS_ROWS_SMALL:
            others = right_height - self._status.canvas.winfo_height() - self._trend.winfo_height()
            minimum_trend = text_height() * 6 + 40
            if right_height - others - self._status.height_for(STATUS_ROWS, len(rows) > STATUS_ROWS) < minimum_trend:
                visible = STATUS_ROWS_SMALL
        self._status.draw(rows, self._track_id, self._parameters.max_consecutive_tracking_misses, visible)

    # 잔차 추이

    def _draw_trend(self, state: MonitorState) -> None:
        canvas = self._trend
        canvas.delete("all")
        self._trend_area = None
        self._trend_cycles = []
        width, height = canvas_size(canvas, 200, 100)
        history = state.tracking
        all_cycles = history.cycles(self._track_id) if self._track_id is not None else []
        if not all_cycles:
            self._trend_title.configure(text="잔차 추이 (예측−측정, 참값 오차 아님)")
            empty_message(canvas, "트랙을 기다리는 중 (확인·추적 빔의 탐지 결과가 필요합니다)")
            return
        start = max(len(all_cycles) - TREND_CYCLES, 0)
        cycles = all_cycles[start:]
        self._trend_title.configure(text=f"트랙 #{self._track_id} 잔차 (예측−측정, 참값 오차 아님)")
        gate = math.sqrt(self._parameters.position_nds_threshold) * self._parameters.measurement_standard_deviation_m
        times = [cycle.time_ms / 1000 for cycle in cycles]
        x_low, x_high = min(times), max(times)
        if x_high - x_low < 1:
            x_low, x_high = x_low - 1, x_high + 1

        residuals = [cycle.selected.angle_error_m for cycle in cycles
                     if cycle.selected is not None and math.isfinite(cycle.selected.angle_error_m)]
        high = min(max(residuals + [gate * 1.6]), gate * RESIDUAL_AXIS_GATES) * 1.08
        over = [value for value in residuals if value > high]

        # 여백은 글자 크기로 잡는다. 오른쪽 여백에는 게이트 선 값을 쓴다(그래프 안 글자는 데이터를 가린다).
        line = text_height()
        gate_text = f"{gate:.0f}"
        left = text_width("0000") + 14
        right = width - text_width(gate_text) - 14
        top = line * 2 + 8 if self._header_lines(width, left, cycles, over, gate) == 2 else line + 8
        bottom = height - line - 10
        area = ChartArea(canvas, (left, top, right, bottom), (x_low, x_high), (0, high))
        y_count = max(3, min(6, int((bottom - top) // (line * 2.2))))
        area.draw_axes(lambda value: "", lambda value: f"{value:.0f}", "", "", y_count=y_count)
        # 가로 눈금 글자는 직접 쓴다: 왼쪽 끝 눈금이 세로축 '0'과 겹치면 뺀다.
        for tick in nice_ticks(x_low, x_high, 5):
            x = area.x(tick)
            label = f"{tick:.0f} s"
            if x - text_width(label) / 2 < area.left - 4 or x + text_width(label) / 2 > width:
                continue
            canvas.create_text(x, area.bottom + 6, text=label, fill=MUTED, anchor="n", font=FONT_SMALL)
        self._trend_area = area

        # 제목 줄(그래프 위): 축 이름, 게이트 범례, 최근 사이클 수와 축 위 값. 좁으면 두 줄 → 짧은 문구.
        legend = f"┅ 게이트 하한 {gate:.0f} m"
        notes = [f"최근 {len(cycles)}사이클"]
        if over:
            notes = [f"최근 {len(cycles)}사이클 · ▲ 축 위 {len(over)}개 (최대 {max(over):.0f} m)",
                     f"▲ 축 위 {len(over)}개 (최대 {max(over):.0f} m)", f"▲ {len(over)}개 · 최대 {max(over):.0f} m"]
        y_title = "잔차 m"
        first = 2
        canvas.create_text(left, first, text=y_title, anchor="nw", fill=MUTED, font=FONT_SMALL)
        x = left + text_width(y_title) + 14
        canvas.create_text(x, first, text=legend, anchor="nw", fill=PREDICTED_COLOR, font=FONT_SMALL)
        x += text_width(legend) + 14
        room = width - 4 - x
        note = next((item for item in notes if text_width(item) <= room), None)
        if note is not None:
            canvas.create_text(width - 4, first, text=note, anchor="ne", fill=MUTED, font=FONT_SMALL)
        else:
            note = next((item for item in notes if text_width(item) <= width - left - 4), "")
            canvas.create_text(width - 4, first + line, text=note, anchor="ne", fill=MUTED, font=FONT_SMALL)

        # 게이트 하한 선: 그래프 안에는 점선만, 값은 오른쪽 여백에.
        y = area.y(gate)
        canvas.create_line(area.left, y, area.right, y, fill=PREDICTED_COLOR, dash=(4, 3))
        canvas.create_text(area.right + 6, y, text=gate_text, fill=PREDICTED_COLOR, anchor="w", font=FONT_SMALL)

        color = track_color(self._track_id)
        points, tops = [], []
        for offset_index, cycle in enumerate(cycles):
            x_value = cycle.time_ms / 1000
            self._trend_cycles.append((x_value, start + offset_index))
            selected = cycle.selected
            residual = selected.angle_error_m if selected is not None else math.nan
            if cycle.outcome == Outcome.MISSED or selected is None:
                # 미탐: 탐지가 있었으면(연관 실패) 그 잔차 자리에, 탐지가 없거나 빔이 없어 잔차를 모르면 0에 × 를 그린다.
                # 연관 실패의 잔차는 미탐 원인을 따질 때 가장 쓸모 있는 값이라 '탐지 없음'과 구분한다.
                if math.isfinite(residual) and residual > high:
                    tops.append((x_value, WARN))
                else:
                    area.crosses([(x_value, residual if math.isfinite(residual) else 0.0)], WARN, size=4)
                points.append((math.nan, math.nan))
                continue
            if math.isfinite(residual) and residual > high:
                tops.append((x_value, color))
                points.append((math.nan, math.nan))
            else:
                points.append((x_value, residual))
        area.line(points, color, width=2)
        area.markers(points, color, 2.5)
        for x_value, top_color in tops:
            x = area.x(x_value)
            canvas.create_polygon(x - 5, area.top + 7, x + 5, area.top + 7, x, area.top, fill=top_color, outline="")
        # 사이클 상세가 보는 사이클
        if self._detail.is_open and self._detail.cycle_time_ms is not None:
            x_value = self._detail.cycle_time_ms / 1000
            if x_low <= x_value <= x_high:
                x = area.x(x_value)
                canvas.create_line(x, area.top, x, area.bottom, fill=TEXT, dash=(2, 2), width=2)

    @staticmethod
    def _header_lines(width: float, left: float, cycles: Sequence[TrackingCycle], over: Sequence[float],
                      gate: float) -> int:
        """잔차 그래프 제목 줄이 한 줄에 들어가는지(축 이름 + 게이트 범례 + 가장 짧은 설명)."""
        shortest = f"최근 {len(cycles)}사이클" if not over else f"▲ {len(over)}개 · 최대 {max(over):.0f} m"
        needed = (left + text_width("잔차 m") + 14 + text_width(f"┅ 게이트 하한 {gate:.0f} m") + 14
                  + text_width(shortest) + 4)
        return 1 if needed <= width else 2

    # 전시기

    def _draw_map(self, state: MonitorState) -> None:
        """전시기(PPI): 거리 고리, 방위 눈금, 추적 영역, 탐색·확인·추적 빔 부채꼴, 트랙과 속도 벡터.

        섹터: 레이더 정면(yaw)을 위로 두고 yaw ±70°만 그린다(같은 크기에서 축척이 약 1.7배).
        360°: 북쪽을 위로 둔 원판.
        Tk 호 각도는 화면 동쪽 0°·반시계라 start = 90 − (방위 − 회전 + 폭/2)이다.
        """
        canvas = self._map
        canvas.delete("all")
        width, height = canvas_size(canvas, 200, 160)
        history = state.tracking
        attitude = state.attitude
        yaw = attitude.yaw_deg if attitude is not None else 0.0
        sector = self._map_mode.get() == "sector"
        rotation = yaw if sector else 0.0
        line = text_height()

        # 위쪽 범례 줄(넘치면 다음 줄로).
        if attitude is None:
            info = "레이더 자세 없음 · 정면 0°로 가정" if sector else "북쪽 위 · 레이더 자세 없음"
        else:
            info = f"레이더 정면 {yaw:.1f}° 위쪽" if sector else f"북쪽 위 · 레이더 정면 {yaw:.1f}°"
        legend = [(info, MUTED), ("▬ 탐색", BEAM_COLORS[BeamType.SEARCH]),
                  ("▬ 확인", BEAM_COLORS[BeamType.CONFIRMATION]), ("▬ 추적", BEAM_COLORS[BeamType.TRACKING]),
                  ("┅ 추적 영역 ±60°", MUTED), (f"■ 트랙 ─ {VECTOR_SECONDS}초 속도 벡터", TEXT)]
        x, y = 4.0, 2.0
        for text, color in legend:
            text_w = text_width(text)
            if x > 4 and x + text_w > width - 4:
                x, y = 4.0, y + line
            canvas.create_text(x, y, text=text, anchor="nw", fill=color, font=FONT_SMALL)
            x += text_w + 14
        area_top = y + line + 4

        # 반지름: 방위 이름표 자리를 글자 크기로 남긴다.
        label_gap = line * 0.6 + 4
        pad_x = text_width("340°") / 2 + label_gap + 6
        pad_y = label_gap + line
        if sector:
            # 칸 너비가 반지름을 정하는 경우가 많다. 남는 높이는 위아래로 나눠 부채꼴을 가운데쯤에 둔다.
            bottom = height - 8
            radius_px = min(bottom - area_top - pad_y,
                            (width / 2 - pad_x) / math.sin(math.radians(SECTOR_HALF_SPAN)))
            slack = max(bottom - area_top - pad_y - radius_px, 0)
            center_x, center_y = width / 2, bottom - slack / 2
        else:
            center_x, center_y = width / 2, (area_top + height) / 2
            radius_px = min(width / 2 - pad_x, (height - area_top) / 2 - pad_y)
        radius_px = max(radius_px, 40.0)

        # 상태 줄과 같은 트랙(살아 있는 트랙 + 최근 종료)만 그린다. 오래전 종료된 트랙은 지도를 어지럽힌다.
        trails = {}
        for track_id in [row.track_id for row in self._rows]:
            points = [track_position(cycle.estimate) for cycle in history.cycles(track_id)[-MAP_TRAIL_CYCLES:]
                      if cycle.estimate is not None]
            if track_id in history.latest_tracks:
                points.append(track_position(history.latest_tracks[track_id]))
            if points:
                trails[track_id] = [(point[0] / 1000, point[1] / 1000) for point in points]
        farthest = max([max(math.hypot(*point) for point in points) for points in trails.values()] + [20.0])
        ticks = nice_ticks(0, farthest * 1.1, 4)
        step_km = ticks[1] - ticks[0] if len(ticks) > 1 else farthest
        extent = ticks[-1] if ticks[-1] >= farthest * 1.05 else ticks[-1] + step_km
        scale = radius_px / extent

        def polar(azimuth: float, r_px: float) -> Tuple[float, float]:
            angle = math.radians(azimuth - rotation)
            return center_x + r_px * math.sin(angle), center_y - r_px * math.cos(angle)

        def to_xy(east: float, north: float) -> Tuple[float, float]:
            return polar(math.degrees(math.atan2(east, north)), math.hypot(east, north) * scale)

        def arc(azimuth: float, width_deg: float, r_px: float, style: str = tk.PIESLICE, **options) -> None:
            canvas.create_arc(center_x - r_px, center_y - r_px, center_x + r_px, center_y + r_px,
                              start=90 - (azimuth - rotation + width_deg / 2), extent=width_deg, style=style,
                              **options)

        def visible(azimuth: float) -> bool:
            return not sector or abs(wrap_degrees(azimuth - yaw)) <= SECTOR_HALF_SPAN

        placer = LabelPlacer(canvas, (0, area_top, width, height))

        # 바탕, 추적 영역
        if sector:
            arc(yaw, 2 * SECTOR_HALF_SPAN, radius_px, fill=PANEL, outline=BORDER)
        else:
            canvas.create_oval(center_x - radius_px, center_y - radius_px, center_x + radius_px,
                               center_y + radius_px, fill=PANEL, outline=BORDER)
        if attitude is not None:
            low, high = COVERAGE_AZIMUTH
            if not sector:
                arc(yaw + (low + high) / 2, high - low, radius_px, fill=COVERAGE_FILL, outline="")
            for edge in (yaw + low, yaw + high):
                canvas.create_line(center_x, center_y, *polar(edge, radius_px), fill=MUTED, dash=(5, 4), width=2)

        # 거리 고리
        for ring in ticks[1:]:
            if ring > extent + 1e-9:
                continue
            r = ring * scale
            if sector:
                arc(yaw, 2 * SECTOR_HALF_SPAN, r, style=tk.ARC, outline=GRID_LINE)
                position = polar(yaw - SECTOR_HALF_SPAN + 5, r - 2)
                anchor = "n"
            else:
                canvas.create_oval(center_x - r, center_y - r, center_x + r, center_y + r, outline=GRID_LINE)
                position = polar(rotation + 135, r)
                anchor = "nw"
            item = canvas.create_text(*position, text=f"{ring:g} km", fill=MUTED, font=FONT_SMALL, anchor=anchor)
            box = canvas.bbox(item)
            if box:
                placer.reserve(box)

        # 방위 눈금: 섹터는 10°마다 선, 20°마다 글자. 360°는 30°마다.
        step = 10 if sector else 30
        if sector:
            azimuth = math.ceil((yaw - SECTOR_HALF_SPAN) / step) * step
            last = yaw + SECTOR_HALF_SPAN
        else:
            azimuth, last = 0, 359
        while azimuth <= last + 1e-9:
            canvas.create_line(*polar(azimuth, radius_px * 0.04), *polar(azimuth, radius_px), fill=GRID_LINE)
            if not sector or round(azimuth) % 20 == 0:
                value = round(azimuth) % 360
                text = {0: "N", 90: "E", 180: "S", 270: "W"}.get(value, f"{value}°")
                item = canvas.create_text(*polar(azimuth, radius_px + label_gap), text=text, fill=MUTED,
                                          font=FONT_SMALL)
                box = canvas.bbox(item)
                if box:
                    placer.reserve(box)
            azimuth += step

        # 빔 부채꼴: 오래된 것부터 그려 최근 빔이 위에 오게 한다.
        now = state.now_ms()
        if now is not None:
            search_color = BEAM_COLORS[BeamType.SEARCH]
            recent = state.recent_beams(SEARCH_GLOW_MS)
            for beam in recent:
                if beam.beam_type != BeamType.SEARCH or not visible(beam.azimuth_deg):
                    continue
                ratio = max(0.0, 1.0 - (now - beam.timestamp_ms) / SEARCH_GLOW_MS)
                arc(beam.azimuth_deg, max(beam.azimuth_width_deg, 1.0), radius_px,
                    fill=blend(search_color, 0.08 + ratio * 0.25, PANEL), outline="")
            latest = next((beam for beam in reversed(recent) if beam.beam_type == BeamType.SEARCH), None)
            if latest is not None and visible(latest.azimuth_deg):
                arc(latest.azimuth_deg, max(latest.azimuth_width_deg, 1.0), radius_px, fill="",
                    outline=search_color, width=2)
            for beam in state.recent_beams(REQUEST_GLOW_MS):
                if beam.beam_type == BeamType.SEARCH or not visible(beam.azimuth_deg):
                    continue
                ratio = max(0.0, 1.0 - (now - beam.timestamp_ms) / REQUEST_GLOW_MS)
                color = BEAM_COLORS.get(beam.beam_type, MUTED)
                arc(beam.azimuth_deg, max(beam.azimuth_width_deg, 1.0), radius_px,
                    fill=blend(color, 0.10 + ratio * 0.45, PANEL), outline=blend(color, 0.3 + ratio * 0.7, PANEL))

        canvas.create_oval(center_x - 4, center_y - 4, center_x + 4, center_y + 4, fill=TEXT, outline="")

        # 트랙: 꼬리, 속도 벡터, 기호, 이름표. 고른 트랙을 마지막에 그려 위에 오게 한다.
        self._map_points = []
        outside = 0
        order = sorted(trails, key=lambda track_id: track_id == self._track_id)
        for track_id in order:
            points = trails[track_id]
            alive = track_id in history.latest_tracks
            east, north = points[-1]
            if not visible(math.degrees(math.atan2(east, north))):
                outside += 1
                continue
            color = track_color(track_id) if alive else CANDIDATE_COLOR
            chosen = track_id == self._track_id
            coordinates = [to_xy(e, n) if visible(math.degrees(math.atan2(e, n))) else None for e, n in points]
            segment: List[float] = []
            for point in coordinates + [None]:
                if point is None:
                    if len(segment) >= 4:
                        canvas.create_line(*segment, fill=color, width=2 if chosen else 1,
                                           dash=() if alive else (3, 3))
                    segment = []
                else:
                    segment.extend(point)
            x, y = coordinates[-1]
            track = history.latest_tracks.get(track_id)
            if track is not None:
                heading = math.radians(track.heading_deg - rotation)
                length = track.speed_mps * VECTOR_SECONDS / 1000 * scale
                canvas.create_line(x, y, x + length * math.sin(heading), y - length * math.cos(heading),
                                   fill=color, width=2)
            size = 7 if chosen else 5
            if alive:
                canvas.create_rectangle(x - size, y - size, x + size, y + size, fill=color, outline=BACKGROUND)
            else:
                canvas.create_line(x - size, y - size, x + size, y + size, fill=color, width=2)
                canvas.create_line(x - size, y + size, x + size, y - size, fill=color, width=2)
            if chosen:
                canvas.create_oval(x - 12, y - 12, x + 12, y + 12, outline=TEXT, width=2)
            placer.reserve((x - size, y - size, x + size, y + size))
            self._map_points.append((x, y, track_id))
        # 이름표는 기호를 다 그린 뒤에 놓아야 다른 기호와 겹치지 않는다. 고른 트랙부터 자리를 잡는다.
        for x, y, track_id in reversed(self._map_points):
            track = history.latest_tracks.get(track_id)
            label = f"#{track_id}"
            if track is not None:
                label += f" {track.slant_range_km:.1f} km · {track.speed_mps:.0f} m/s"
            gap = (12 if track_id == self._track_id else 5) + 4
            if placer.place(x, y, label, gap=gap, color=TEXT if track is not None else MUTED) is None:
                placer.place(x, y, f"#{track_id}", gap=gap, color=TEXT if track is not None else MUTED)
        if not trails:
            canvas.create_text(center_x, center_y - radius_px / 2, text="트랙 없음", fill=MUTED, font=FONT_BODY)
        if outside:
            canvas.create_text(4, height - 4, text=f"섹터 밖 트랙 {outside}개 → 360°에서 보기", fill=WARN,
                               anchor="sw", font=FONT_SMALL)
