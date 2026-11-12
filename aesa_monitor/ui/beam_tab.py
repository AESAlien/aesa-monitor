"""빔 탭 — "빔이 계획대로 나가고 있나?"

탐색 격자를 도는 속도, 확인·추적 빔이 끼어드는 시점, 트랙마다 추적 빔이 설정 주기대로 나가는지를 본다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.protocol import BeamType
from aesa_monitor.core.state import BEAM_SLOT_MS, MonitorState, ViewMode
from aesa_monitor.core.verdict import (
    WINDOW_MS, beam_verdict, confirmation_check, gap_check, period_check, sweep_check,
)
from aesa_monitor.ui.style import (
    BACKGROUND, BEAM_COLORS, BEAM_NAMES, FONT_SMALL, GRID_LINE, LEVEL_COLORS, MUTED, PANEL, TEXT,
    beam_color, beam_name, blend, format_time, track_color,
)
from aesa_monitor.ui.widgets import (
    KpiCard, LabelPlacer, Table, VerdictBar, canvas_size, frame_share_chart, nice_ticks, section_title,
)

SEARCH_AFTERGLOW_MS = {ViewMode.FLOW: 500, ViewMode.DETAIL: 150}
REQUEST_AFTERGLOW_MS = {ViewMode.FLOW: 2000, ViewMode.DETAIL: 400}
TIMELINE_WINDOW_MS = {ViewMode.FLOW: 3000, ViewMode.DETAIL: 400}
# 확인·추적 빔 추적 영역(안테나 기준). 처리부 .env의 BEAM_REQUEST_COVERAGE_* 기본값과 같다.
COVERAGE_AZIMUTH = (-60.0, 60.0)
COVERAGE_ELEVATION = (-10.0, 60.0)

HELP_SWEEP = (
    "탐색 프레임: 탐색 격자(189칸)를 처음부터 끝까지 한 번 다 도는 것입니다.\n"
    "탐색 프레임 시간은 한 프레임에 걸린 시간으로, 같은 탐색 칸을 다시 방문하기까지의 간격으로 잽니다.\n\n"
    "기대값 = 격자 칸 수 × 10 ms ÷ 최근 5초 탐색 점유율.\n"
    "확인·추적 빔이 끼어든 만큼 프레임 시간이 길어지므로 점유율로 보정합니다.\n"
    "기대값과 10% 넘게 다르면 주의, 30% 넘게 다르면 이상입니다."
)
HELP_SHARE = "최근 5초 동안 나간 빔 중 탐색·확인·추적이 차지한 비율입니다. 추적 트랙이 늘면 탐색 비율이 줄어듭니다."
HELP_CONFIRM = (
    "탐색 빔에서 표적을 찾은 뒤 같은 방향으로 확인 빔이 나가기까지 걸린 시간입니다.\n\n"
    "확인 빔 방향과 0.5° 안에 있는 직전 탐색 탐지를 찾아 계산합니다(모니터 추정).\n"
    "기준은 .env의 TARGET_CONFIRMATION_DELAY_MS입니다. 20 ms 넘게 다르면 주의, 100 ms 넘게 다르면 이상입니다."
)
HELP_PERIOD = (
    "트랙마다 연속한 두 추적 빔 사이 간격과 설정 추적 주기(TARGET_TRACKING_INTERVAL_MS)의 차이 중 "
    "가장 큰 값입니다.\n20 ms 넘게 다르면 주의, 100 ms 넘게 다르면 이상입니다."
)
HELP_GAPS = (
    "처리부는 10 ms마다 빔을 하나씩 보냅니다. 최근 5초 동안 빔이 오지 않은 10 ms 칸 수입니다.\n\n"
    "처리부가 늦었거나, 처리부의 모니터 큐(1000개)가 넘쳐 복사본이 버려진 경우입니다.\n"
    "1~5칸은 주의, 6칸 이상은 이상입니다."
)
HELP_FRAMES = (
    "탐색 프레임마다 나간 빔 중 탐색·확인·추적 빔의 비율입니다.\n\n"
    "탐색 프레임은 탐색 격자를 처음(탐색 빔 0번)부터 끝까지 한 번 다 도는 구간이고, 사이에 끼어든 확인·추적 빔도 "
    "그 프레임에 셉니다. 처음에는 탐색이 거의 100%이고, 추적 트랙이 늘수록 탐색 비율이 줄고 추적 비율이 늘어납니다.\n"
    "모니터를 켠 뒤 처음 잘린 프레임과 처리부 재시작으로 끊긴 프레임은 그리지 않습니다. 맨 위 숫자는 마지막 프레임 값입니다."
)
HELP_MAP = (
    "처리부가 보낸 빔의 방위·고각(ENU, 북쪽 0°, 시계방향)입니다.\n\n"
    "• 파란 원: 탐색 빔 189개의 실제 빔 범위(지름 = 빔 폭 6°). 방금 쏜 빔일수록 진하고, 굵은 검은 원이 "
    "마지막 탐색 빔입니다. 빔 폭이 격자 간격(4.2°, 4.4°)보다 넓어 원이 서로 겹칩니다.\n"
    "• 노랑·초록 원: 확인·추적 빔(빔 폭 3°).\n"
    "• 방위와 고각은 같은 축척으로 그립니다. 모의기는 방위·고각 차이를 빔 폭으로 나눈 타원식"
    "((2Δaz/폭)² + (2Δel/폭)² ≤ 1, cos(고각) 보정 없음)으로 빔 안을 판정하므로 이 그림의 원 안이 탐지 범위입니다.\n"
    "• ◆: 처리부가 운용통제기에 보낸 트랙 위치와 ID입니다.\n"
    "• 점선 네모: 확인·추적 빔을 보낼 수 있는 추적 영역(안테나 기준 방위 ±60°, 고각 -10~60°)입니다. "
    "레이더 yaw만 반영한 근사이고 roll·pitch는 무시합니다. 이 밖의 요청은 처리부가 거부합니다(OUT_OF_COVERAGE)."
)


class BeamTab:
    def __init__(self, parent: tk.Widget, parameters: TrackingParameters) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND, padx=12, pady=8)
        self._parameters = parameters
        self._view_mode = tk.StringVar(value=ViewMode.AUTO)
        self._show_coverage = tk.BooleanVar(value=True)

        self._verdict = VerdictBar(self.frame, "빔이 계획대로 나가고 있나?")
        self._verdict.frame.pack(fill=tk.X)

        cards = tk.Frame(self.frame, background=BACKGROUND)
        cards.pack(fill=tk.X, pady=(8, 0))
        self._sweep = KpiCard(cards, "탐색 프레임 시간", HELP_SWEEP)
        self._share = KpiCard(cards, "빔 점유율 (최근 5초)", HELP_SHARE)
        self._confirm = KpiCard(cards, "탐색 → 확인", HELP_CONFIRM)
        self._period = KpiCard(cards, "추적 주기 오차", HELP_PERIOD)
        self._gaps = KpiCard(cards, "빠진 빔 (최근 5초)", HELP_GAPS)
        for index, card in enumerate((self._sweep, self._share, self._confirm, self._period, self._gaps)):
            card.frame.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            cards.columnconfigure(index, weight=1, uniform="kpi")

        middle = tk.Frame(self.frame, background=BACKGROUND)
        middle.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        middle.columnconfigure(0, weight=3)
        middle.columnconfigure(1, weight=2)
        middle.rowconfigure(0, weight=1)

        left = tk.Frame(middle, background=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        header = tk.Frame(left, background=BACKGROUND)
        header.pack(fill=tk.X)
        section_title(header, "빔 방향 지도 (방위·고각)", HELP_MAP).master.pack(side=tk.LEFT)
        ttk.Checkbutton(header, text="추적 영역", variable=self._show_coverage).pack(side=tk.RIGHT)
        for text, value in (("흐름", ViewMode.FLOW), ("상세", ViewMode.DETAIL), ("자동", ViewMode.AUTO)):
            ttk.Radiobutton(header, text=text, value=value, variable=self._view_mode).pack(side=tk.RIGHT, padx=2)
        tk.Label(header, text="보기", background=BACKGROUND, foreground=MUTED).pack(side=tk.RIGHT, padx=(0, 4))
        self._plane = tk.Canvas(left, background=BACKGROUND, highlightthickness=0, height=260)
        self._plane.pack(fill=tk.BOTH, expand=True)
        legend = tk.Frame(left, background=BACKGROUND)
        legend.pack(fill=tk.X)
        for text, color in (("○ 탐색 빔 (빔 폭 원)", BEAM_COLORS[BeamType.SEARCH]),
                            ("● 확인 빔", BEAM_COLORS[BeamType.CONFIRMATION]),
                            ("● 추적 빔", BEAM_COLORS[BeamType.TRACKING]), ("◆ 트랙 위치", TEXT),
                            ("┅ 추적 영역", MUTED)):
            tk.Label(legend, text=text, foreground=color, background=BACKGROUND, font=FONT_SMALL).pack(
                side=tk.LEFT, padx=(0, 12))

        right = tk.Frame(middle, background=BACKGROUND)
        right.grid(row=0, column=1, sticky="nsew")
        self._schedule_title = section_title(
            right, "트랙별 요청 빔 일정",
            "트랙(요청 ID)마다 마지막으로 나간 확인·추적 빔과 직전 간격입니다.\n"
            "주기 차이 = 직전 간격 − 설정 추적 주기. 색은 판정(초록 정상, 주황 주의, 빨강 이상)이고 "
            "회색은 처리부 트랙 목록에서 사라진 트랙입니다.")
        self._schedule = Table(right, (
            ("id", "트랙", 60, tk.CENTER), ("type", "마지막 빔", 80, tk.CENTER),
            ("last", "송신 시각", 110, tk.E), ("interval", "직전 간격", 90, tk.E),
            ("deviation", "주기 차이", 90, tk.E), ("next", "다음 예정", 110, tk.E),
        ), height=5)
        self._schedule.frame.pack(fill=tk.BOTH, expand=True)
        section_title(right, "탐색 프레임별 빔 점유율", HELP_FRAMES)
        self._frames = tk.Canvas(right, background=BACKGROUND, highlightthickness=0, height=150)
        self._frames.pack(fill=tk.BOTH, expand=True)

        self._timeline_title = section_title(self.frame, "빔 타임라인")
        self._timeline = tk.Canvas(self.frame, background=BACKGROUND, highlightthickness=0, height=110)
        self._timeline.pack(fill=tk.X)

    # 갱신

    def refresh(self, state: MonitorState) -> None:
        mode = state.resolve_view_mode(self._view_mode.get())
        self._verdict.set(beam_verdict(state, self._parameters))
        self._draw_cards(state)
        self._draw_plane(state, mode)
        self._draw_schedule(state)
        frame_share_chart(self._frames, state.frames.complete_frames())
        self._draw_timeline(state, mode)

    def _draw_cards(self, state: MonitorState) -> None:
        sweep = sweep_check(state)
        self._sweep.set("-" if sweep.value is None else f"{sweep.value / 1000:.2f} s",
                        "" if sweep.expected is None or sweep.value is None else f"기대 {sweep.expected / 1000:.2f} s",
                        sweep.level)

        occupancy = state.occupancy(WINDOW_MS)
        if any(occupancy.values()):
            self._share.set(f"{occupancy[BeamType.SEARCH] * 100:.0f}%",
                            f"탐색 {occupancy[BeamType.SEARCH] * 100:.0f} · 확인 "
                            f"{occupancy[BeamType.CONFIRMATION] * 100:.0f} · 추적 "
                            f"{occupancy[BeamType.TRACKING] * 100:.0f} %", "ok")
        else:
            self._share.set("-", "빔 없음", "idle")

        confirmation = confirmation_check(state, self._parameters)
        self._confirm.set("-" if confirmation.value is None else f"{confirmation.value:.0f} ms",
                          f"설정 {self._parameters.confirmation_delay_ms} ms", confirmation.level)

        period = period_check(state, self._parameters)
        self._period.set("-" if period.value is None else f"{period.value:.0f} ms",
                         f"설정 주기 {self._parameters.tracking_interval_ms} ms", period.level)

        gaps = gap_check(state)
        self._gaps.set("-" if gaps.value is None else f"{gaps.value:.0f}칸",
                       f"빔 간격 {BEAM_SLOT_MS} ms 기준", gaps.level)

    def _bounds(self, state: MonitorState, coverage: Optional[Tuple[float, float, float, float]]):
        cells = list(state.search_cells.values())
        if not cells and coverage is None:
            return (-60.0, 60.0, -10.0, 60.0)
        lefts, rights, bottoms, tops = [], [], [], []
        for cell in cells:
            lefts.append(cell.azimuth_deg - cell.width_deg)
            rights.append(cell.azimuth_deg + cell.width_deg)
            bottoms.append(cell.elevation_deg - cell.width_deg)
            tops.append(cell.elevation_deg + cell.width_deg)
        if coverage is not None:
            lefts.append(coverage[0] - 3)
            rights.append(coverage[1] + 3)
            bottoms.append(coverage[2] - 3)
            tops.append(coverage[3] + 3)
        return min(lefts), max(rights), min(bottoms), max(tops)

    def _coverage(self, state: MonitorState) -> Optional[Tuple[float, float, float, float]]:
        if not self._show_coverage.get() or state.attitude is None:
            return None
        yaw = state.attitude.yaw_deg
        center = state.unwrap_azimuth(yaw)
        return (center + COVERAGE_AZIMUTH[0], center + COVERAGE_AZIMUTH[1],
                COVERAGE_ELEVATION[0], COVERAGE_ELEVATION[1])

    def _draw_plane(self, state: MonitorState, mode: str) -> None:
        canvas = self._plane
        canvas.delete("all")
        width, height = canvas_size(canvas, 200, 160)
        coverage = self._coverage(state)
        left, right, bottom, top = self._bounds(state, coverage)
        margin_left, margin_bottom, margin_top, margin_right = 48, 26, 8, 8
        plot_width = width - margin_left - margin_right
        plot_height = height - margin_bottom - margin_top
        # 방위와 고각을 같은 축척(1°당 같은 픽셀)으로 그려야 빔이 화면에서도 원으로 보인다.
        # 남는 쪽은 범위를 넓혀 가운데에 둔다.
        scale = min(plot_width / (right - left), plot_height / (top - bottom))
        middle_x, middle_y = (left + right) / 2, (bottom + top) / 2
        left, right = middle_x - plot_width / scale / 2, middle_x + plot_width / scale / 2
        bottom, top = middle_y - plot_height / scale / 2, middle_y + plot_height / scale / 2

        def to_x(azimuth: float) -> float:
            return margin_left + (azimuth - left) * scale

        def to_y(elevation: float) -> float:
            return margin_top + plot_height - (elevation - bottom) * scale

        degree_x = degree_y = scale
        placer = LabelPlacer(canvas, (margin_left, margin_top, width - margin_right, height - margin_bottom))

        canvas.create_rectangle(margin_left, margin_top, width - margin_right, height - margin_bottom,
                                outline=GRID_LINE)
        for tick in nice_ticks(left, right, max(3, int(plot_width / 90))):
            x = to_x(tick)
            canvas.create_line(x, height - margin_bottom, x, height - margin_bottom + 4, fill=GRID_LINE)
            canvas.create_text(x, height - margin_bottom + 5, text=f"{tick % 360:.0f}°", anchor="n",
                               fill=MUTED, font=FONT_SMALL)
        for tick in nice_ticks(bottom, top, max(3, int(plot_height / 50))):
            y = to_y(tick)
            canvas.create_line(margin_left - 4, y, margin_left, y, fill=GRID_LINE)
            canvas.create_text(margin_left - 6, y, text=f"{tick:.0f}°", anchor="e", fill=MUTED, font=FONT_SMALL)

        if coverage is not None:
            canvas.create_rectangle(to_x(coverage[0]), to_y(coverage[3]), to_x(coverage[1]), to_y(coverage[2]),
                                    outline=MUTED, dash=(6, 4), width=2)
            item = canvas.create_text(to_x(coverage[1]) - 4, to_y(coverage[3]) + 4, text="추적 영역",
                                      anchor="ne", fill=MUTED, font=FONT_SMALL)
            box = canvas.bbox(item)
            if box:
                placer.reserve(box)

        now = state.now_ms()
        if now is None:
            canvas.create_text(width / 2, height / 2, text="빔을 기다리는 중", fill=MUTED, font=FONT_SMALL)
            return
        search_afterglow = SEARCH_AFTERGLOW_MS[mode]
        latest_search = next((beam for beam in reversed(state.beams) if beam.beam_type == BeamType.SEARCH), None)
        # 탐색 빔은 실제 빔 범위(지름 = 빔 폭)의 원으로 그린다. 모의기는 방위·고각 차이를 빔 폭으로 나눈
        # 타원식으로 빔 안을 판정하므로, 같은 축척의 방위-고각 평면에서는 이 원 안이 탐지 범위다.
        # 빔 폭(6°)이 격자 간격(4.2°, 4.4°)보다 넓어 원이 서로 겹친다.
        search_color = BEAM_COLORS[BeamType.SEARCH]
        cells = sorted(state.search_cells.items(), key=lambda item: item[1].last_ms)
        for _, cell in cells:
            radius = cell.width_deg / 2 * scale
            x, y = to_x(cell.azimuth_deg), to_y(cell.elevation_deg)
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                               outline=blend(search_color, 0.22, BACKGROUND), width=1)
            canvas.create_oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5, fill=blend(search_color, 0.45, BACKGROUND),
                               outline="")
        # 잔상: 오래된 빔부터 그려 최근 빔이 위에 오게 한다.
        for beam_id, cell in cells:
            ratio = 1.0 - (now - cell.last_ms) / search_afterglow
            if ratio <= 0:
                continue
            radius = cell.width_deg / 2 * scale
            x, y = to_x(cell.azimuth_deg), to_y(cell.elevation_deg)
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                               fill=blend(search_color, 0.15 + ratio * 0.6, BACKGROUND),
                               outline=blend(search_color, 0.4 + ratio * 0.6, BACKGROUND), width=1)
        if latest_search is not None and latest_search.beam_id in state.search_cells:
            cell = state.search_cells[latest_search.beam_id]
            radius = cell.width_deg / 2 * scale
            x, y = to_x(cell.azimuth_deg), to_y(cell.elevation_deg)
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline=TEXT, width=2)
            canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=TEXT, outline="")

        request_afterglow = REQUEST_AFTERGLOW_MS[mode]
        track_ids = {track.track_id for track in state.tracks}
        for beam in state.recent_beams(request_afterglow):
            if beam.beam_type == BeamType.SEARCH:
                continue
            ratio = 1.0 - (now - beam.timestamp_ms) / request_afterglow
            color = beam_color(beam.beam_type)
            x, y = to_x(state.unwrap_azimuth(beam.azimuth_deg)), to_y(beam.elevation_deg)
            radius_x = max(beam.azimuth_width_deg / 2 * degree_x, 4)
            radius_y = max(beam.elevation_width_deg / 2 * degree_y, 4)
            canvas.create_oval(x - radius_x, y - radius_y, x + radius_x, y + radius_y,
                               fill=blend(color, max(ratio, 0) * 0.85, BACKGROUND), outline=color, width=2)
            placer.reserve((x - radius_x, y - radius_y, x + radius_x, y + radius_y))
            if beam.beam_id not in track_ids:
                placer.place(x + radius_x, y, f"#{beam.beam_id}", gap=3, color=color)

        for track in state.tracks:
            x, y = to_x(state.unwrap_azimuth(track.azimuth_deg)), to_y(track.elevation_deg)
            color = track_color(track.track_id)
            size = 6
            canvas.create_polygon(x, y - size, x + size, y, x, y + size, x - size, y, fill=color,
                                  outline=BACKGROUND)
            placer.reserve((x - size, y - size, x + size, y + size))
            placer.place(x, y, f"#{track.track_id}", gap=size + 3, color=TEXT)

    def _draw_schedule(self, state: MonitorState) -> None:
        interval = self._parameters.tracking_interval_ms
        rows = []
        for row in reversed(state.request_schedule(interval)):
            if not row.active:
                tag = "muted"
            elif row.deviation_ms is None:
                tag = "text"
            else:
                tag = "ok" if abs(row.deviation_ms) <= 20 else ("warn" if abs(row.deviation_ms) <= 100 else "bad")
            rows.append((str(row.track_id), (
                f"#{row.track_id}", beam_name(row.last_type), format_time(row.last_ms),
                "-" if row.interval_ms is None else f"{row.interval_ms} ms",
                "-" if row.deviation_ms is None else f"{row.deviation_ms:+d} ms",
                format_time(row.next_ms) if row.next_ms is not None else "종료",
            ), tag))
        self._schedule_title.configure(text=f"트랙별 요청 빔 일정 (설정 주기 {interval} ms)")
        self._schedule.set_rows(rows)

    def _draw_timeline(self, state: MonitorState, mode: str) -> None:
        window = TIMELINE_WINDOW_MS[mode]
        self._timeline_title.configure(text=f"빔 타임라인 — 최근 {window / 1000:g}초, 오른쪽 끝이 지금")
        canvas = self._timeline
        canvas.delete("all")
        width, height = canvas_size(canvas, 200, 80)
        label_width = 56
        plot_width = width - label_width - 8
        lane_height = (height - 22) / 3
        lanes = {BeamType.SEARCH: 0, BeamType.CONFIRMATION: 1, BeamType.TRACKING: 2}
        for beam_type, index in lanes.items():
            top = index * lane_height
            canvas.create_rectangle(label_width, top + 1, label_width + plot_width, top + lane_height - 1,
                                    fill=PANEL, outline="")
            canvas.create_text(label_width - 8, top + lane_height / 2, text=BEAM_NAMES[beam_type], fill=MUTED,
                               anchor="e", font=FONT_SMALL)
        for tick in nice_ticks(-window, 0, 6):
            x = label_width + plot_width + tick / window * plot_width
            canvas.create_line(x, height - 20, x, height - 16, fill=GRID_LINE)
            canvas.create_text(x, height - 15, text="지금" if tick == 0 else f"{tick / 1000:g}s",
                               anchor="ne" if tick == 0 else ("nw" if tick == -window else "n"),
                               fill=MUTED, font=FONT_SMALL)
        now = state.now_ms()
        if now is None:
            return
        slot_width = plot_width / window * BEAM_SLOT_MS
        bar_width = max(slot_width * 0.8, 1.0)
        placer = LabelPlacer(canvas)
        labels = []
        for beam in state.recent_beams(window):
            index = lanes.get(BeamType(beam.beam_type)) if beam.beam_type in (1, 2, 3) else None
            if index is None:
                continue
            top = index * lane_height
            x = label_width + plot_width - (now - beam.timestamp_ms) / window * plot_width
            canvas.create_rectangle(x - bar_width, top + 4, x, top + lane_height - 4,
                                    fill=beam_color(beam.beam_type), outline="")
            if beam.beam_type != BeamType.SEARCH:
                placer.reserve((x - bar_width - 1, top + 4, x + 1, top + lane_height - 4))
                labels.append((x, top + lane_height / 2, f"#{beam.beam_id}"))
        # 막대를 모두 그린 뒤 막대와 겹치지 않는 자리에 ID를 쓴다.
        for x, y, text in labels:
            placer.place(x, y, text, gap=4, color=TEXT)

        missed = [time_ms for time_ms, _ in state.beam_gaps if time_ms >= now - window]
        for time_ms in missed:
            x = label_width + plot_width - (now - time_ms) / window * plot_width
            canvas.create_line(x, 0, x, height - 22, fill=LEVEL_COLORS["bad"], dash=(2, 2))

