"""처리부 로그로 만든 재생(io/log_replay.py)과 재생 중 전체 추적 기록."""

import math
import random
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from aesa_monitor.core.config import BeamGeometry, TrackingParameters, read_beam_geometry
from aesa_monitor.core.events import SOURCE_LOG
from aesa_monitor.core.geometry import direction_vector, position_m
from aesa_monitor.core.protocol import Beam, BeamType, OperatorControl, encode
from aesa_monitor.core.tracking import Outcome
from aesa_monitor.io.log_replay import (
    LogRecording, angle_between_deg, build_log_recording, direction_of, fit_rotation, fit_search_grid, rotate,
)
from aesa_monitor.io.processor_log import (
    DETECTION_HEADER, CsvDetection, CsvPlot, DetectionLog, read_detection_log, read_message_log,
)
from aesa_monitor.io.recording import RECORDING_NAME, Recorder
from aesa_monitor.io.replay import ReplayPlayer
from aesa_monitor.io.session import load_session

GEOMETRY = BeamGeometry()
PARAMETERS = TrackingParameters()


def axis_angle(axis: Tuple[float, float, float], degrees: float) -> Tuple[float, float, float, float]:
    norm = math.sqrt(sum(value * value for value in axis))
    half = math.radians(degrees) / 2.0
    return (math.cos(half),) + tuple(value / norm * math.sin(half) for value in axis)  # type: ignore[return-value]


def multiply(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw)


def attitude(yaw: float, pitch: float = 0.0, roll: float = 0.0):
    """안테나 → ENU 회전. 방위는 북쪽에서 시계방향이므로 yaw는 위(z)축 기준 −yaw 회전이다."""
    q = axis_angle((0.0, 1.0, 0.0), roll)                 # 정면(북)축 기준 기울기
    q = multiply(axis_angle((1.0, 0.0, 0.0), pitch), q)   # 동쪽축 기준: 정면이 위로
    return multiply(axis_angle((0.0, 0.0, 1.0), -yaw), q)


def enu_of_cell(beam_id: int, q) -> Tuple[float, float]:
    cell = GEOMETRY.search_cell(beam_id)
    assert cell is not None
    return direction_of(rotate(q, direction_vector(*cell)))


def search_detection(time_ms: int, beam_id: int, plots: Sequence[CsvPlot] = ()) -> CsvDetection:
    return CsvDetection(BeamType.SEARCH, beam_id, time_ms, list(plots))


def plot_at(azimuth: float, elevation: float, range_m: float = 60000.0) -> CsvPlot:
    return CsvPlot(range_m, azimuth, elevation, -200.0, 80.0)


def plot_of_position(position: Tuple[float, float, float]) -> CsvPlot:
    east, north, up = position
    range_m = math.sqrt(east * east + north * north + up * up)
    azimuth, elevation = direction_of(position)
    return CsvPlot(range_m, azimuth, elevation, -200.0, 80.0)


def write_csv(path: Path, detections: Sequence[CsvDetection]) -> None:
    names = {1: "SEARCH", 2: "CONFIRMATION", 3: "TRACKING"}
    lines = [DETECTION_HEADER]
    for detection in detections:
        prefix = f"{names[detection.beam_type]},{detection.request_id},{detection.time_ms},{len(detection.plots)}"
        if not detection.plots:
            lines.append(prefix + ",,,,,,")
        for index, plot in enumerate(detection.plots):
            lines.append(f"{prefix},{index},{plot.slant_range_m},{plot.azimuth_deg},{plot.elevation_deg},"
                         f"{plot.doppler_mps},{plot.power_db}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Scenario:
    """탐색 격자를 10 ms마다 돌며, 표적 하나를 찾아 확인·추적하는 처리부 로그를 만든다."""

    def __init__(self, q, misses: Sequence[int] = (), end_track_at: Optional[int] = None,
                 tracking_beams: int = 8) -> None:
        self.q = q
        self.detections: List[CsvDetection] = []
        rng = random.Random(3)
        # 탐색: 격자를 두 바퀴 돌고, 칸마다 가끔 탐지(칸 중심 근처)를 넣는다(격자 맞춤용).
        time_ms = 0
        for sweep in range(2):
            for beam_id in range(GEOMETRY.search_cell_count):
                plots = []
                if (beam_id * 7 + sweep) % 5 == 0:
                    azimuth, elevation = enu_of_cell(beam_id, q)
                    plots = [plot_at(azimuth + rng.uniform(-1.5, 1.5), elevation + rng.uniform(-1.5, 1.5))]
                self.detections.append(search_detection(time_ms, beam_id, plots))
                time_ms += 10
        # 표적: 탐색 칸 40에서 찾고 100 ms 뒤 확인, 1초마다 추적. 동쪽으로 300 m/s.
        found = time_ms
        start = position_m(60.0, *enu_of_cell(40, q))
        self.velocity = (300.0, 0.0, 0.0)

        def position(at_ms: int):
            seconds = (at_ms - found) / 1000.0
            return tuple(p + v * seconds for p, v in zip(start, self.velocity))

        self.detections.append(search_detection(found, 40, [plot_of_position(position(found))]))
        confirm = found + PARAMETERS.confirmation_delay_ms
        self.detections.append(CsvDetection(BeamType.CONFIRMATION, 1, confirm, [plot_of_position(position(confirm))]))
        self.tracking_times = []
        last = end_track_at if end_track_at is not None else tracking_beams
        for index in range(1, last + 1):
            at = confirm + index * PARAMETERS.tracking_interval_ms
            plots = [] if index in misses else [plot_of_position(position(at))]
            self.detections.append(CsvDetection(BeamType.TRACKING, 1, at, plots))
            self.tracking_times.append(at)
        # 로그 끝까지 탐색 빔을 이어 둔다(트랙이 로그 끝 전에 끝났는지 가리려고).
        end = confirm + (tracking_beams + 3) * PARAMETERS.tracking_interval_ms
        beam_id = 0
        at = found + 10
        while at <= end:
            self.detections.append(search_detection(at, beam_id))
            beam_id = (beam_id + 1) % GEOMETRY.search_cell_count
            at += 10
        self.detections.sort(key=lambda detection: detection.time_ms)

    def log(self) -> DetectionLog:
        return DetectionLog(Path("adp_detection_20261122_141833_020.csv"), self.detections)


class RotationTest(unittest.TestCase):
    def test_fit_rotation_recovers_attitude(self):
        q = attitude(35.0, pitch=4.0, roll=-3.0)
        pairs = []
        for beam_id in range(0, GEOMETRY.search_cell_count, 7):
            a = direction_vector(*GEOMETRY.search_cell(beam_id))
            pairs.append((a, rotate(q, a)))
        fitted = fit_rotation(pairs)
        for a, b in pairs:
            self.assertLess(angle_between_deg(direction_of(rotate(fitted, a)), direction_of(b)), 1e-4)

    def test_search_cell_numbering_matches_processor_grid(self):
        # 처리부 SearchGrid: 고각 행마다 방위를 처음부터 끝까지(번호 = 행 × 방위 수 + 열)
        self.assertEqual(GEOMETRY.search_cell(0), (-42.0, 3.0))
        self.assertEqual(GEOMETRY.search_cell(22), (-42.0 + 4.2, 3.0 + 4.4))
        self.assertIsNone(GEOMETRY.search_cell(GEOMETRY.search_cell_count))
        geometry = read_beam_geometry({"BEAM_SEARCH_AZIMUTH_COUNT": "11", "BEAM_SEARCH_WIDTH_DEG": "8"})
        self.assertEqual((geometry.search_azimuth_count, geometry.search_width_deg), (11, 8.0))


class GridFitTest(unittest.TestCase):
    def test_spread_detections_fit_full_rotation(self):
        q = attitude(300.0, pitch=6.0, roll=2.0)
        fit = fit_search_grid(GEOMETRY, Scenario(q).detections)
        self.assertEqual(fit.method, "rotation")
        self.assertAlmostEqual((fit.facing_deg - 300.0 + 180) % 360 - 180, 0.0, delta=1.0)
        # 맞춘 격자로 바꾼 칸 방향이 실제 칸 방향과 빔 폭(6°)보다 훨씬 가깝다.
        worst = max(angle_between_deg(fit.to_enu(*GEOMETRY.search_cell(beam_id)), enu_of_cell(beam_id, q))
                    for beam_id in range(GEOMETRY.search_cell_count))
        self.assertLess(worst, 1.5)

    def test_narrow_detections_fall_back_to_offsets(self):
        q = attitude(13.0)
        detections = [search_detection(index * 10, 100, [plot_at(*enu_of_cell(100, q))]) for index in range(5)]
        fit = fit_search_grid(GEOMETRY, detections)
        self.assertEqual(fit.method, "offset")
        self.assertAlmostEqual(fit.facing_deg, 13.0, delta=0.5)

    def test_no_search_plots(self):
        fit = fit_search_grid(GEOMETRY, [search_detection(0, 0)])
        self.assertEqual(fit.method, "none")
        self.assertEqual(fit.facing_deg, 0.0)
        self.assertIn("가정", fit.describe())


class BuildTest(unittest.TestCase):
    def replay(self, recording: LogRecording) -> ReplayPlayer:
        player = ReplayPlayer(recording)
        player.seek(player.duration_s)
        return player

    def test_beams_detections_and_tracks(self):
        q = attitude(40.0, pitch=3.0)
        scenario = Scenario(q, misses=(3,), end_track_at=5, tracking_beams=8)
        recording = build_log_recording(scenario.log(), None, GEOMETRY, PARAMETERS)
        self.assertEqual(recording.meta["source"], SOURCE_LOG)
        beams = [event for _, event in recording.built_events if isinstance(event, Beam)]
        self.assertEqual(len(beams), len(scenario.detections))
        self.assertEqual([beam.command_count for beam in beams[:3]], [1, 2, 3])
        # 탐색 빔 방향 = 맞춘 격자 칸. 실제 칸 방향과 빔 폭보다 훨씬 가깝다.
        for beam in beams[:GEOMETRY.search_cell_count]:
            self.assertLess(angle_between_deg((beam.azimuth_deg, beam.elevation_deg),
                                              enu_of_cell(beam.beam_id, q)), 1.5)
        # 확인 빔 = 확인 지연 앞 탐색 탐지 방향
        confirmation = next(beam for beam in beams if beam.beam_type == BeamType.CONFIRMATION)
        search_plot = next(d for d in scenario.detections if d.beam_type == BeamType.SEARCH and d.request_id == 40
                           and d.time_ms == confirmation.timestamp_ms - 100).plots[0]
        self.assertAlmostEqual(confirmation.azimuth_deg, search_plot.azimuth_deg)

        state = self.replay(recording).state
        cycles = state.tracking.cycles(1)
        outcomes = [cycle.outcome for cycle in cycles]
        # 확인 갱신, 추적 1·2 갱신, 3 미탐, 4 갱신, 5(로그 끝 전 마지막) 종료
        self.assertEqual(outcomes, [Outcome.UPDATED, Outcome.UPDATED, Outcome.UPDATED, Outcome.MISSED,
                                    Outcome.UPDATED, Outcome.ENDED])
        self.assertNotIn(1, state.tracking.latest_tracks)
        # 갱신 위치는 탐지(측정) 위치이고 속력은 두 갱신 위치의 차이
        updated = [cycle for cycle in cycles if cycle.estimate is not None]
        self.assertAlmostEqual(updated[-1].estimate.speed_mps, 300.0, delta=1.0)
        self.assertEqual(state.source, SOURCE_LOG)
        self.assertIsNotNone(state.attitude)

    def test_track_alive_at_log_end_stays(self):
        scenario = Scenario(attitude(10.0), tracking_beams=6)
        # 로그 끝을 마지막 추적 빔 바로 뒤로 자른다.
        last = scenario.tracking_times[-1]
        scenario.detections = [d for d in scenario.detections if d.time_ms <= last + 20]
        state = self.replay(build_log_recording(scenario.log(), None, GEOMETRY, PARAMETERS)).state
        self.assertIn(1, state.tracking.latest_tracks)
        self.assertEqual(state.tracking.cycles(1)[-1].outcome, Outcome.UPDATED)

    def test_missed_tracking_beam_points_at_prediction(self):
        scenario = Scenario(attitude(0.0), misses=(4,), tracking_beams=6)
        recording = build_log_recording(scenario.log(), None, GEOMETRY, PARAMETERS)
        missed = [event for _, event in recording.built_events if isinstance(event, Beam)
                  and event.beam_type == BeamType.TRACKING][3]
        # 등속 외삽한 예측 방향(실제 표적 방향과 같아야 한다: 표적이 등속이므로)
        found = scenario.tracking_times[0] - PARAMETERS.tracking_interval_ms - PARAMETERS.confirmation_delay_ms
        start = position_m(60.0, *enu_of_cell(40, attitude(0.0)))
        seconds = (missed.timestamp_ms - found) / 1000.0
        expected = direction_of(tuple(p + v * seconds for p, v in zip(start, scenario.velocity)))
        self.assertLess(angle_between_deg((missed.azimuth_deg, missed.elevation_deg), expected), 0.01)

    def test_message_log_operation_and_unanswered(self):
        scenario = Scenario(attitude(0.0), tracking_beams=8)
        with tempfile.TemporaryDirectory() as directory:
            # 처리부 시각 0 = 워커 시작 줄. 추적 빔 3번 뒤 응답 없는 트랙 삭제, 그 뒤 운용 OFF → ON.
            removed = scenario.tracking_times[2] + 500
            off = scenario.tracking_times[3] + 500
            on = off + 50

            def stamp(ms: float) -> str:
                seconds = 10 + ms / 1000.0
                return f"2026-11-22T05:18:{int(seconds):02d}.{int(round(seconds % 1 * 1000)):03d}Z"

            text = "\n".join([
                f"{stamp(0)} [main] [info] [thread=1] [10/12] All 7 worker threads started",
                f"{stamp(1)} [operation] [info] [thread=2] Beam applied operation state: ON",
                f"{stamp(removed)} [target] [info] [thread=3] Removed unanswered track: id=1, currentTime=1ms",
                f"{stamp(off)} [operation] [info] [thread=2] Beam applied operation state: OFF",
                f"{stamp(off)} [operation] [info] [thread=3] Target applied operation state: OFF, cleared tracks=0",
                f"{stamp(on)} [operation] [info] [thread=2] Beam applied operation state: ON",
            ]) + "\n"
            path = Path(directory) / "adp_20261122_141833_020.log"
            path.write_text(text, encoding="utf-8")
            message_log = read_message_log(path)
        recording = build_log_recording(scenario.log(), message_log, GEOMETRY, PARAMETERS)
        controls = [event.status for _, event in recording.built_events if isinstance(event, OperatorControl)]
        self.assertEqual(controls, [1, 0, 1])
        beams = [event for _, event in recording.built_events if isinstance(event, Beam)]
        after_on = next(beam for beam in beams if beam.timestamp_ms >= on)
        self.assertEqual(after_on.command_count, 1)
        state = self.replay(recording).state
        # 응답 없는 트랙 삭제 뒤에는 트랙 목록에서 빠진다(그 뒤 갱신은 새로 만들어진 트랙).
        self.assertGreaterEqual(state.operation_restarts, 1)


class SessionTest(unittest.TestCase):
    def test_logs_only_folder_replays(self):
        scenario = Scenario(attitude(25.0, pitch=2.0), misses=(2,), tracking_beams=6)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_csv(folder / "adp_detection_20261122_141833_020.csv", scenario.detections)
            (folder / "adp_20261122_141833_020.log").write_text(
                "2026-11-22T05:18:33.020Z [main] [info] [thread=1] [10/12] All 7 worker threads started\n",
                encoding="utf-8")
            session = load_session(folder, PARAMETERS, GEOMETRY)
            self.assertTrue(session.is_log_replay)
            self.assertIsNotNone(session.player)
            self.assertIn("탐지 CSV", session.report.source)
            self.assertTrue(any("처리부 로그로 재생" in line for line in session.describe()))
            # 실행을 다시 고르면 재생을 새로 만든다.
            first = session.player
            session.select_run(0)
            self.assertIsNot(session.player, first)
            self.assertTrue(session.is_log_replay)
            # CSV 읽기와 같은 결과
            self.assertEqual(len(read_detection_log(folder / "adp_detection_20261122_141833_020.csv").detections),
                             len(scenario.detections))
            # 문제(미탐)를 녹화처럼 찾는다.
            self.assertTrue(any(problem.track_id == 1 for problem in session.problems()))

    def test_recording_wins_over_logs(self):
        scenario = Scenario(attitude(0.0), tracking_beams=3)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_csv(folder / "adp_detection_20261122_141833_020.csv", scenario.detections)
            recorder = Recorder(folder / RECORDING_NAME)
            recorder.write_connection(True)
            recorder.write_frame(encode(Beam(BeamType.SEARCH, 10, 0, 1, 0.0, 3.0, 6.0, 6.0)))
            recorder.close()
            session = load_session(folder, PARAMETERS, GEOMETRY)
            self.assertFalse(session.is_log_replay)
            self.assertIsNotNone(session.player)
            self.assertIn("모니터 녹화", session.report.source)
            session.select_run(0)  # 실행을 골라도 녹화 재생은 그대로
            self.assertFalse(session.is_log_replay)


class FullHistoryTest(unittest.TestCase):
    def test_replay_keeps_every_cycle(self):
        scenario = Scenario(attitude(0.0), tracking_beams=320)
        player = ReplayPlayer(build_log_recording(scenario.log(), None, GEOMETRY, PARAMETERS))
        player.seek(player.duration_s)
        self.assertTrue(player.state.full_tracking_history)
        # 실시간 보관 한도(300)를 넘어도 처음부터 남는다(확인 1 + 추적 320).
        self.assertEqual(len(player.state.tracking.cycles(1)), 321)
        # 되감으면 그 시점까지로 줄어든다.
        player.seek(player.duration_s / 2)
        self.assertLess(len(player.state.tracking.cycles(1)), 321)


if __name__ == "__main__":
    unittest.main()
