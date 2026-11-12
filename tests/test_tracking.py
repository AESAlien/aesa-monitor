import math
import unittest

from aesa_monitor.core.geometry import BeamFrame, position_m
from aesa_monitor.core.protocol import Beam, BeamType, Detection, MessageId, Plot, Track, TrackList
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.tracking import Outcome, TrackingHistory
from aesa_monitor.core.traffic import MessageTraffic


def request_beam(timestamp, track_id=5, azimuth=30.0, elevation=5.0, beam_type=BeamType.TRACKING):
    return Beam(int(beam_type), timestamp, track_id, 0, azimuth, elevation, 6.0, 6.0)


def plot(range_km=50.0, azimuth=30.0, elevation=5.0):
    return Plot(range_km, azimuth, elevation, -100.0, 80.0)


def track(timestamp, track_id=5, range_km=50.0, azimuth=30.0, elevation=5.0):
    return Track(track_id, 2, range_km, range_km, azimuth, elevation, -100.0,
                 37.0, 127.0, 1.0, 250.0, 90.0, 0.0, timestamp)


class BeamFrameTest(unittest.TestCase):
    def test_offset_of_point_on_axis_is_zero_across_beam(self):
        frame = BeamFrame(30.0, 5.0)
        cross, up, along = frame.offset(position_m(50.0, 30.0, 5.0))

        self.assertAlmostEqual(cross, 0.0, places=6)
        self.assertAlmostEqual(up, 0.0, places=6)
        self.assertAlmostEqual(along, 50000.0, places=3)

    def test_offset_signs_follow_azimuth_and_elevation(self):
        frame = BeamFrame(30.0, 5.0)
        cross, _, _ = frame.offset(position_m(50.0, 30.1, 5.0))
        _, up, _ = frame.offset(position_m(50.0, 30.0, 5.1))

        # 0.1도는 50 km에서 약 87 m다. 방위 쪽은 cos(고각)만큼 짧다.
        self.assertAlmostEqual(cross, 50000 * math.radians(0.1) * math.cos(math.radians(5.0)), delta=0.5)
        self.assertAlmostEqual(up, 50000 * math.radians(0.1), delta=0.5)

    def test_normalized_offset_matches_simulator_in_beam_rule_across_north(self):
        frame = BeamFrame(359.0, 0.0)
        u, v = frame.normalized_offset(2.0, 3.0, 6.0, 6.0)

        self.assertAlmostEqual(u, 1.0)
        self.assertAlmostEqual(v, 1.0)


class TrackingHistoryTest(unittest.TestCase):
    def test_joins_beam_and_detection_and_selects_plot_nearest_to_prediction(self):
        history = TrackingHistory()
        history.apply_beam(request_beam(1000))

        cycle = history.apply_detection(Detection(
            int(BeamType.TRACKING), 1000, 5, [plot(azimuth=31.0), plot(azimuth=30.02)]))

        self.assertEqual(cycle.predicted, (30.0, 5.0))
        self.assertEqual(cycle.selected_index, 1)
        self.assertLess(cycle.selected.angle_error_m, 20.0)
        self.assertAlmostEqual(cycle.plots[0].normalized[0], 2 * 1.0 / 6.0, places=5)

    def test_detection_without_beam_keeps_plots_without_offsets(self):
        history = TrackingHistory()

        cycle = history.apply_detection(Detection(int(BeamType.TRACKING), 1000, 5, [plot()]))

        self.assertIsNone(cycle.predicted)
        self.assertEqual(cycle.selected_index, 0)
        self.assertTrue(math.isnan(cycle.selected.angle_error_m))

    def test_search_detections_are_counted_but_not_cycles(self):
        history = TrackingHistory()

        result = history.apply_detection(Detection(int(BeamType.SEARCH), 10, 3, [plot(), plot()]))

        self.assertIsNone(result)
        self.assertEqual((history.search_detections, history.search_plots), (1, 2))
        self.assertEqual(history.track_ids(), [])

    def test_track_list_resolves_update_miss_and_end(self):
        history = TrackingHistory()
        history.apply_tracks([track(0)])
        for time_ms in (1000, 2000, 3000):
            history.apply_beam(request_beam(time_ms))
            history.apply_detection(Detection(int(BeamType.TRACKING), time_ms, 5, [plot()]))
            if time_ms == 1000:
                history.apply_tracks([track(1000)])
            elif time_ms == 2000:
                history.apply_tracks([track(1000)])
            else:
                history.apply_tracks([])

        outcomes = [cycle.outcome for cycle in history.cycles(5)]
        self.assertEqual(outcomes, [Outcome.UPDATED, Outcome.MISSED, Outcome.ENDED])
        first = history.cycles(5)[0]
        self.assertEqual(first.previous_estimate.timestamp_ms, 0)
        self.assertEqual(first.elapsed_ms, 1000)
        self.assertEqual(first.estimate.timestamp_ms, 1000)
        self.assertEqual(first.next_beam.timestamp_ms, 2000)

    def test_late_track_list_upgrades_provisional_miss(self):
        history = TrackingHistory()
        history.apply_beam(request_beam(1000))
        history.apply_detection(Detection(int(BeamType.TRACKING), 1000, 5, [plot()]))

        # 앞선 탐지를 처리한 트랙 목록이 먼저 와도 다음 목록에서 갱신으로 바뀐다.
        history.apply_tracks([track(0)])
        history.apply_tracks([track(1000)])

        self.assertEqual(history.cycles(5)[0].outcome, Outcome.UPDATED)

    def test_summary_counts_consecutive_misses_and_mean_difference(self):
        history = TrackingHistory()
        for time_ms in (1000, 2000, 3000):
            history.apply_beam(request_beam(time_ms))
            history.apply_detection(Detection(int(BeamType.TRACKING), time_ms, 5, [plot()]))
        history.apply_tracks([track(1000)])

        summary = history.summary(5)

        self.assertEqual((summary.cycles, summary.updated, summary.missed), (3, 1, 2))
        self.assertEqual(summary.consecutive_misses, 2)
        self.assertAlmostEqual(summary.mean_angle_error_m, 0.0, places=6)


class TrafficTest(unittest.TestCase):
    def test_counts_and_rate_within_one_second(self):
        traffic = MessageTraffic()
        for index in range(5):
            traffic.record(request_beam(index), received_at=10.0 + index * 0.1)
        traffic.record(TrackList([]), received_at=10.0)

        self.assertEqual(traffic.counts[MessageId.BEAM_TRANSMIT], 5)
        self.assertEqual(traffic.rate(MessageId.BEAM_TRANSMIT, now=10.45), 5.0)
        self.assertEqual(traffic.rate(MessageId.BEAM_TRANSMIT, now=11.25), 2.0)
        self.assertAlmostEqual(traffic.age(MessageId.TRACK_INFORMATION, now=12.0), 2.0)
        self.assertIsNone(traffic.age(MessageId.PLOT_INFORMATION))

    def test_state_routes_detection_and_tracks_to_history(self):
        state = MonitorState()
        state.apply(request_beam(1000), received_at=1.0)
        state.apply(Detection(int(BeamType.TRACKING), 1000, 5, [plot()]), received_at=1.0)
        state.apply(TrackList([track(1000)]), received_at=1.0)

        self.assertEqual(state.tracking.cycles(5)[0].outcome, Outcome.UPDATED)
        self.assertEqual(state.traffic.counts[MessageId.PLOT_INFORMATION], 1)


if __name__ == "__main__":
    unittest.main()
