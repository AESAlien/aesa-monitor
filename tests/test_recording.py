import tempfile
import unittest
from pathlib import Path

from aesa_monitor.core.events import ConnectionChanged, PeerLinkChanged
from aesa_monitor.core.protocol import Beam, BeamType, Detection, MonitorStatus, Plot, encode
from aesa_monitor.io.recording import (
    RECORDING_NAME, Recorder, RecordingError, find_session_files, new_session_directory, read_recording,
)
from aesa_monitor.io.replay import ReplayPlayer


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def beam(time_ms, beam_id=0, beam_type=BeamType.SEARCH):
    return Beam(beam_type, time_ms, beam_id, 1, 10.0, 3.0, 6.0, 6.0)


def write_sample(path):
    clock = FakeClock()
    recorder = Recorder(path, {"server": "x"}, clock=clock)
    recorder.write_connection(True)
    for index in range(5):
        clock.now += 0.01
        recorder.write_frame(encode(beam(1000 + index * 10, index)))
        recorder.write_frame(encode(Detection(BeamType.SEARCH, 1000 + index * 10, index,
                                              [Plot(50.0, 10.0, 3.0, -100.0, 80.0)] if index == 2 else [])))
    clock.now += 0.01
    recorder.write_frame(encode(MonitorStatus(1050, 1.0, False, True, True)))
    clock.now += 0.5
    recorder.write_connection(False, "closed by processor")
    recorder.close()
    return recorder


class RecordingTest(unittest.TestCase):
    def test_round_trip_keeps_frames_times_and_connection_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / RECORDING_NAME
            recorder = write_sample(path)
            recording = read_recording(path)
            self.assertEqual(recording.meta["server"], "x")
            self.assertFalse(recording.truncated)
            self.assertEqual(len(recording.records), 13)
            self.assertEqual(recorder.frames, 11)
            events, errors = recording.events()
            self.assertEqual(errors, 0)
            self.assertEqual(events[0][1], ConnectionChanged(True))
            self.assertEqual(events[1][1], beam(1000, 0))
            self.assertAlmostEqual(events[1][0], 0.01)
            self.assertEqual(events[-1][1], ConnectionChanged(False, "closed by processor"))
            self.assertAlmostEqual(recording.duration_s, 0.56)

    def test_keeps_source_and_peer_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / RECORDING_NAME
            recorder = Recorder(path, {"source": "capture"})
            recorder.write_connection(True)
            recorder.write_peer_link("simulator", True)
            recorder.write_peer_link("console", False, "FIN")
            recorder.close()
            events = [event for _, event in read_recording(path).events()[0]]
        self.assertEqual(events, [ConnectionChanged(True, source="capture"), PeerLinkChanged("simulator", True, ""),
                                  PeerLinkChanged("console", False, "FIN")])

    def test_truncated_file_keeps_complete_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / RECORDING_NAME
            write_sample(path)
            data = path.read_bytes()
            path.write_bytes(data[:-10])
            recording = read_recording(path)
            self.assertTrue(recording.truncated)
            self.assertEqual(len(recording.records), 12)

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.aesarec"
            path.write_bytes(b"not a recording")
            with self.assertRaises(RecordingError):
                read_recording(path)

    def test_session_directory_names_do_not_collide(self):
        import datetime
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.datetime(2026, 11, 12, 10, 15, 0)
            first = new_session_directory(Path(directory), now)
            second = new_session_directory(Path(directory), now)
            self.assertEqual(first.name, "20261112_101500")
            self.assertEqual(second.name, "20261112_101500_1")

    def test_finds_recording_and_processor_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sample(root / RECORDING_NAME)
            (root / "processor_logs").mkdir()
            (root / "processor_logs" / "adp_20261112_101500_123.log").write_text("")
            (root / "processor_logs" / "adp_detection_20261112_101500_124.csv").write_text("")
            (root / "processor_logs" / "notes.txt").write_text("")
            recording, logs = find_session_files(root)
            self.assertEqual(recording, root / RECORDING_NAME)
            self.assertEqual([path.name for path in logs],
                             ["adp_20261112_101500_123.log", "adp_detection_20261112_101500_124.csv"])


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / RECORDING_NAME
        write_sample(path)
        self.player = ReplayPlayer(read_recording(path))

    def tearDown(self):
        self.directory.cleanup()

    def test_seek_applies_events_up_to_position(self):
        self.player.seek(0.025)
        self.assertTrue(self.player.state.connected)
        self.assertEqual([item.beam_id for item in self.player.state.beams], [0, 1])
        self.player.seek(0.1)
        self.assertEqual(len(self.player.state.beams), 5)

    def test_seeking_back_rebuilds_state(self):
        self.player.seek(0.1)
        self.player.seek(0.015)
        self.assertEqual([item.beam_id for item in self.player.state.beams], [0])

    def test_advance_uses_speed_and_stops_at_end(self):
        self.player.play()
        self.player.speed = 10.0
        self.player.advance(0.01)
        self.assertAlmostEqual(self.player.position_s, 0.1)
        self.player.advance(1.0)
        self.assertEqual(self.player.position_s, self.player.duration_s)
        self.assertFalse(self.player.playing)
        self.assertFalse(self.player.state.connected)

    def test_state_clock_follows_replay_position(self):
        self.player.seek(0.07)
        self.assertTrue(self.player.state.status_is_fresh())
        self.player.seek(self.player.duration_s)
        # 상태 메시지가 0.5초 넘게 오지 않았으므로 여전히 1초 이내다.
        self.assertTrue(self.player.state.status_is_fresh())

    def test_maps_processor_time_to_recording_time(self):
        self.assertEqual(self.player.processor_range_ms(), (1000, 1050))
        self.assertAlmostEqual(self.player.time_for_processor_ms(1015), 0.03)
        self.assertIsNone(self.player.time_for_processor_ms(5000))
        self.assertAlmostEqual(self.player.first_beam_s(), 0.01)


if __name__ == "__main__":
    unittest.main()
