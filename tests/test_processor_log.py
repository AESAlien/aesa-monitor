import tempfile
import unittest
from pathlib import Path

from aesa_monitor.io.processor_log import (
    CATEGORY_ATTITUDE, CATEGORY_LIFECYCLE, CATEGORY_OPERATION, CATEGORY_OTHER, CATEGORY_PROBLEM, CATEGORY_REJECTED,
    DETECTION_HEADER, group_runs, read_detection_log, read_message_log,
)

# 처리부 MessageLogManager 패턴: "%Y-%m-%dT%H:%M:%S.%eZ [%n] [%l] [thread=%t] %v" (UTC)
MESSAGE_LOG = """\
2026-11-10T06:11:59.900Z [main] [info] [thread=100] Application starting
2026-11-10T06:11:59.950Z [main] [info] [thread=100] Communication server opened
2026-11-10T06:11:59.951Z [BeamWorker] [info] [thread=101] Worker started
2026-11-10T06:12:00.000Z [main] [info] [thread=100] Six worker threads started; beam tick = 10 ms
2026-11-10T06:12:01.250Z [BeamEvent] [warning] [thread=101] Attitude ignored (operation state is OFF): roll=0, pitch=0, yaw=13.02429
2026-11-10T06:12:02.000Z [BeamEvent] [info] [thread=101] Operation state changed: ON
2026-11-10T06:12:02.500Z [BeamEvent] [info] [thread=101] Attitude applied: roll=0, pitch=0, yaw=13.02429
2026-11-10T06:13:12.010Z [BeamEvent] [warning] [thread=101] Beam request rejected: status=OUT_OF_COVERAGE, requestId=6
2026-11-10T06:13:20.000Z [CommReceive] [error] [thread=103] Communication data could not be received
2026-11-10T06:13:30.000Z [main] [info] [thread=100] Shutdown requested
2026-11-10T06:13:30.0
"""

DETECTION_CSV = DETECTION_HEADER + """
SEARCH,5,1000,0,,,,,,
SEARCH,6,1010,2,0,60000.5,20.25,1.5,-250,80
SEARCH,6,1010,2,1,61000,21,1.5,-240.5,70
CONFIRMATION,1,1110,1,0,59990,20.25,1.5,-250,80
TRACKING,1,2110,0,,,,,,
TRACKING,1,3110,1,0,59500,20.3,1.5,-250,8
"""


class MessageLogTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "adp_20261110_151159_900.log"
        self.path.write_text(MESSAGE_LOG, encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def test_classifies_events_and_skips_partial_line(self):
        log = read_message_log(self.path)
        self.assertEqual(log.skipped_lines, 1)
        categories = [event.category for event in log.events]
        self.assertEqual(categories, [
            CATEGORY_LIFECYCLE, CATEGORY_LIFECYCLE, CATEGORY_OTHER, CATEGORY_LIFECYCLE, CATEGORY_ATTITUDE,
            CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM, CATEGORY_LIFECYCLE,
        ])

    def test_processor_time_starts_at_worker_start_line(self):
        log = read_message_log(self.path)
        by_text = {event.text: event for event in log.events}
        self.assertAlmostEqual(by_text["Six worker threads started; beam tick = 10 ms"].processor_ms, 0.0)
        rejected = [event for event in log.events if event.category == CATEGORY_REJECTED][0]
        self.assertAlmostEqual(rejected.processor_ms, 72010.0)
        self.assertEqual(rejected.details, {"status": "OUT_OF_COVERAGE", "requestId": "6"})
        self.assertEqual(rejected.summary, "요청 빔 거부 #6 (OUT_OF_COVERAGE)")

    def test_attitude_summaries(self):
        log = read_message_log(self.path)
        attitudes = [event.summary for event in log.events if event.category == CATEGORY_ATTITUDE]
        self.assertEqual(attitudes, ["자세 무시: 운용 OFF 중 수신 (yaw 13.02429)", "자세 반영 (yaw 13.02429)"])
        counts = log.counts()
        self.assertEqual(counts[CATEGORY_PROBLEM], 1)


class DetectionLogTest(unittest.TestCase):
    def test_groups_rows_by_beam(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adp_detection_20261110_151159_901.csv"
            path.write_text(DETECTION_CSV + "TRACKING,1,4110,1,0,59", encoding="utf-8")
            log = read_detection_log(path)
        self.assertEqual(log.skipped_lines, 1)
        self.assertEqual([(item.beam_type, item.request_id, item.time_ms, len(item.plots))
                          for item in log.detections],
                         [(1, 5, 1000, 0), (1, 6, 1010, 2), (2, 1, 1110, 1), (3, 1, 2110, 0), (3, 1, 3110, 1)])
        self.assertAlmostEqual(log.detections[1].plots[1].doppler_mps, -240.5)

    def test_rejects_other_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adp_detection_20261110_151159_901.csv"
            path.write_text("a,b,c\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_detection_log(path)


class GroupRunsTest(unittest.TestCase):
    def test_pairs_message_log_and_csv_of_same_run(self):
        names = ["adp_20261110_151159_900.log", "adp_detection_20261110_151159_901.csv",
                 "adp_20261110_160000_000.log", "adp_detection_20261110_170000_000.csv", "other.log"]
        runs = group_runs(Path("/x") / name for name in names)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0].message_log.name, "adp_20261110_151159_900.log")
        self.assertEqual(runs[0].detection_log.name, "adp_detection_20261110_151159_901.csv")
        self.assertIsNone(runs[1].detection_log)
        self.assertIsNone(runs[2].message_log)
        self.assertIn("메시지 로그", runs[0].label)


if __name__ == "__main__":
    unittest.main()
