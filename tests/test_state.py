import unittest

from aesa_monitor.core.events import ConnectionChanged
from aesa_monitor.core.protocol import Beam, BeamType, MonitorStatus, OperatorControl, Track, TrackList
from aesa_monitor.core.state import MonitorState, ViewMode
from aesa_monitor.core.traffic import MESSAGE_TYPES


def beam(timestamp, beam_type=BeamType.SEARCH, beam_id=0, azimuth=0.0, elevation=0.0):
    return Beam(int(beam_type), timestamp, beam_id, 0, azimuth, elevation, 6.0, 6.0)


def track(track_id):
    return Track(track_id, 2, 10.0, 9.0, 1.0, 2.0, 3.0, 37.0, 127.0, 1.0, 200.0, 45.0, 0.0, 100)


def status(time_ms=0, scale=1.0, paused=False):
    return MonitorStatus(time_ms, scale, paused, True, True)


class OccupancyTest(unittest.TestCase):
    def test_counts_beam_types_within_window(self):
        state = MonitorState()
        for index in range(10):
            beam_type = BeamType.TRACKING if index == 9 else BeamType.SEARCH
            state.apply(beam(index * 10, beam_type, index))

        occupancy = state.occupancy(1000)

        self.assertAlmostEqual(occupancy[BeamType.SEARCH], 0.9)
        self.assertAlmostEqual(occupancy[BeamType.TRACKING], 0.1)
        self.assertAlmostEqual(occupancy[BeamType.CONFIRMATION], 0.0)

    def test_excludes_beams_older_than_window(self):
        state = MonitorState()
        state.apply(beam(0, BeamType.TRACKING))
        state.apply(beam(5000, BeamType.SEARCH))

        self.assertEqual([item.timestamp_ms for item in state.recent_beams(1000)], [5000])
        self.assertAlmostEqual(state.occupancy(1000)[BeamType.SEARCH], 1.0)

    def test_empty_state_has_zero_occupancy(self):
        self.assertEqual(set(MonitorState().occupancy(1000).values()), {0.0})


class SearchSweepTest(unittest.TestCase):
    def test_sweep_time_is_revisit_interval_of_search_cell(self):
        state = MonitorState()
        # 탐색 칸 3개를 10ms 간격으로 두 프레임 돈다.
        for index in range(6):
            state.apply(beam(index * 10, BeamType.SEARCH, index % 3))

        self.assertEqual(state.search_sweep_ms(), 30)
        self.assertEqual(len(state.search_cells), 3)

    def test_request_beams_do_not_affect_search_cells(self):
        state = MonitorState()
        state.apply(beam(0, BeamType.SEARCH, 0))
        state.apply(beam(10, BeamType.TRACKING, 0))

        self.assertEqual(state.search_cells[0].last_ms, 0)
        self.assertIsNone(state.search_sweep_ms())

    def test_unwraps_azimuth_around_reference(self):
        state = MonitorState()
        state.apply(beam(0, BeamType.SEARCH, 0, azimuth=350.0))

        self.assertAlmostEqual(state.unwrap_azimuth(10.0), 370.0)
        self.assertAlmostEqual(state.unwrap_azimuth(340.0), 340.0)


class ResetTest(unittest.TestCase):
    def test_clears_history_when_processor_time_goes_back(self):
        state = MonitorState()
        state.apply(beam(50000, BeamType.SEARCH, 0))
        state.apply(TrackList([track(1)]))

        state.apply(beam(10, BeamType.SEARCH, 5))

        self.assertEqual([item.timestamp_ms for item in state.beams], [10])
        self.assertEqual(list(state.search_cells), [5])
        self.assertEqual(state.tracks, [])

    def test_clears_history_on_reconnection(self):
        state = MonitorState()
        state.apply(ConnectionChanged(True))
        state.apply(beam(100))
        state.apply(ConnectionChanged(False, "closed"))
        self.assertEqual(len(state.beams), 1)

        state.apply(ConnectionChanged(True))

        self.assertTrue(state.connected)
        self.assertEqual(len(state.beams), 0)


class StatusTest(unittest.TestCase):
    def test_status_becomes_stale_without_updates(self):
        state = MonitorState()
        state.apply(status(), received_at=100.0)

        self.assertTrue(state.status_is_fresh(now=100.5))
        self.assertFalse(state.status_is_fresh(now=101.5))

    def test_now_uses_latest_of_beam_and_status_time(self):
        state = MonitorState()
        state.apply(beam(100))
        state.apply(status(time_ms=250), received_at=0.0)

        self.assertEqual(state.now_ms(), 250)

    def test_tracks_are_sorted_by_id(self):
        state = MonitorState()
        state.apply(TrackList([track(9), track(3)]))

        self.assertEqual([item.track_id for item in state.tracks], [3, 9])


class ViewModeTest(unittest.TestCase):
    def test_auto_mode_follows_speed(self):
        state = MonitorState()
        self.assertEqual(state.resolve_view_mode(ViewMode.AUTO), ViewMode.FLOW)

        state.apply(status(scale=0.1), received_at=0.0)
        self.assertEqual(state.resolve_view_mode(ViewMode.AUTO), ViewMode.DETAIL)

        state.apply(status(scale=1.0, paused=True), received_at=0.0)
        self.assertEqual(state.resolve_view_mode(ViewMode.AUTO), ViewMode.DETAIL)

        state.apply(status(scale=1.0), received_at=0.0)
        self.assertEqual(state.resolve_view_mode(ViewMode.AUTO), ViewMode.FLOW)

    def test_manual_mode_is_kept(self):
        state = MonitorState()
        state.apply(status(scale=0.1), received_at=0.0)

        self.assertEqual(state.resolve_view_mode(ViewMode.FLOW), ViewMode.FLOW)


if __name__ == "__main__":
    unittest.main()


def counted(timestamp, count, beam_id):
    return Beam(int(BeamType.SEARCH), timestamp, beam_id, count, float(beam_id), 0.0, 6.0, 6.0)


class OperationRestartTest(unittest.TestCase):
    """운용 OFF→ON: 처리부는 OFF 동안 빔을 보내지 않고, ON이면 카운트 1·탐색 0번부터 다시 보낸다."""

    def run_until_off(self, state):
        for index in range(10):
            state.apply(counted(1000 + index * 10, index + 1, index % 4))

    def test_off_time_is_not_counted_as_missed_beams(self):
        state = MonitorState()
        self.run_until_off(state)
        state.apply(OperatorControl(0))
        state.apply(OperatorControl(1))
        state.apply(counted(20000, 1, 0))
        state.apply(counted(20010, 2, 1))
        self.assertEqual(list(state.beam_gaps), [])
        self.assertEqual(state.missed_beam_slots(60000), 0)
        self.assertEqual(state.operation_restarts, 1)

    def test_count_reset_alone_starts_a_new_run(self):
        # 캡처를 늦게 켜서 OFF 명령을 못 봐도 카운트가 1로 돌아오면 새 운용 구간이다.
        state = MonitorState()
        self.run_until_off(state)
        state.apply(counted(20000, 1, 0))
        self.assertEqual(list(state.beam_gaps), [])

    def test_sweep_time_is_not_measured_across_off(self):
        state = MonitorState()
        self.run_until_off(state)
        self.assertEqual(state.search_sweep_ms(), 40)
        state.apply(OperatorControl(0))
        state.apply(counted(20000, 1, 0))
        self.assertIsNone(state.search_sweep_ms())
        for index in range(1, 5):
            state.apply(counted(20000 + index * 10, index + 1, index % 4))
        self.assertEqual(state.search_sweep_ms(), 40)

    def test_frame_in_progress_at_off_is_closed_incomplete(self):
        state = MonitorState()
        self.run_until_off(state)  # 0 1 2 3 | 0 1 2 3 | 0 1 → 완료 1개(앞 프레임은 0번부터라 완료)
        complete = len(state.frames.complete_frames())
        state.apply(OperatorControl(0))
        state.apply(counted(20000, 1, 0))
        self.assertEqual(len(state.frames.complete_frames()), complete)
        self.assertFalse(state.frames.frames[-1].complete)

    def test_late_beam_after_off_command_is_not_a_restart(self):
        # OFF 직전에 보낸 빔이 OFF 명령보다 늦게 캡처돼도(시간 공백 없음) 새 구간으로 보지 않는다.
        state = MonitorState()
        self.run_until_off(state)
        state.apply(OperatorControl(0))
        state.apply(counted(1100, 11, 2))
        self.assertEqual(state.operation_restarts, 0)
        state.apply(counted(20000, 1, 0))
        self.assertEqual(state.operation_restarts, 1)
        self.assertEqual(list(state.beam_gaps), [])

    def test_real_gap_inside_a_run_is_still_counted(self):
        state = MonitorState()
        self.run_until_off(state)
        state.apply(counted(1150, 11, 2))  # 1090 다음 1100~1140 네 칸이 빔
        self.assertEqual(state.missed_beam_slots(60000), 5)


class ViewLimitTest(unittest.TestCase):
    def test_beams_after_view_limit_are_not_shown_yet(self):
        state = MonitorState()
        for index in range(10):
            state.apply(beam(index * 10, BeamType.SEARCH, index))
        state.view_limit_ms = 50
        self.assertEqual(state.now_ms(), 50)
        self.assertEqual(state.latest_ms(), 90)
        self.assertEqual([item.timestamp_ms for item in state.recent_beams(20)], [30, 40, 50])
        state.view_limit_ms = None
        self.assertEqual(state.now_ms(), 90)


class ViewResetTest(unittest.TestCase):
    """시나리오를 바꿀 때의 화면 초기화(메뉴 '화면 초기화', '운용 ON마다 화면 초기화')."""

    def scenario(self, state, start_ms):
        for index in range(10):
            state.apply(counted(start_ms + index * 10, index + 1, index % 4))
        state.apply(TrackList((track(3), track(4))))

    def test_reset_clears_history_but_keeps_connection(self):
        state = MonitorState()
        state.apply(ConnectionChanged(True, "capture"))
        self.scenario(state, 1000)
        state.apply(OperatorControl(1))
        self.assertTrue(state.tracks)
        state.reset_view()
        self.assertEqual((state.tracks, list(state.beams), state.operation_restarts), ([], [], 0))
        self.assertTrue(state.connected)
        self.assertIsNotNone(state.operator_control)
        self.assertEqual(state.view_resets, 1)
        self.assertEqual([entry.text for entry in state.event_log], ["화면 초기화"])

    def test_next_beam_after_reset_is_not_counted_as_gap(self):
        state = MonitorState()
        self.scenario(state, 1000)
        state.reset_view()
        state.apply(counted(9000, 11, 0))
        self.assertEqual(list(state.beam_gaps), [])

    def test_operation_start_resets_only_when_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                state = MonitorState()
                state.reset_on_operation_start = enabled
                self.scenario(state, 1000)
                state.apply(OperatorControl(0))
                state.apply(OperatorControl(1))
                state.apply(counted(20000, 1, 0))  # 새 시나리오 첫 빔
                self.assertEqual(state.view_resets, 1 if enabled else 0)
                self.assertEqual(len(state.beams), 1 if enabled else 11)
                self.assertEqual(state.beams[-1].timestamp_ms, 20000)  # 첫 빔은 남는다
                self.assertEqual(state.operation_restarts, 1)
                # 통신 기록에도 새 시나리오의 첫 빔이 있다(초기화가 지운 뒤 다시 넣음)
                self.assertEqual(state.traffic.counts[MESSAGE_TYPES[Beam]], 1 if enabled else 11)
                self.assertIsNotNone(state.traffic.age(MESSAGE_TYPES[Beam]))


class ResetSettingTest(unittest.TestCase):
    def test_reset_on_operation_start_round_trips_through_monitor_env(self):
        import tempfile
        from dataclasses import replace
        from pathlib import Path

        from aesa_monitor.core.settings import load_settings, save_settings

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "monitor.env"
            self.assertFalse(load_settings(path).reset_on_operation_start)  # 기본은 끔
            save_settings(replace(load_settings(path), reset_on_operation_start=True), path)
            self.assertIn("MONITOR_RESET_ON_OPERATION_START=1", path.read_text(encoding="utf-8"))
            self.assertTrue(load_settings(path).reset_on_operation_start)
