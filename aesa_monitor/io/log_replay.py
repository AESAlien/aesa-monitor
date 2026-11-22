"""녹화가 없을 때 처리부 로그로 재생 이벤트를 만든다.

처리부 탐지 CSV(adp_detection_*.csv)에는 빔 종류, 요청 ID, 송신 시각, 탐지(플롯)만 있다. 빔 방향, 트랙 목록,
레이더 자세는 없으므로 아래처럼 채운다. 채운 값은 모두 모니터 추정이고, 화면에는 '로그 재생'으로 알린다.

- 탐색 빔 방향: 탐색 빔의 요청 ID는 처리부 탐색 격자 번호다(SearchGrid). .env의 BEAM_SEARCH_* 격자(안테나 기준)를
  탐색 탐지 방향에 맞춰 돌려 ENU 방향으로 바꾼다. 탐지가 여러 행·열에 퍼져 있으면 3축 회전(Horn 사원수 방법),
  좁게 모여 있으면 방위·고각 차이만 맞춘다. 맞춘 결과로 레이더 정면 방위(자세 yaw 자리)도 정한다.
  탐지는 빔 안(빔 폭 6°) 어디에나 있을 수 있으므로 맞춘 격자도 그만큼 어긋날 수 있다(describe()에 차이를 적는다).
- 확인 빔 방향: 처리부처럼 확인 지연(TARGET_CONFIRMATION_DELAY_MS) 앞 탐색 빔의 탐지 방향.
- 추적 빔 방향: 탐지가 있으면 그 탐지 방향(처리부 예측 대신), 없으면 등속 외삽한 예측 방향.
  그래서 로그 재생의 잔차·빔 안 위치는 처리부 예측과의 차이가 아니다(화면에서 계산하지 않는다).
- 트랙 목록: 확인 빔 뒤에 같은 ID의 추적 빔이 나간 경우만 트랙으로 본다. 탐지가 있으면 그 위치로 갱신하고,
  없으면 미탐(트랙 시각을 그대로 둔다). 그 ID의 마지막 빔 뒤에 목록에서 뺀다(로그 끝까지 이어진 트랙은 남긴다).
  위치는 처리부 추정이 아니라 측정값이고, 속력·진행 방향은 연속한 두 갱신 위치의 차이다.
- 운용 ON/OFF, 응답 없는 트랙 삭제: 메시지 로그에 있으면 운용 명령·빔 명령 카운트·트랙 목록에 반영한다.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence, Tuple

from aesa_monitor.core.config import BeamGeometry, TrackingParameters
from aesa_monitor.core.events import SOURCE_LOG, ConnectionChanged, Event
from aesa_monitor.core.geometry import Vector, direction_vector, position_m, wrap_degrees
from aesa_monitor.core.protocol import (
    Beam, BeamType, Detection, OperatorControl, Plot, RadarAttitude, Track, TrackList, TrackStatus,
)
from aesa_monitor.io.processor_log import CsvDetection, CsvPlot, DetectionLog, MessageLog
from aesa_monitor.io.recording import Recording

# 메시지 로그에서 읽는 줄. 현재 처리부는 "Beam applied operation state: ON", 이전 처리부는 "Operation state changed: ON".
OPERATION_PATTERN = re.compile(r"(?:Beam applied operation state|Operation state changed): (ON|OFF)\b")
UNANSWERED_PATTERN = re.compile(r"Removed unanswered track: id=(\d+)")
# 격자 맞춤에 쓰는 (격자 칸, 탐지) 쌍의 최대 개수. 넘으면 고르게 덜어 낸다.
FIT_MAX_PAIRS = 4000
# 3축 회전을 맞추려면 탐지가 이만큼 서로 다른 열·행에 있어야 한다. 아니면 방위·고각 차이만 맞춘다.
FIT_ROTATION_MIN_COLUMNS = 3
FIT_ROTATION_MIN_ROWS = 2
# 확인 빔 시각 − 확인 지연 근처에서 탐색 빔을 찾는 범위(탐색 빔은 10 ms 간격).
CONFIRMATION_MATCH_MS = 15
# 마지막 빔이 로그 끝에서 추적 주기 × 이 값 안이면 로그 끝까지 이어진 트랙으로 본다.
TRACK_END_MARGIN = 1.5

Quaternion = Tuple[float, float, float, float]


# 방향·회전 계산

def direction_of(vector: Vector) -> Tuple[float, float]:
    """ENU 벡터의 (방위 0~360, 고각)."""
    east, north, up = vector
    horizontal = math.hypot(east, north)
    return math.degrees(math.atan2(east, north)) % 360.0, math.degrees(math.atan2(up, horizontal))


def angle_between_deg(first: Tuple[float, float], second: Tuple[float, float]) -> float:
    a = direction_vector(*first)
    b = direction_vector(*second)
    cosine = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(cosine))


def rotate(quaternion: Quaternion, vector: Vector) -> Vector:
    w, x, y, z = quaternion
    vx, vy, vz = vector
    # t = 2 (q × v), v' = v + w t + q × t
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def symmetric_eigen(matrix: Sequence[Sequence[float]]) -> Tuple[List[float], List[List[float]]]:
    """대칭 행렬의 고윳값과 고유벡터(열)를 야코비 회전으로 구한다. 4×4 사원수 행렬용이라 작은 행렬만 쓴다."""
    size = len(matrix)
    a = [list(map(float, row)) for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]
    for _ in range(60):
        off = sum(a[i][j] ** 2 for i in range(size) for j in range(size) if i != j)
        if off < 1e-22:
            break
        for p in range(size - 1):
            for q in range(p + 1, size):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(size):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(size):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(size):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    return [a[i][i] for i in range(size)], v


def fit_rotation(pairs: Sequence[Tuple[Vector, Vector]]) -> Quaternion:
    """Σ|R·a − b|²가 가장 작은 회전 R(a → b)을 Horn의 사원수 방법으로 구한다."""
    s = [[0.0] * 3 for _ in range(3)]
    for a, b in pairs:
        for i in range(3):
            for j in range(3):
                s[i][j] += a[i] * b[j]
    (sxx, sxy, sxz), (syx, syy, syz), (szx, szy, szz) = s
    n = [
        [sxx + syy + szz, syz - szy, szx - sxz, sxy - syx],
        [syz - szy, sxx - syy - szz, sxy + syx, szx + sxz],
        [szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy],
        [sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz],
    ]
    values, vectors = symmetric_eigen(n)
    best = max(range(4), key=lambda index: values[index])
    q = [vectors[row][best] for row in range(4)]
    norm = math.sqrt(sum(value * value for value in q)) or 1.0
    w, x, y, z = (value / norm for value in q)
    return (w, x, y, z) if w >= 0 else (-w, -x, -y, -z)


# 탐색 격자 맞춤

@dataclass(frozen=True)
class GridFit:
    """안테나 기준 탐색 격자를 ENU 방향으로 바꾸는 방법. 탐색 탐지로 맞춘 모니터 추정이다."""

    method: str                       # "rotation"(3축 회전), "offset"(방위·고각 차이), "none"(탐지 없음)
    rotation: Optional[Quaternion] = None
    azimuth_offset_deg: float = 0.0
    elevation_offset_deg: float = 0.0
    residual_deg: Optional[float] = None   # 맞춘 격자 칸 중심과 탐색 탐지 사이 각도 차이의 중앙값
    pairs: int = 0
    cells: int = 0

    def to_enu(self, azimuth_deg: float, elevation_deg: float) -> Tuple[float, float]:
        if self.method == "rotation" and self.rotation is not None:
            return direction_of(rotate(self.rotation, direction_vector(azimuth_deg, elevation_deg)))
        return ((azimuth_deg + self.azimuth_offset_deg) % 360.0,
                max(-90.0, min(90.0, elevation_deg + self.elevation_offset_deg)))

    @property
    def facing_deg(self) -> float:
        """안테나 정면(방위 0°, 고각 0°)의 ENU 방위. 레이더 자세의 yaw 자리에 쓴다."""
        return self.to_enu(0.0, 0.0)[0]

    def describe(self) -> str:
        if self.method == "none":
            return "탐색 탐지가 없어 격자 방향을 맞추지 못함 (레이더 정면 0°로 가정)"
        kind = "3축 회전" if self.method == "rotation" else "방위·고각 차이"
        residual = "" if self.residual_deg is None else f", 칸 중심과 탐지 차이 중앙값 {self.residual_deg:.1f}°"
        return (f"탐색 격자 방향: 탐색 탐지 {self.pairs}개({self.cells}칸)로 {kind} 맞춤{residual}, "
                f"레이더 정면 {self.facing_deg:.1f}° (추정)")


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def fit_search_grid(geometry: BeamGeometry, detections: Sequence[CsvDetection]) -> GridFit:
    """탐색 탐지 방향에 격자 칸 중심을 맞춘다."""
    pairs: List[Tuple[int, Tuple[float, float], Tuple[float, float]]] = []
    for detection in detections:
        if detection.beam_type != BeamType.SEARCH or not detection.plots:
            continue
        cell = geometry.search_cell(detection.request_id)
        if cell is None:
            continue
        for plot in detection.plots:
            pairs.append((detection.request_id, cell, (plot.azimuth_deg, plot.elevation_deg)))
    if not pairs:
        return GridFit("none")
    if len(pairs) > FIT_MAX_PAIRS:
        step = len(pairs) / FIT_MAX_PAIRS
        pairs = [pairs[int(index * step)] for index in range(FIT_MAX_PAIRS)]
    cells = {beam_id for beam_id, _, _ in pairs}

    # 방위·고각 차이: 방위 차이는 첫 값 기준으로 펼친 뒤 중앙값을 쓴다(0°/360° 경계).
    reference = wrap_degrees(pairs[0][2][0] - pairs[0][1][0])
    azimuth_offset = _median([reference + wrap_degrees(plot[0] - cell[0] - reference) for _, cell, plot in pairs])
    elevation_offset = _median([plot[1] - cell[1] for _, cell, plot in pairs])
    candidates = [GridFit("offset", None, azimuth_offset, elevation_offset)]

    columns = {beam_id % geometry.search_azimuth_count for beam_id in cells}
    rows = {beam_id // geometry.search_azimuth_count for beam_id in cells}
    if ((len(columns) >= FIT_ROTATION_MIN_COLUMNS and len(rows) >= FIT_ROTATION_MIN_ROWS) or
            (len(rows) >= FIT_ROTATION_MIN_COLUMNS and len(columns) >= FIT_ROTATION_MIN_ROWS)):
        rotation = fit_rotation([(direction_vector(*cell), direction_vector(*plot)) for _, cell, plot in pairs])
        candidates.append(GridFit("rotation", rotation))

    best: Optional[GridFit] = None
    for candidate in candidates:
        residual = _median([angle_between_deg(candidate.to_enu(*cell), plot) for _, cell, plot in pairs])
        scored = GridFit(candidate.method, candidate.rotation, candidate.azimuth_offset_deg,
                         candidate.elevation_offset_deg, residual, len(pairs), len(cells))
        if best is None or residual < (best.residual_deg or 0.0):
            best = scored
    assert best is not None
    return best


# 트랙

@dataclass
class _TrackState:
    track_id: int
    position: Vector
    time_ms: int
    plot: CsvPlot
    velocity: Optional[Vector] = None
    track: Optional[Track] = None

    def predict(self, time_ms: int) -> Vector:
        if self.velocity is None:
            return self.position
        seconds = (time_ms - self.time_ms) / 1000.0
        return tuple(p + v * seconds for p, v in zip(self.position, self.velocity))  # type: ignore[return-value]

    def update(self, position: Vector, time_ms: int, plot: CsvPlot) -> None:
        seconds = (time_ms - self.time_ms) / 1000.0
        if seconds > 0:
            self.velocity = tuple((new - old) / seconds for new, old in zip(position, self.position))  # type: ignore
        self.position, self.time_ms, self.plot = position, time_ms, plot
        self.track = self._make_track()

    def _make_track(self) -> Track:
        east, north, up = self.position
        horizontal = math.hypot(east, north)
        speed = heading = path_angle = 0.0
        if self.velocity is not None:
            ve, vn, vu = self.velocity
            speed = math.sqrt(ve * ve + vn * vn + vu * vu)
            heading = math.degrees(math.atan2(ve, vn)) % 360.0
            path_angle = math.degrees(math.atan2(vu, math.hypot(ve, vn)))
        return Track(
            track_id=self.track_id, status=TrackStatus.TRACKING,
            slant_range_km=self.plot.slant_range_m / 1000.0, ground_range_km=horizontal / 1000.0,
            azimuth_deg=self.plot.azimuth_deg, elevation_deg=self.plot.elevation_deg,
            doppler_mps=self.plot.doppler_mps, latitude_deg=0.0, longitude_deg=0.0, altitude_km=up / 1000.0,
            speed_mps=speed, heading_deg=heading, flight_path_angle_deg=path_angle, timestamp_ms=self.time_ms,
        )


def _plot_position(plot: CsvPlot) -> Vector:
    return position_m(plot.slant_range_m / 1000.0, plot.azimuth_deg, plot.elevation_deg)


def _to_plot(plot: CsvPlot) -> Plot:
    return Plot(plot.slant_range_m / 1000.0, plot.azimuth_deg, plot.elevation_deg, plot.doppler_mps, plot.power_db)


# 녹화처럼 재생할 수 있는 묶음

@dataclass
class LogRecording(Recording):
    """처리부 로그로 만든 재생 이벤트. ReplayPlayer가 모니터 녹화처럼 재생한다."""

    built_events: List[Tuple[float, Event]] = field(default_factory=list)
    fit: Optional[GridFit] = None
    notes: List[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.built_events[-1][0] if self.built_events else 0.0

    def events(self) -> Tuple[List[Tuple[float, Event]], int]:
        return list(self.built_events), 0


def _log_actions(message_log: Optional[MessageLog]) -> List[Tuple[float, str, int]]:
    """메시지 로그의 (처리부 시각 ms, 종류, 값). 종류: "on", "off", "unanswered"(값 = 트랙 ID)."""
    actions: List[Tuple[float, str, int]] = []
    if message_log is None:
        return actions
    last_state: Optional[str] = None
    for event in message_log.events:
        if event.processor_ms is None:
            continue
        match = OPERATION_PATTERN.search(event.text)
        if match:
            # 빔·타겟 워커가 같은 전환을 따로 적으므로 같은 상태가 이어지면 한 번만 쓴다.
            state = match.group(1).lower()
            if state != last_state:
                actions.append((event.processor_ms, state, 0))
                last_state = state
            continue
        match = UNANSWERED_PATTERN.search(event.text)
        if match:
            actions.append((event.processor_ms, "unanswered", int(match.group(1))))
    actions.sort(key=lambda action: action[0])
    return actions


def build_log_recording(detection_log: DetectionLog, message_log: Optional[MessageLog],
                        geometry: BeamGeometry = BeamGeometry(),
                        parameters: TrackingParameters = TrackingParameters()) -> LogRecording:
    """탐지 CSV(필수)와 메시지 로그(있으면)로 재생 이벤트를 만든다. 시각은 첫 탐지 기준 초다."""
    detections = sorted(detection_log.detections, key=lambda detection: detection.time_ms)
    meta = {
        "source": SOURCE_LOG,
        "detection_log": detection_log.path.name,
        "message_log": message_log.path.name if message_log is not None else None,
    }
    recording = LogRecording(detection_log.path, meta, [])
    if not detections:
        recording.notes.append("탐지 CSV가 비어 있어 재생할 것이 없음")
        return recording

    fit = fit_search_grid(geometry, detections)
    recording.fit = fit
    meta["grid_fit"] = fit.describe()
    origin_ms = detections[0].time_ms
    end_ms = detections[-1].time_ms
    interval_ms = parameters.tracking_interval_ms

    def seconds(time_ms: float) -> float:
        return max(time_ms - origin_ms, 0.0) / 1000.0

    events: List[Tuple[float, Event]] = [
        (0.0, ConnectionChanged(True, "처리부 로그 재생", SOURCE_LOG)),
        (0.0, RadarAttitude(0.0, 0.0, 0.0, 0.0, 0.0, fit.facing_deg)),
    ]
    tracking_ids = {d.request_id for d in detections if d.beam_type == BeamType.TRACKING}
    last_ms: Dict[int, int] = {}
    for detection in detections:
        if detection.beam_type != BeamType.SEARCH:
            last_ms[detection.request_id] = detection.time_ms

    actions = _log_actions(message_log)
    tracks: Dict[int, _TrackState] = {}
    recent_search: Deque[CsvDetection] = deque()
    command_count = 0
    reset_count = False
    for detection in detections:
        now_ms = detection.time_ms
        time_s = seconds(now_ms)

        # 이 빔 전에 일어난 메시지 로그의 운용 전환·트랙 삭제
        while actions and actions[0][0] <= now_ms:
            action_ms, kind, value = actions.pop(0)
            action_s = seconds(action_ms)
            if kind == "off":
                events.append((action_s, OperatorControl(0)))
                if tracks:
                    tracks.clear()
                    events.append((action_s, TrackList([])))
            elif kind == "on":
                events.append((action_s, OperatorControl(1)))
                reset_count = command_count > 0
            elif kind == "unanswered" and value in tracks:
                del tracks[value]
                events.append((action_s, TrackList([state.track for state in tracks.values() if state.track])))
        command_count = 1 if reset_count else command_count + 1
        reset_count = False

        beam_type = detection.beam_type
        selected: Optional[CsvPlot] = None
        if beam_type == BeamType.SEARCH:
            cell = geometry.search_cell(detection.request_id)
            if cell is not None:
                direction = fit.to_enu(*cell)
            elif detection.plots:
                direction = (detection.plots[0].azimuth_deg, detection.plots[0].elevation_deg)
            else:
                direction = (fit.facing_deg, 0.0)
            width = geometry.search_width_deg
            recent_search.append(detection)
            while recent_search and recent_search[0].time_ms < now_ms - parameters.confirmation_delay_ms - 200:
                recent_search.popleft()
        elif beam_type == BeamType.CONFIRMATION:
            direction = _confirmation_direction(detection, recent_search, parameters, geometry, fit)
            width = geometry.confirmation_width_deg
            if detection.plots:
                selected = min(detection.plots, key=lambda plot: angle_between_deg(
                    direction, (plot.azimuth_deg, plot.elevation_deg)))
        else:
            state = tracks.get(detection.request_id)
            predicted = state.predict(now_ms) if state is not None else None
            if detection.plots:
                if predicted is not None:
                    selected = min(detection.plots, key=lambda plot: sum(
                        (a - b) ** 2 for a, b in zip(_plot_position(plot), predicted)))
                else:
                    selected = detection.plots[0]
                direction = (selected.azimuth_deg, selected.elevation_deg)
            elif predicted is not None:
                direction = direction_of(predicted)
            else:
                direction = (fit.facing_deg, 0.0)
            width = geometry.tracking_width_deg

        events.append((time_s, Beam(beam_type, now_ms, detection.request_id, command_count,
                                    direction[0], direction[1], width, width)))
        events.append((time_s, Detection(beam_type, now_ms, detection.request_id,
                                         [_to_plot(plot) for plot in detection.plots])))
        if beam_type == BeamType.SEARCH:
            continue

        track_id = detection.request_id
        state = tracks.get(track_id)
        if selected is not None and (beam_type == BeamType.TRACKING or track_id in tracking_ids):
            position = _plot_position(selected)
            if state is None:
                state = tracks[track_id] = _TrackState(track_id, position, now_ms, selected)
                state.track = state._make_track()
            else:
                state.update(position, now_ms, selected)
        if (track_id in tracks and now_ms >= last_ms.get(track_id, now_ms)
                and now_ms + interval_ms * TRACK_END_MARGIN < end_ms):
            del tracks[track_id]
        events.append((time_s, TrackList([state.track for state in tracks.values() if state.track is not None])))

    recording.built_events = events
    return recording


def _confirmation_direction(detection: CsvDetection, recent_search: Sequence[CsvDetection],
                            parameters: TrackingParameters, geometry: BeamGeometry,
                            fit: GridFit) -> Tuple[float, float]:
    """확인 빔 방향: 처리부처럼 확인 지연 앞 탐색 탐지 방향. 못 찾으면 확인 탐지, 그다음 그 탐색 칸 방향."""
    target_ms = detection.time_ms - parameters.confirmation_delay_ms
    nearby = [search for search in recent_search if abs(search.time_ms - target_ms) <= CONFIRMATION_MATCH_MS]
    with_plots = [search for search in nearby if search.plots]
    if with_plots:
        search = min(with_plots, key=lambda item: abs(item.time_ms - target_ms))
        plots = search.plots
        if detection.plots:
            reference = (detection.plots[0].azimuth_deg, detection.plots[0].elevation_deg)
            plot = min(plots, key=lambda item: angle_between_deg(reference, (item.azimuth_deg, item.elevation_deg)))
        else:
            plot = plots[0]
        return plot.azimuth_deg, plot.elevation_deg
    if detection.plots:
        return detection.plots[0].azimuth_deg, detection.plots[0].elevation_deg
    if nearby:
        cell = geometry.search_cell(min(nearby, key=lambda item: abs(item.time_ms - target_ms)).request_id)
        if cell is not None:
            return fit.to_enu(*cell)
    return fit.facing_deg, 0.0
