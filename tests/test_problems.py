import unittest

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.events import PEER_SIMULATOR, ConnectionChanged, PeerLinkChanged
from aesa_monitor.core.problems import BAD, WARN, scan_events
from aesa_monitor.core.protocol import Beam, BeamType, Detection, MonitorStatus, Plot, Track, TrackList

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


class TrackEndTest(unittest.TestCase):
    def test_track_leaving_track_list_is_recorded(self):
        recording = Recording()
        recording.beams(0, 9000)
        for time_ms in range(1000, 5000, 1000):
            recording.add(time_ms, TrackList([track(7, time_ms)]))
        recording.add(5000, TrackList([]))
        ended = of_kind(recording.scan(), "ended")
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].track_id, 7)
        self.assertIn("#7", ended[0].title)

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
