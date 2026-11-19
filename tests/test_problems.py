import unittest

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.events import PEER_SIMULATOR, ConnectionChanged, PeerLinkChanged
from aesa_monitor.core.problems import BAD, MASS_LOSS_WINDOW_MS, WARN, ProblemDetector, scan_events
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.protocol import (
    Beam, BeamType, Detection, MonitorStatus, OperatorControl, Plot, Track, TrackList,
)

PARAMETERS = TrackingParameters()
CELLS = 4


def search(time_ms, beam_id):
    return Beam(BeamType.SEARCH, time_ms, beam_id, 0, 10.0 * beam_id, 3.0, 6.0, 6.0)


def track(track_id, time_ms):
    return Track(track_id, 2, 50.0, 49.9, 20.0, 1.5, -250.0, 0, 0, 1.3, 250.0, 270.0, 0.0, time_ms)


class Recording:
    """녹화 이벤트 목록 (녹화 시각 초, 이벤트)를 만든다. 처리부 시각 1 ms = 녹화 1 ms로 둔다."""

    def __init__(self):
        self.events = [(0.0, ConnectionChanged(True))]
        self.offset_s = 0.0

    def beams(self, start_ms, end_ms, skip=()):
        for index, time_ms in enumerate(range(start_ms, end_ms, 10)):
            if time_ms in skip:
                continue
            at = self.offset_s + time_ms / 1000
            self.events.append((at, search(time_ms, index % CELLS)))
            self.events.append((at, Detection(BeamType.SEARCH, time_ms, index % CELLS, [])))
            if time_ms % 200 == 0:
                self.events.append((at, MonitorStatus(time_ms, 1.0, False, True, True)))

    def add(self, time_ms, event):
        self.events.append((self.offset_s + time_ms / 1000, event))

    def scan(self):
        self.events.sort(key=lambda item: item[0])
        return scan_events(self.events, PARAMETERS)

    def scan_at(self, update_times):
        """검사(update)를 정한 녹화 시각에만 한다. 실시간에서 검사가 밀린 경우를 흉내 낸다."""
        self.events.sort(key=lambda item: item[0])
        position = [0.0]
        state = MonitorState(clock=lambda: position[0])
        detector = ProblemDetector(PARAMETERS)
        pending = sorted(update_times)
        for time_s, event in self.events:
            while pending and time_s >= pending[0]:
                position[0] = pending.pop(0)
                detector.update(state)
            position[0] = time_s
            state.apply(event, time_s)
        for time_s in pending:
            position[0] = time_s
            detector.update(state)
        return detector.problems


def of_kind(problems, kind):
    return [problem for problem in problems if problem.kind == kind]


class GapTest(unittest.TestCase):
    def test_close_gaps_merge_into_one_incident_with_total_slots(self):
        recording = Recording()
        recording.beams(0, 10000, skip=(3000, 3010, 3500, 3510, 3520))
        gaps = of_kind(recording.scan(), "gap")
        self.assertEqual(len(gaps), 1)
        self.assertEqual((gaps[0].amount, gaps[0].count, gaps[0].level), (5, 2, WARN))
        self.assertEqual((gaps[0].start_ms, gaps[0].end_ms), (3000, 3500))
        self.assertEqual((gaps[0].duration_ms, gaps[0].is_range), (500, True))
        self.assertAlmostEqual(gaps[0].start_s, 3.0, delta=0.3)

    def test_far_gaps_are_separate_and_many_slots_are_bad(self):
        recording = Recording()
        skip = tuple(range(2000, 2070, 10)) + (8000,)
        recording.beams(0, 12000, skip=skip)
        gaps = of_kind(recording.scan(), "gap")
        self.assertEqual([(gap.amount, gap.level) for gap in gaps], [(7, BAD), (1, WARN)])

    def test_normal_schedule_has_no_problems(self):
        recording = Recording()
        recording.beams(0, 8000)
        self.assertEqual(recording.scan(), [])


class RestartAndLinkTest(unittest.TestCase):
    def test_processor_restart_is_recorded_once(self):
        recording = Recording()
        recording.beams(0, 5000)
        recording.offset_s = 6.0          # 처리부가 다시 시작해 처리부 시각이 0부터
        recording.beams(0, 5000)
        restarts = of_kind(recording.scan(), "restart")
        self.assertEqual(len(restarts), 1)
        self.assertAlmostEqual(restarts[0].start_s, 6.0, delta=0.3)

    def test_restart_after_reconnect_is_recorded(self):
        recording = Recording()
        recording.beams(0, 5000)
        recording.events.append((5.5, ConnectionChanged(False, "closed by processor")))
        recording.events.append((7.0, ConnectionChanged(True)))
        recording.offset_s = 7.0
        recording.beams(0, 4000)
        problems = recording.scan()
        self.assertEqual(len(of_kind(problems, "restart")), 1)
        self.assertEqual(len(of_kind(problems, "disconnect")), 1)

    def test_short_disconnect_inside_one_scan_step_is_recorded(self):
        # 감지 간격(0.25 s) 안에 끊겼다 다시 연결돼도 사건으로 받아 남긴다.
        recording = Recording()
        recording.beams(0, 4000)
        recording.events.append((4.10, ConnectionChanged(False, "closed by processor")))
        recording.events.append((4.15, ConnectionChanged(True)))
        recording.offset_s = 4.15
        recording.beams(0, 3000)
        disconnects = of_kind(recording.scan(), "disconnect")
        self.assertEqual(len(disconnects), 1)
        self.assertAlmostEqual(disconnects[0].start_s, 4.10)
        self.assertAlmostEqual(disconnects[0].end_s, 4.15)
        self.assertIn("0.1 s 뒤 다시 연결", disconnects[0].detail)

    def test_short_peer_link_drop_from_capture_is_recorded(self):
        recording = Recording()
        recording.beams(0, 4000)
        recording.events.append((2.01, PeerLinkChanged(PEER_SIMULATOR, False, "FIN")))
        recording.events.append((2.05, PeerLinkChanged(PEER_SIMULATOR, True)))
        drops = of_kind(recording.scan(), f"peer-{PEER_SIMULATOR}")
        self.assertEqual(len(drops), 1)
        self.assertEqual((drops[0].level, drops[0].open), (BAD, False))
        self.assertAlmostEqual(drops[0].end_s, 2.05)

    def test_mid_recording_disconnect_is_recorded_but_final_one_is_not(self):
        recording = Recording()
        recording.beams(0, 4000)
        recording.events.append((4.5, ConnectionChanged(False, "closed by processor")))
        recording.events.append((7.0, ConnectionChanged(True)))
        recording.offset_s = 7.0
        recording.beams(0, 3000)
        recording.events.append((10.5, ConnectionChanged(False, "monitor closed")))
        disconnects = of_kind(recording.scan(), "disconnect")
        self.assertEqual(len(disconnects), 1)
        self.assertEqual(disconnects[0].level, BAD)
        self.assertAlmostEqual(disconnects[0].start_s, 4.5, delta=0.3)
        self.assertIn("다시 연결", disconnects[0].detail)


class MissTest(unittest.TestCase):
    def test_misses_of_one_track_merge_and_reach_bad_at_limit_minus_one(self):
        recording = Recording()
        recording.beams(0, 12000, skip=tuple(range(5, 12000, 1000)))
        interval = PARAMETERS.tracking_interval_ms
        missed = {3000 + interval * index for index in range(PARAMETERS.max_consecutive_tracking_misses - 1)}
        last_update = 0
        for time_ms in range(1005, 9000, interval):
            beam_ms = time_ms
            recording.add(beam_ms, Beam(BeamType.TRACKING, beam_ms, 7, 0, 20.0, 1.5, 3.0, 3.0))
            hit = beam_ms - 5 not in missed
            plots = [Plot(50.0, 20.0, 1.5, -250.0, 80.0)] if hit else []
            recording.add(beam_ms + 1, Detection(BeamType.TRACKING, beam_ms, 7, plots))
            if hit:
                last_update = beam_ms
            recording.add(beam_ms + 2, TrackList([track(7, last_update)]))
        misses = of_kind(recording.scan(), "miss")
        self.assertEqual(len(misses), 1)
        self.assertEqual(misses[0].count, len(missed))
        self.assertEqual(misses[0].level, BAD)
        self.assertEqual(misses[0].track_id, 7)
        self.assertEqual(misses[0].start_ms, 3005)


class OffBeamTest(unittest.TestCase):
    def test_detection_outside_beam_is_not_an_automatic_problem(self):
        # 연관 탐지(모니터 추정)에 기대는 '빔 밖 탐지'는 자동 기록하지 않는다.
        recording = Recording()
        recording.beams(0, 6000, skip=tuple(range(5, 6000, 1000)))
        for beam_ms in range(1005, 5000, PARAMETERS.tracking_interval_ms):
            recording.add(beam_ms, Beam(BeamType.TRACKING, beam_ms, 7, 0, 20.0, 1.5, 3.0, 3.0))
            recording.add(beam_ms + 1, Detection(BeamType.TRACKING, beam_ms, 7, [Plot(50.0, 24.0, 1.5, -250.0, 80.0)]))
            recording.add(beam_ms + 2, TrackList([track(7, beam_ms)]))
        problems = recording.scan()
        self.assertEqual([problem for problem in problems if problem.kind == "off-beam"], [])
        self.assertEqual([problem for problem in problems if problem.track_id == 7], [])


def tracked(recording, track_ids, until_ms, missed=(), unanswered_at=None, ends=None, keep_listed=False):
    """트랙들을 추적 주기마다 갱신한다. missed: {(트랙, 빔 시각)} 미탐, unanswered_at: 결과 없이 나간 마지막 빔 시각.
    ends: {트랙: 시각} 그 시각부터 빔이 나가지 않고 처리부 트랙 목록에서 빠지는 트랙(없으면 until_ms까지 유지).
    keep_listed: ends가 지난 트랙도 until_ms까지 트랙 목록에 남긴다(한 번에 여러 트랙이 사라지는 경우).

    트랙마다 빔 슬롯을 1 ms씩 비켜 둔다. 반환: 마지막 갱신 시각 {트랙: 시각}.
    """
    interval = PARAMETERS.tracking_interval_ms
    last = {track_id: 0 for track_id in track_ids}
    ends = ends or {}
    for base in range(1005, until_ms, interval):
        alive = [track_id for track_id in track_ids if base < ends.get(track_id, until_ms)]
        for offset, track_id in enumerate(track_ids):
            if track_id not in alive:
                continue
            beam_ms = base + offset
            recording.add(beam_ms, Beam(BeamType.TRACKING, beam_ms, track_id, 0, 20.0, 1.5, 3.0, 3.0))
            hit = (track_id, base) not in missed
            plots = [Plot(50.0, 20.0, 1.5, -250.0, 80.0)] if hit else []
            recording.add(beam_ms + 1, Detection(BeamType.TRACKING, beam_ms, track_id, plots))
            if hit:
                last[track_id] = beam_ms
        listed = track_ids if keep_listed else alive
        recording.add(base + 50, TrackList([track(track_id, last[track_id]) for track_id in listed]))
    if unanswered_at is not None:
        for offset, track_id in enumerate(track_ids):
            recording.add(unanswered_at + offset, Beam(BeamType.TRACKING, unanswered_at + offset, track_id, 0, 20.0,
                                                       1.5, 3.0, 3.0))
    return last


def tracking_slots(until_ms, tracks):
    return tuple(base + offset for base in range(1005, until_ms + PARAMETERS.tracking_interval_ms,
                                                  PARAMETERS.tracking_interval_ms) for offset in range(tracks))


class TrackEndTest(unittest.TestCase):
    """트랙 종료 분류: 정상 종료(운용 OFF, 영역 이탈)는 문제로 남기지 않는다. 2026-11-19 시연 녹화에서 확인한 모양."""

    def test_track_lost_after_next_beam_without_result_is_recorded(self):
        # 다음 추적 빔은 나갔는데 결과가 없고(모의기 응답 없음) 처리부가 트랙을 지움
        recording = Recording()
        recording.beams(0, 12000, skip=tracking_slots(12000, 1))
        tracked(recording, [7], 5000, unanswered_at=5005)
        recording.add(9000, TrackList([]))
        ended = of_kind(recording.scan(), "ended")
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].track_id, 7)
        self.assertIn("#7", ended[0].title)
        self.assertIn("결과 없이", ended[0].detail)

    def test_track_leaving_coverage_is_not_recorded(self):
        # 영역 경계에서 한 번 빗나간 뒤 다음 요청이 영역 밖으로 거부되어(빔 없음) 4초 뒤 지워짐
        recording = Recording()
        recording.beams(0, 12000, skip=tracking_slots(12000, 1))
        tracked(recording, [7], 6000, missed={(7, 5005)})
        recording.add(10000, TrackList([]))
        problems = recording.scan()
        self.assertEqual([problem for problem in problems if problem.track_id == 7], [])

    def test_earlier_miss_is_kept_whatever_the_update_timing(self):
        # 생애 앞쪽의 미탐(2초)은 영역 이탈(5초 미탐 뒤 다음 빔 없음)과 상관없이 남고, 끝 미탐(5초)은 빠진다.
        #   [2, 12]: 검사가 밀려 트랙 종료와 앞쪽 사이클 확정을 한 번에 봄(실시간 큐가 쌓인 경우)
        #   [3, 6, 9, 12]: 끝 미탐이 앞 미탐 사건에 먼저 붙은 뒤 종료를 봄
        for updates in ([2.0, 12.0], [3.0, 6.0, 9.0, 12.0], [index / 2 for index in range(1, 25)]):
            with self.subTest(updates=updates):
                recording = Recording()
                recording.beams(0, 12000, skip=tracking_slots(12000, 1))
                tracked(recording, [7], 6000, missed={(7, 2005), (7, 5005)})
                recording.add(10000, TrackList([]))
                problems = recording.scan_at(updates)
                misses = [problem for problem in of_kind(problems, "miss") if problem.track_id == 7]
                self.assertEqual([(problem.start_ms, problem.end_ms, problem.count) for problem in misses],
                                 [(2005, 2005, 1)])
                self.assertEqual(of_kind(problems, "ended"), [])

    def test_tracks_cleared_by_operation_off_are_not_recorded(self):
        recording = Recording()
        recording.beams(0, 5500, skip=tracking_slots(5500, 2))
        tracked(recording, [7, 8], 5000)
        recording.add(5500, OperatorControl(0))
        recording.add(5500, MonitorStatus(5500, 1.0, True, True, True))
        recording.add(5600, TrackList([]))
        self.assertEqual(of_kind(recording.scan(), "ended"), [])

    def test_single_track_lost_by_miss_limit_is_recorded(self):
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        recording = Recording()
        recording.beams(0, 14000, skip=tracking_slots(14000, 1))
        misses = {(7, 3005 + interval * index) for index in range(limit)}
        tracked(recording, [7], 3005 + interval * limit, missed=misses)
        recording.add(3005 + interval * limit + 100, TrackList([]))
        problems = recording.scan()
        self.assertEqual(len(of_kind(problems, "ended")), 1)
        self.assertEqual(len(of_kind(problems, "miss")), 1)
        self.assertEqual(of_kind(problems, "mass-loss"), [])

    def test_tracks_lost_together_become_one_incident(self):
        # 시나리오 한 주기가 끝나 모의기가 표적을 없애면 모든 트랙이 함께 미탐 한도로 지워진다.
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        ids = [7, 8, 9]
        recording = Recording()
        recording.beams(0, 14000, skip=tracking_slots(14000, len(ids)))
        misses = {(track_id, 3005 + interval * index) for track_id in ids for index in range(limit)}
        tracked(recording, ids, 3005 + interval * limit, missed=misses)
        recording.add(3005 + interval * limit + 100, TrackList([]))
        problems = recording.scan()
        groups = of_kind(problems, "mass-loss")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].amount, 3)
        self.assertIn("3개 동시 소실", groups[0].title)
        self.assertEqual([problem for problem in problems if problem.track_id in ids], [])

    def test_earlier_miss_of_track_in_mass_loss_is_kept(self):
        # 동시 소실에 든 트랙도 생애 앞쪽의 미탐(2초)은 남는다. 끝 미탐 구간만 동시 소실 사건으로 옮긴다.
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        ids = [7, 8, 9]
        end = 4005 + interval * limit
        for updates in (None, [3.0, 5.5, 8.0, 12.0], [2.0, 12.0]):
            with self.subTest(updates=updates):
                recording = Recording()
                recording.beams(0, 14000, skip=tracking_slots(14000, len(ids)))
                misses = {(track_id, end - interval * (index + 1)) for track_id in ids for index in range(limit)}
                tracked(recording, ids, end, missed=misses | {(7, 2005)})
                recording.add(end + 100, TrackList([]))
                problems = recording.scan() if updates is None else recording.scan_at(updates)
                self.assertEqual([group.amount for group in of_kind(problems, "mass-loss")], [3])
                kept = [(problem.kind, problem.track_id, problem.start_ms, problem.end_ms)
                        for problem in problems if problem.track_id in ids]
                self.assertEqual(kept, [("miss", 7, 2005, 2005)])

    def test_loss_batches_further_apart_than_window_are_separate_incidents(self):
        # 두 묶음(3개씩)이 4초 간격으로 사라짐: 3초 창 하나에 여섯 개가 다 들어가지 않으므로 '3개 동시 소실' 두 건이다.
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        first, second = [7, 8, 9], [10, 11, 12]
        first_end = 3005 + interval * limit
        second_end = first_end + 4000
        recording = Recording()
        recording.beams(0, 16000, skip=tracking_slots(16000, 6))
        misses = {(track_id, first_end - interval * (index + 1)) for track_id in first for index in range(limit)}
        misses |= {(track_id, second_end - interval * (index + 1)) for track_id in second for index in range(limit)}
        ends = {**{track_id: first_end for track_id in first}, **{track_id: second_end for track_id in second}}
        tracked(recording, first + second, second_end, missed=misses, ends=ends)
        recording.add(second_end + 100, TrackList([]))
        groups = of_kind(recording.scan(), "mass-loss")
        self.assertEqual([group.amount for group in groups], [3, 3])
        self.assertTrue(all("3개 동시 소실" in group.title for group in groups))
        self.assertGreater(groups[1].start_ms - groups[0].start_ms, 3000)

    def test_loss_shortly_after_a_batch_joins_it(self):
        # 묶음(3개) 뒤 창 안에서 하나가 더 사라지면 같은 사건(4개)이다.
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        first, late = [7, 8, 9], [10]
        first_end = 3005 + interval * limit
        late_end = first_end + 2000
        recording = Recording()
        recording.beams(0, 14000, skip=tracking_slots(14000, 4))
        misses = {(track_id, first_end - interval * (index + 1)) for track_id in first for index in range(limit)}
        misses |= {(10, late_end - interval * (index + 1)) for index in range(limit)}
        ends = {**{track_id: first_end for track_id in first}, 10: late_end}
        tracked(recording, first + late, late_end, missed=misses, ends=ends)
        recording.add(late_end + 100, TrackList([]))
        problems = recording.scan()
        groups = of_kind(problems, "mass-loss")
        self.assertEqual([group.amount for group in groups], [4])
        self.assertEqual([problem for problem in problems if problem.track_id in first + late], [])

    def test_loss_batches_across_reconnect_are_separate_incidents(self):
        # 모니터가 처리부와 다시 연결됨(처리부는 재시작하지 않아 시각이 이어짐): 앞 묶음 사건을 닫고 새로 센다.
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        first, second = [7, 8, 9], [10, 11, 12]
        first_end = 3005 + interval * limit
        second_end = first_end + 4000
        recording = Recording()
        recording.beams(0, 16000, skip=tracking_slots(16000, 6))
        misses = {(track_id, first_end - interval * (index + 1)) for track_id in first for index in range(limit)}
        misses |= {(track_id, second_end - interval * (index + 1)) for track_id in second for index in range(limit)}
        ends = {**{track_id: first_end for track_id in first}, **{track_id: second_end for track_id in second}}
        tracked(recording, first + second, second_end, missed=misses, ends=ends)
        # 앞 묶음이 사건으로 잡힌 뒤(그 사건의 유예 시간 안), 뒤 묶음의 미탐이 시작되기 전에 다시 연결된다.
        recording.add(first_end + 600, ConnectionChanged(False))
        recording.add(first_end + 700, ConnectionChanged(True))
        recording.add(second_end + 100, TrackList([]))
        problems = recording.scan()
        groups = of_kind(problems, "mass-loss")
        self.assertEqual([group.amount for group in groups], [3, 3])
        self.assertFalse(groups[0].open)

    def test_losses_seen_together_are_grouped_by_processor_time(self):
        # 빠른 재생: 트랙 1(10초에 소실)과 트랙 7·8(5초에 소실)이 한 번에 목록에서 빠짐. 번호 순으로 보면 10초 → 5초 순이라
        # 5초 넘게 떨어진 셋이 '3개 동시 소실'로 묶였다. 처리부 시각 순으로 보면 셋은 3초 창 하나에 들지 않는다.
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        early, late = [7, 8], [1]
        early_end = 3005 + interval * limit
        late_end = early_end + 5000
        recording = Recording()
        recording.beams(0, 16000, skip=tracking_slots(16000, 3))
        misses = {(track_id, early_end - interval * (index + 1)) for track_id in early for index in range(limit)}
        misses |= {(track_id, late_end - interval * (index + 1)) for track_id in late for index in range(limit)}
        tracked(recording, late + early, late_end, missed=misses, ends={7: early_end, 8: early_end},
                keep_listed=True)
        recording.add(late_end + 100, TrackList([]))
        problems = recording.scan()
        self.assertEqual(of_kind(problems, "mass-loss"), [])
        self.assertEqual(sorted(problem.track_id for problem in of_kind(problems, "ended")), [1, 7, 8])

    def test_out_of_order_losses_group_only_within_window(self):
        # 소실이 처리부 시각 순서대로 오지 않아도 결과가 같다: 3초 창 안의 셋만 묶이고, 창 밖 하나는 따로다.
        for order in ([1, 7, 8, 9], [7, 1, 8, 9], [9, 8, 7, 1]):
            with self.subTest(order=order):
                detector = ProblemDetector(PARAMETERS)
                at = {1: 10000, 7: 5000, 8: 5050, 9: 5100}
                grouped = {track_id: detector._group_loss(track_id, [], at[track_id], 0.0, 4000)
                           for track_id in order}
                groups = of_kind(detector.problems, "mass-loss")
                self.assertEqual([group.amount for group in groups], [3])
                self.assertEqual(groups[0].start_ms, 5000)
                self.assertLessEqual(groups[0].end_ms - groups[0].start_ms, MASS_LOSS_WINDOW_MS)
                self.assertFalse(grouped[1])

    def test_late_loss_does_not_rewind_incident_freshness(self):
        # 사건(5.0~5.1초)에 그보다 앞선 소실(4.9초)이 늦게 붙어도, 사건의 유예 시간은 가장 늦은 소실(5.1초)부터 센다.
        detector = ProblemDetector(PARAMETERS)
        grace = 4000
        grouped = [detector._group_loss(track_id, [], at_ms, 0.0, grace)
                   for track_id, at_ms in ((7, 5000), (8, 5050), (9, 5100), (10, 4900))]
        self.assertEqual(grouped, [False, False, True, True])
        limit_ms = grace + MASS_LOSS_WINDOW_MS
        detector._close_stale(0.0, 5100 + limit_ms - 50)   # 4.9초 기준으로는 지났지만 5.1초 기준으로는 아직
        self.assertIn(("mass-loss", None), detector._open)
        self.assertTrue(detector._group_loss(11, [], 5150, 0.0, grace))
        groups = of_kind(detector.problems, "mass-loss")
        self.assertEqual([group.amount for group in groups], [5])
        self.assertEqual((groups[0].start_ms, groups[0].end_ms), (4900, 5150))

    def test_random_loss_orders_keep_incidents_consistent(self):
        # 소실 시각·순서를 무작위로 섞어도: 사건 하나의 길이는 창 이하, 한 트랙은 사건 하나에만, 개수는 트랙 수와 같다.
        import random
        import re
        generator = random.Random(20261120)
        for _ in range(300):
            detector = ProblemDetector(PARAMETERS)
            losses = []
            track_id = 1
            for _cluster in range(generator.randint(1, 4)):
                center = generator.randint(0, 20000)
                for _ in range(generator.randint(1, 5)):
                    losses.append((track_id, center + generator.randint(-2500, 2500)))
                    track_id += 1
            generator.shuffle(losses)
            for lost_id, at_ms in losses:
                detector._group_loss(lost_id, [], max(at_ms, 0), 0.0, 4000)
            seen = set()
            for group in of_kind(detector.problems, "mass-loss"):
                members = {int(item) for item in re.findall(r"#(\d+)", group.detail)}
                self.assertEqual(group.amount, len(members), group.detail)
                self.assertGreaterEqual(group.amount, 3)
                self.assertLessEqual(group.end_ms - group.start_ms, MASS_LOSS_WINDOW_MS, losses)
                self.assertFalse(members & seen, losses)
                seen |= members

    def test_reused_track_id_after_processor_restart_is_watched_again(self):
        # 재시작 전에 트랙 7이 영역 이탈(정상)로 분류되어도, 재시작 뒤 같은 번호의 새 트랙 미탐은 기록한다.
        limit = PARAMETERS.max_consecutive_tracking_misses
        interval = PARAMETERS.tracking_interval_ms
        recording = Recording()
        recording.beams(0, 12000, skip=tracking_slots(12000, 1))
        tracked(recording, [7], 6000, missed={(7, 5005)})
        recording.add(10000, TrackList([]))
        recording.offset_s = 13.0  # 처리부 재시작: 처리부 시각이 0부터 다시
        recording.beams(0, 14000, skip=tracking_slots(14000, 1))
        misses = {(7, 3005 + interval * index) for index in range(limit)}
        tracked(recording, [7], 3005 + interval * limit, missed=misses)
        recording.add(3005 + interval * limit + 100, TrackList([]))
        problems = recording.scan()
        self.assertEqual(len([problem for problem in of_kind(problems, "miss") if problem.track_id == 7]), 1)
        self.assertEqual(len([problem for problem in of_kind(problems, "ended") if problem.track_id == 7]), 1)

    def test_restart_does_not_count_as_track_end(self):
        recording = Recording()
        recording.beams(0, 5000)
        for time_ms in range(1000, 5000, 1000):
            recording.add(time_ms, TrackList([track(7, time_ms)]))
        recording.offset_s = 6.0
        recording.beams(0, 5000)
        self.assertEqual(of_kind(recording.scan(), "ended"), [])


if __name__ == "__main__":
    unittest.main()
