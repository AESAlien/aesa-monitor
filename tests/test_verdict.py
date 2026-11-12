import unittest

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.events import ConnectionChanged
from aesa_monitor.core.protocol import Beam, BeamType, Detection, MonitorStatus, Plot, Track, TrackList
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.verdict import BAD, IDLE, OK, WARN, beam_verdict, comm_verdict, track_verdict

PARAMETERS = TrackingParameters()


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def search(time_ms, beam_id):
    return Beam(BeamType.SEARCH, time_ms, beam_id, 0, 10.0 * beam_id, 3.0, 6.0, 6.0)


def track(track_id, time_ms):
    return Track(track_id, 2, 50.0, 49.9, 20.0, 1.5, -250.0, 0, 0, 1.3, 250.0, 270.0, 0.0, time_ms)


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = MonitorState(clock=self.clock)
        self.state.apply(ConnectionChanged(True))

    def feed_search(self, start_ms, count, cells=4, skip=()):
        for index in range(count):
            time_ms = start_ms + index * 10
            if time_ms in skip:
                continue
            self.clock.now = time_ms / 1000
            self.state.apply(search(time_ms, index % cells))
            self.state.apply(Detection(BeamType.SEARCH, time_ms, index % cells, []))

    def status(self, time_ms, console=True, simulator=True, paused=False):
        self.clock.now = time_ms / 1000
        self.state.apply(MonitorStatus(time_ms, 1.0, paused, console, simulator))

    def test_disconnected_states(self):
        state = MonitorState(clock=self.clock)
        self.assertEqual(beam_verdict(state, PARAMETERS).level, IDLE)
        self.assertEqual(comm_verdict(state).level, BAD)

    def test_normal_search_schedule(self):
        self.feed_search(0, 200)
        verdict = beam_verdict(self.state, PARAMETERS)
        self.assertEqual(verdict.level, OK, verdict.text)
        self.assertIn("탐색 프레임 시간 0.04 s", verdict.text)

    def test_missing_beams_are_reported(self):
        self.feed_search(0, 200, skip=(1000, 1010, 1020, 1030, 1040, 1050, 1060))
        verdict = beam_verdict(self.state, PARAMETERS)
        self.assertEqual(verdict.level, BAD)
        self.assertIn("빠진 빔 7개", verdict.text)

    def test_no_beams_while_connected_is_warning(self):
        self.feed_search(0, 10)
        self.status(5000)
        self.assertEqual(beam_verdict(self.state, PARAMETERS).level, WARN)

    def test_confirmation_delay_is_matched_by_direction(self):
        self.state.apply(search(500, 2))
        self.state.apply(Detection(BeamType.SEARCH, 500, 2, [Plot(50.0, 20.2, 3.1, -100, 80)]))
        # 이웃 칸이 10 ms 뒤 같은 표적을 또 찾아도 처음 탐지 기준으로 잰다.
        self.state.apply(search(510, 3))
        self.state.apply(Detection(BeamType.SEARCH, 510, 3, [Plot(50.0, 20.2, 3.1, -100, 80)]))
        self.state.apply(Beam(BeamType.CONFIRMATION, 650, 7, 0, 20.2, 3.1, 3, 3))
        self.assertEqual(self.state.latest_confirmation_delay(), 150)

    def test_track_misses_raise_level(self):
        self.state.apply(Beam(BeamType.TRACKING, 1000, 1, 0, 20.0, 1.5, 3, 3))
        self.state.apply(Detection(BeamType.TRACKING, 1000, 1, [Plot(50.0, 20.0, 1.5, -250, 80)]))
        self.state.apply(TrackList([track(1, 1000)]))
        self.assertEqual(track_verdict(self.state, PARAMETERS).level, OK)
        for time_ms in (2000, 3000):
            self.state.apply(Beam(BeamType.TRACKING, time_ms, 1, 0, 20.0, 1.5, 3, 3))
            self.state.apply(Detection(BeamType.TRACKING, time_ms, 1, []))
            self.state.apply(TrackList([track(1, 1000)]))
        verdict = track_verdict(self.state, PARAMETERS)
        self.assertEqual(verdict.level, BAD)
        self.assertIn("#1 연속 미탐 2/3", verdict.text)

    def test_comm_reports_peer_disconnection_and_missing_answers(self):
        self.status(0, simulator=False)
        verdict = comm_verdict(self.state)
        self.assertEqual(verdict.level, BAD)
        self.assertIn("모의기 연결 안 됨", verdict.text)
        self.state.apply(MonitorStatus(0, 1.0, False, True, True))
        for index in range(100):
            time_ms = index * 10
            self.clock.now = time_ms / 1000
            self.state.apply(search(time_ms, index % 4))
            if index % 2 == 0:
                self.state.apply(Detection(BeamType.SEARCH, time_ms, index % 4, []))
        self.state.apply(MonitorStatus(990, 1.0, False, True, True))
        verdict = comm_verdict(self.state)
        self.assertEqual(verdict.level, WARN)
        self.assertIn("모의기 응답이 빔보다 적음", verdict.text)

    def test_event_log_records_peer_changes(self):
        self.status(0)
        self.status(100, simulator=False)
        texts = [entry.text for entry in self.state.event_log]
        self.assertEqual(texts, ["처리부에 연결됨", "운용통제기 연결됨", "모의기 연결됨", "모의기 연결 안 됨"])


if __name__ == "__main__":
    unittest.main()
