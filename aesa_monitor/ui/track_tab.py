"""추적 탭 — "표적을 잘 따라가고 있나?" (이전 타겟 탭 + 추적 탭)

왼쪽: 트랙 목록과 위에서 본 트랙 위치.
오른쪽: 고른 트랙의 빔-탐지 차이 추이, 고른 사이클의 예측 → 관측·연관 → 갱신 결과.

처리부 내부 값(예측 거리, 공분산, NDS 점수)은 받지 않으므로 연관된 탐지와 차이는 모니터의 추정이다.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.geometry import wrap_degrees
from aesa_monitor.core.protocol import BeamType
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.tracking import OUTCOME_NAMES, Outcome, TrackingCycle, track_position
from aesa_monitor.core.verdict import track_verdict
from aesa_monitor.ui.style import (
    BACKGROUND, BAD, CANDIDATE_COLOR, DETECTED_COLOR, ESTIMATE_COLOR, FONT_BODY, FONT_SMALL, FONT_TITLE,
    FONT_VALUE, GRID_LINE, MUTED, OK, PANEL, PREDICTED_COLOR, TEXT, UNKNOWN, WARN, beam_name, format_number,
    format_time, track_color, track_status_name,
)
from aesa_monitor.ui.widgets import (
    ChartArea, HelpButton, LabelPlacer, Table, VerdictBar, canvas_size, empty_message, nice_ticks, section_title,
)

OUTCOME_COLORS = {Outcome.PENDING: UNKNOWN, Outcome.UPDATED: OK, Outcome.MISSED: WARN, Outcome.ENDED: BAD}
TREND_CYCLES = 120
MAP_TRAIL_CYCLES = 30


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
        "'빔-탐지 차이'는 빔 축과 탐지 사이의 수직 거리(각도 차이 × 탐지 거리)다. "
        "연관된 탐지는 빔 축에 가장 가까운 탐지로 추정하므로 처리부가 고른 것과 다를 수 있다.\n"
        "미탐·갱신은 트랙 목록의 트랙 시각이 빔 송신 시각과 같은지로 판단한다."
    )


class StepCard:
    """사이클 한 단계: 색 제목, 큰 값, 설명 줄."""

    def __init__(self, parent: tk.Widget, title: str, color: str) -> None:
        self.frame = tk.Frame(parent, background=PANEL, highlightthickness=2, highlightbackground=color)
        self._title = tk.Label(self.frame, text=title, font=FONT_TITLE, background=color, foreground=BACKGROUND,
                               anchor="w", padx=8, pady=3)
        self._title.pack(fill=tk.X)
        self._value = tk.Label(self.frame, text="-", font=FONT_VALUE, background=PANEL, foreground=TEXT,
                               anchor="w", padx=8)
        self._value.pack(fill=tk.X)
        self._lines = tk.Label(self.frame, text="", font=FONT_BODY, background=PANEL, foreground=TEXT,
                               anchor="nw", justify=tk.LEFT, padx=8, pady=4)
        self._lines.pack(fill=tk.BOTH, expand=True)
        self.frame.bind("<Configure>", lambda event: self._lines.configure(wraplength=max(event.width - 20, 80)))

    def set(self, value: str, lines: List[str], value_color: str = TEXT) -> None:
        self._value.configure(text=value, foreground=value_color)
        self._lines.configure(text="\n".join(line for line in lines if line))


class TrackTab:
    def __init__(self, parent: tk.Widget, parameters: TrackingParameters) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND, padx=12, pady=8)
        self._parameters = parameters
        self._track_id: Optional[int] = None
        self._cycle_index: Optional[int] = None
        self._follow_latest = tk.BooleanVar(value=True)
        self._trend_area: Optional[ChartArea] = None
        self._trend_cycles: List[Tuple[float, int]] = []
        self._map_points: List[Tuple[float, float, int]] = []

        top = tk.Frame(self.frame, background=BACKGROUND)
        top.pack(fill=tk.X)
        self._verdict = VerdictBar(top, "표적을 잘 따라가고 있나?")
        self._verdict.frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        HelpButton(top, "처리부 추적 계산", lambda: algorithm_text(self._parameters)).pack(side=tk.LEFT, padx=(8, 0))

        body = tk.Frame(self.frame, background=BACKGROUND)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        body.columnconfigure(0, weight=2, uniform="track")
        body.columnconfigure(1, weight=3, uniform="track")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, background=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        section_title(left, "트랙 목록", "처리부가 확인·추적 빔을 보낸 요청 ID별 요약입니다.\n"
                      "갱신/미탐: 추적 빔 결과 수. 차이 RMS: 빔 축과 연관 탐지 사이 거리의 RMS(m).\n"
                      "행을 누르면 오른쪽에 그 트랙을 보여줍니다.")
        self._table = Table(left, (
            ("id", "ID", 50, tk.CENTER), ("status", "상태", 60, tk.CENTER), ("life", "수명", 70, tk.E),
            ("result", "갱신/미탐", 90, tk.E), ("misses", "연속 미탐", 80, tk.E), ("rms", "차이 RMS", 90, tk.E),
        ), height=7, on_select=self._on_track_selected)
        self._table.frame.pack(fill=tk.BOTH, expand=False)
        section_title(left, "위에서 본 트랙 (동·북)", "처리부 트랙 목록의 갱신 위치를 위에서 본 그림입니다. "
                      "가운데 +가 레이더, 꼬리는 최근 사이클의 갱신 위치입니다. 점을 누르면 그 트랙을 고릅니다.")
        self._map = tk.Canvas(left, background=BACKGROUND, highlightthickness=0, height=200)
        self._map.pack(fill=tk.BOTH, expand=True)
        self._map.bind("<Button-1>", self._on_map_click)

        right = tk.Frame(body, background=BACKGROUND)
        right.grid(row=0, column=1, sticky="nsew")
        self._trend_title = section_title(
            right, "빔-탐지 차이 추이",
            "고른 트랙의 사이클마다 처리부가 예측해 보낸 빔 방향과 실제 탐지 사이의 거리(m)입니다.\n"
            "작을수록 예측이 정확합니다. 점선은 연관 게이트 하한(√NDS 임계값 × 측정 잡음)입니다.\n"
            "● 갱신, × 미탐(탐지 없음은 아래쪽에 표시), ■ 종료. 그래프를 누르면 그 사이클을 아래에 보여줍니다.")
        self._trend = tk.Canvas(right, background=BACKGROUND, highlightthickness=0, height=150)
        self._trend.pack(fill=tk.X)
        self._trend.bind("<Button-1>", self._on_trend_click)

        navigation = tk.Frame(right, background=BACKGROUND)
        navigation.pack(fill=tk.X, pady=(6, 4))
        ttk.Button(navigation, text="◀", width=3, command=lambda: self._step(-1)).pack(side=tk.LEFT)
        ttk.Button(navigation, text="▶", width=3, command=lambda: self._step(1)).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(navigation, text="최신 따라가기", variable=self._follow_latest).pack(side=tk.LEFT, padx=8)
        self._cycle_label = tk.Label(navigation, text="", font=FONT_TITLE, background=BACKGROUND, foreground=TEXT,
                                     anchor="w")
        self._cycle_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        cards = tk.Frame(right, background=BACKGROUND)
        cards.pack(fill=tk.X)
        self._predict_card = StepCard(cards, "① 예측 (처리부가 보낸 빔)", PREDICTED_COLOR)
        self._observe_card = StepCard(cards, "② 관측·연관 (모의기 탐지)", DETECTED_COLOR)
        self._update_card = StepCard(cards, "③ 결과 (처리부 트랙)", ESTIMATE_COLOR)
        for index, card in enumerate((self._predict_card, self._observe_card, self._update_card)):
            card.frame.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            cards.columnconfigure(index, weight=1, uniform="step")

        section_title(right, "빔 단면 — 예측 방향을 가운데 두고 본 탐지 (m)",
                      "예측 빔 방향(가운데 ○)을 기준으로 탐지(●)와 갱신 결과(■)가 어디 있는지 "
                      "미터 단위로 그립니다. 오른쪽이 방위 증가, 위쪽이 고각 증가 방향입니다. "
                      "점선 원은 연관 게이트 하한입니다.")
        self._cross = tk.Canvas(right, background=BACKGROUND, highlightthickness=0, height=180)
        self._cross.pack(fill=tk.BOTH, expand=True)

    # 조작

    def _on_track_selected(self, iid: str) -> None:
        track_id = int(iid)
        if track_id != self._track_id:
            self._track_id = track_id
            self._cycle_index = None
            self._follow_latest.set(True)

    def _step(self, delta: int) -> None:
        self._follow_latest.set(False)
        if self._cycle_index is not None:
            self._cycle_index = max(0, self._cycle_index + delta)

    def _on_trend_click(self, event: tk.Event) -> None:
        if self._trend_area is None or not self._trend_cycles:
            return
        time_s = self._trend_area.x_value(event.x)
        nearest = min(self._trend_cycles, key=lambda item: abs(item[0] - time_s))
        self._follow_latest.set(False)
        self._cycle_index = nearest[1]

    def _on_map_click(self, event: tk.Event) -> None:
        if not self._map_points:
            return
        x, y, track_id = min(self._map_points, key=lambda item: math.hypot(item[0] - event.x, item[1] - event.y))
        if math.hypot(x - event.x, y - event.y) <= 20:
            self._on_track_selected(str(track_id))
            self._table.select(str(track_id))

    # 갱신

    def refresh(self, state: MonitorState) -> None:
        self._verdict.set(track_verdict(state, self._parameters))
        history = state.tracking
        track_ids = history.track_ids()
        if self._track_id is None or self._track_id not in track_ids:
            active = [track_id for track_id in track_ids if track_id in history.latest_tracks]
            self._track_id = (active or track_ids or [None])[-1]
            self._cycle_index = None
        self._draw_table(state, track_ids)
        if self._track_id is not None:
            self._table.select(str(self._track_id))
        self._draw_map(state)

        cycles = history.cycles(self._track_id) if self._track_id is not None else []
        if not cycles:
            self._cycle_label.configure(text="트랙을 기다리는 중 (확인·추적 빔의 탐지 결과가 필요합니다)",
                                        foreground=MUTED)
            for canvas in (self._trend, self._cross):
                canvas.delete("all")
            for card in (self._predict_card, self._observe_card, self._update_card):
                card.set("-", [])
            return
        if self._follow_latest.get() or self._cycle_index is None:
            self._cycle_index = len(cycles) - 1
        self._cycle_index = min(self._cycle_index, len(cycles) - 1)
        cycle = cycles[self._cycle_index]
        self._trend_title.configure(text=f"트랙 #{cycle.track_id} — 빔-탐지 차이 추이")
        self._cycle_label.configure(
            text=f"사이클 {self._cycle_index + 1}/{len(cycles)} · {beam_name(cycle.beam_type)} 빔 "
                 f"{format_time(cycle.time_ms)} → {OUTCOME_NAMES[cycle.outcome]}",
            foreground=OUTCOME_COLORS[cycle.outcome])
        self._draw_trend(cycles, cycle)
        self._draw_cards(cycles, cycle)
        self._draw_cross_section(cycle)

    def _draw_table(self, state: MonitorState, track_ids: List[int]) -> None:
        history = state.tracking
        limit = self._parameters.max_consecutive_tracking_misses
        now = state.now_ms()
        rows = []
        for track_id in reversed(track_ids):
            summary = history.summary(track_id)
            cycles = history.cycles(track_id)
            track = history.latest_tracks.get(track_id)
            status = "종료" if track is None else track_status_name(track.status)
            life = (now - cycles[0].time_ms) / 1000 if track is not None and now is not None else (
                (cycles[-1].time_ms - cycles[0].time_ms) / 1000)
            if track is None:
                tag = "muted"
            elif summary.consecutive_misses >= max(limit - 1, 1):
                tag = "bad"
            elif summary.consecutive_misses > 0:
                tag = "warn"
            else:
                tag = "ok"
            rows.append((str(track_id), (
                f"#{track_id}", status, f"{life:.0f} s", f"{summary.updated}/{summary.missed}",
                f"{summary.consecutive_misses}/{limit}", format_number(summary.rms_angle_error_m, 1, " m"),
            ), tag))
        self._table.set_rows(rows)

    def _draw_map(self, state: MonitorState) -> None:
        canvas = self._map
        canvas.delete("all")
        width, height = canvas_size(canvas, 160, 120)
        history = state.tracking
        trails = {}
        for track_id in history.track_ids():
            points = [track_position(cycle.estimate) for cycle in history.cycles(track_id)[-MAP_TRAIL_CYCLES:]
                      if cycle.estimate is not None]
            if track_id in history.latest_tracks:
                points.append(track_position(history.latest_tracks[track_id]))
            if points:
                trails[track_id] = [(point[0] / 1000, point[1] / 1000) for point in points]
        self._map_points = []
        if not trails:
            empty_message(canvas, "트랙 위치 없음")
            return
        extent = max(max(abs(value) for point in points for value in point) for points in trails.values()) * 1.15
        extent = max(extent, 5.0)
        center_x, center_y = width / 2, height / 2
        scale = (min(width, height) / 2 - 12) / extent
        canvas.create_rectangle(0, 0, width, height, fill=PANEL, outline="")
        for ring in nice_ticks(0, extent, 3)[1:]:
            radius = ring * scale
            canvas.create_oval(center_x - radius, center_y - radius, center_x + radius, center_y + radius,
                               outline=GRID_LINE)
            canvas.create_text(center_x + radius * 0.71, center_y - radius * 0.71, text=f"{ring:g} km",
                               fill=MUTED, font=FONT_SMALL, anchor="sw")
        canvas.create_line(center_x - 6, center_y, center_x + 6, center_y, fill=MUTED)
        canvas.create_line(center_x, center_y - 6, center_x, center_y + 6, fill=MUTED)
        canvas.create_text(width - 6, 6, text="북 ↑", fill=MUTED, anchor="ne", font=FONT_SMALL)
        placer = LabelPlacer(canvas, (0, 0, width, height))
        for track_id, points in trails.items():
            color = track_color(track_id) if track_id in history.latest_tracks else CANDIDATE_COLOR
            coordinates = [(center_x + east * scale, center_y - north * scale) for east, north in points]
            if len(coordinates) >= 2:
                canvas.create_line(*[value for point in coordinates for value in point], fill=color,
                                   width=3 if track_id == self._track_id else 1)
            x, y = coordinates[-1]
            radius = 6 if track_id == self._track_id else 4
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color,
                               outline=TEXT if track_id == self._track_id else "", width=2)
            placer.reserve((x - radius, y - radius, x + radius, y + radius))
            placer.place(x, y, f"#{track_id}", gap=radius + 2, color=TEXT)
            self._map_points.append((x, y, track_id))

    def _draw_trend(self, cycles: List[TrackingCycle], cycle: TrackingCycle) -> None:
        canvas = self._trend
        canvas.delete("all")
        width, height = canvas_size(canvas, 200, 100)
        index = cycles.index(cycle)
        start = max(0, min(index, len(cycles) - TREND_CYCLES))
        window = cycles[start:start + TREND_CYCLES]
        gate = math.sqrt(self._parameters.position_nds_threshold) * self._parameters.measurement_standard_deviation_m
        values = [item.selected.angle_error_m for item in window
                  if item.selected is not None and not math.isnan(item.selected.angle_error_m)]
        high = max(values + [gate * 1.5])
        times = [item.time_ms / 1000 for item in window]
        x_low, x_high = min(times), max(times)
        if x_high - x_low < 1:
            x_low, x_high = x_low - 1, x_high + 1
        area = ChartArea(canvas, (56, 18, width - 12, height - 24), (x_low, x_high), (0, high * 1.1))
        area.draw_axes(lambda value: f"{value:.0f}s", lambda value: f"{value:.0f}", "m", "처리부 시각")
        area.hline(gate, PREDICTED_COLOR, f"게이트 하한 {gate:.0f} m")
        self._trend_area = area
        self._trend_cycles = []
        line = []
        for offset, item in enumerate(window):
            x_value = item.time_ms / 1000
            self._trend_cycles.append((x_value, start + offset))
            selected = item.selected
            value = selected.angle_error_m if selected is not None else math.nan
            if item.outcome == Outcome.MISSED or selected is None:
                y_value = value if not math.isnan(value) else 0.0
                area.crosses([(x_value, y_value)], WARN)
                line.append((math.nan, math.nan))
                continue
            line.append((x_value, value))
            color = BAD if item.outcome == Outcome.ENDED else (OK if item.outcome == Outcome.UPDATED else UNKNOWN)
            area.markers([(x_value, value)], color)
        area.line(line, track_color(cycle.track_id), width=1)
        x = area.x(cycle.time_ms / 1000)
        canvas.create_line(x, area.top, x, area.bottom, fill=TEXT, width=1, dash=(2, 2))

    def _draw_cards(self, cycles: List[TrackingCycle], cycle: TrackingCycle) -> None:
        previous = cycle.previous_estimate
        if cycle.predicted is None:
            self._predict_card.set("빔 없음", ["빔 명령을 받지 못했습니다", "(모니터 큐에서 버려졌을 수 있음)"],
                                   UNKNOWN)
        else:
            elapsed = cycle.elapsed_ms
            lines = [
                f"직전 추정에서 +{elapsed} ms 뒤를 예측" if elapsed is not None else
                ("탐색 탐지 방향으로 확인 빔" if cycle.beam_type == BeamType.CONFIRMATION else "직전 추정 없음"),
                "" if cycle.beam_width is None else f"빔 폭 {cycle.beam_width[0]:.1f}°",
            ]
            if previous is not None:
                lines.append(f"직전 추정: {previous.slant_range_km:.2f} km, {previous.speed_mps:.0f} m/s")
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
                f"빔 안 위치 {offset:.2f} (1 넘으면 빔 밖)",
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
        # 오른쪽에 범례 자리를 남긴다.
        available = width - 260 if width > 520 else width
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
        placer = LabelPlacer(canvas, (center_x - half, center_y - half, center_x + half, center_y + half))
        for text, x, y, anchor in (("방위 +", center_x + half - 4, center_y + 4, "ne"),
                                   ("고각 +", center_x + 4, center_y - half + 4, "nw"),
                                   (f"±{extent:.0f} m", center_x - half + 4, center_y + half - 4, "sw")):
            item = canvas.create_text(x, y, text=text, fill=MUTED, anchor=anchor, font=FONT_SMALL)
            box = canvas.bbox(item)
            if box:
                placer.reserve(box)

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
        if legend_x + 160 > width:
            legend_x = 8
        y = center_y - half
        for text, color in legend:
            item = canvas.create_text(legend_x, y, text=text, fill=color, anchor="nw", font=FONT_BODY)
            box = canvas.bbox(item)
            y = (box[3] if box else y + 18) + 4
