import unittest

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.events import ConnectionChanged
from aesa_monitor.core.protocol import Beam, BeamType, Detection, Plot, Track, TrackList
from aesa_monitor.core.stats import BeamRecord, compute_stats, records_from_events
from aesa_monitor.core.verdict import BAD, OK, WARN

PARAMETERS = TrackingParameters()


def item(report, name):
    return next(entry for entry in report.items if entry.name == name)


def track(track_id, time_ms):
    return Track(track_id, 2, 50.0, 49.9, 20.0, 1.5, -250.0, 0, 0, 1.3, 250.0, 270.0, 0.0, time_ms)


class RecordsFromEventsTest(unittest.TestCase):
    def test_joins_detections_and_resolves_track_outcomes(self):
        plot = Plot(50.0, 20.0, 1.5, -250.0, 80.0)
        events = [
            ConnectionChanged(True),
            Beam(BeamType.SEARCH, 0, 0, 1, 20.0, 1.5, 6, 6), Detection(BeamType.SEARCH, 0, 0, [plot]),
            Beam(BeamType.CONFIRMATION, 100, 1, 2, 20.0, 1.5, 3, 3), Detection(BeamType.CONFIRMATION, 100, 1, [plot]),
            TrackList([track(1, 100)]),
            Beam(BeamType.TRACKING, 1100, 1, 3, 20.0, 1.5, 3, 3), Detection(BeamType.TRACKING, 1100, 1, []),
            TrackList([track(1, 100)]),
            Beam(BeamType.TRACKING, 2100, 1, 4, 20.0, 1.5, 3, 3), Detection(BeamType.TRACKING, 2100, 1, [plot]),
            TrackList([track(1, 2100)]),
            ConnectionChanged(False, "x"),
            ConnectionChanged(True),
            Beam(BeamType.SEARCH, 50, 0, 5, 20.0, 1.5, 6, 6),
            ConnectionChanged(False, "end"),
        ]
        records, extra = records_from_events((index * 0.01, event) for index, event in enumerate(events))
        self.assertEqual(extra, {"disconnects": 1, "restarts": 1})
        self.assertEqual([record.segment for record in records], [0, 0, 0, 0, 1])
        self.assertEqual(records[0].plots, [(50.0, 20.0, 1.5, -250.0)])
        self.assertEqual([record.outcome for record in records[2:4]], ["missed", "updated"])
        self.assertAlmostEqual(records[3].angle_error_m, 0.0, places=3)
        self.assertIsNone(records[4].plots)


class ComputeStatsTest(unittest.TestCase):
    def build(self, tracking_times, confirmation_delay=100, gap_after=None):
        records = []
        time_ms = 0
        while time_ms < 4000:
            if gap_after is not None and time_ms == gap_after:
                time_ms += 30
            beam_type = BeamType.SEARCH
            beam_id = (time_ms // 10) % 4
            plots = []
            if time_ms == 500:
                plots = [(50.0, 20.0, 1.5, -250.0)]
            if time_ms == 500 + confirmation_delay:
                beam_type, beam_id, plots = BeamType.CONFIRMATION, 1, [(50.0, 20.0, 1.5, -250.0)]
            if time_ms in tracking_times:
                beam_type, beam_id, plots = BeamType.TRACKING, 1, tracking_times[time_ms]
            azimuth = 20.0 if beam_type != BeamType.SEARCH else 10.0 * beam_id
            records.append(BeamRecord(time_ms, beam_type, beam_id, azimuth, 1.5, plots))
            time_ms += 10
        return records

    def test_normal_schedule_is_ok(self):
        hit = [(50.0, 20.0, 1.5, -250.0)]
        report = compute_stats(self.build({1600: hit, 2600: hit, 3600: hit}), PARAMETERS, "test")
        self.assertEqual(item(report, "연속 빔 간격").level, OK)
        self.assertEqual(item(report, "탐색→확인 지연").samples, [100.0])
        self.assertEqual(item(report, "추적 주기").samples, [1000.0, 1000.0])
        self.assertEqual(item(report, "추적 주기").level, OK)
        self.assertEqual(item(report, "탐색 한 바퀴").level, OK)
        self.assertEqual(report.tracks[0].track_id, 1)
        self.assertTrue(report.tracks[0].confirmed)
        self.assertTrue(report.tracks[0].has_confirmation)

    def test_period_deviation_and_misses_are_graded(self):
        hit = [(50.0, 20.0, 1.5, -250.0)]
        records = self.build({1600: hit, 2750: [], 3750: hit})
        for record in records:
            record.azimuth_deg = None  # 탐지 CSV처럼 방향이 없다.
            record.elevation_deg = None
        report = compute_stats(records, PARAMETERS, "csv")
        self.assertEqual(item(report, "추적 주기").level, BAD)
        self.assertEqual(item(report, "추적 빔 미탐").value, "1/3 (33.3%)")
        self.assertEqual(item(report, "탐색→확인 지연").samples, [100.0])
        self.assertEqual(report.tracks[0].max_consecutive_misses, 1)

    def test_gap_counts_missing_slots(self):
        report = compute_stats(self.build({}, gap_after=2000), PARAMETERS, "test")
        gaps = item(report, "연속 빔 간격")
        self.assertIn("빠진 칸 3개", gaps.note)
        self.assertEqual(gaps.level, WARN)

    def test_empty_records(self):
        report = compute_stats([], PARAMETERS, "none")
        self.assertEqual(report.records, 0)


if __name__ == "__main__":
    unittest.main()
