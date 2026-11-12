import unittest

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.frames import FrameCounter
from aesa_monitor.core.protocol import Beam, BeamType
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.stats import BeamRecord, compute_stats

SEARCH, CONFIRMATION, TRACKING = int(BeamType.SEARCH), int(BeamType.CONFIRMATION), int(BeamType.TRACKING)


def feed(counter, beams, start_ms=0):
    """(종류, 번호) 목록을 10 ms 간격으로 넣는다."""
    for offset, (beam_type, beam_id) in enumerate(beams):
        counter.add(beam_type, beam_id, start_ms + offset * 10)


class FrameCounterTest(unittest.TestCase):
    def test_counts_beams_per_search_frame(self):
        counter = FrameCounter()
        # 격자 4칸. 두 번째 프레임에 추적 빔 2개가 끼어든다.
        feed(counter, [(SEARCH, 0), (SEARCH, 1), (SEARCH, 2), (SEARCH, 3),
                       (SEARCH, 0), (TRACKING, 7), (SEARCH, 1), (SEARCH, 2), (TRACKING, 7), (SEARCH, 3),
                       (SEARCH, 0)])

        frames = counter.complete_frames()

        self.assertEqual([frame.index for frame in frames], [1, 2])
        self.assertEqual((frames[0].start_ms, frames[0].end_ms), (0, 40))
        self.assertAlmostEqual(frames[0].share(BeamType.SEARCH), 1.0)
        self.assertEqual(frames[1].total, 6)
        self.assertAlmostEqual(frames[1].share(BeamType.TRACKING), 2 / 6)
        self.assertEqual(frames[1].duration_ms, 60)

    def test_first_frame_started_mid_grid_is_not_complete(self):
        counter = FrameCounter()
        feed(counter, [(SEARCH, 2), (SEARCH, 3), (SEARCH, 0), (SEARCH, 1), (SEARCH, 2), (SEARCH, 3),
                       (SEARCH, 0)])

        self.assertEqual([frame.complete for frame in counter.frames], [False, True])
        self.assertEqual([frame.index for frame in counter.complete_frames()], [2])

    def test_frame_cut_by_grid_restart_is_not_complete(self):
        counter = FrameCounter()
        # 한 바퀴를 다 돈 뒤, 다음 프레임 중간(2번)에서 격자가 다시 0번부터 시작한다(자세 재설정 등).
        feed(counter, [(SEARCH, 0), (SEARCH, 1), (SEARCH, 2), (SEARCH, 3),
                       (SEARCH, 0), (SEARCH, 1), (SEARCH, 0)])

        self.assertEqual([frame.complete for frame in counter.frames], [True, False])

    def test_break_frame_closes_current_as_incomplete(self):
        counter = FrameCounter()
        feed(counter, [(SEARCH, 0), (SEARCH, 1), (CONFIRMATION, 5)])
        self.assertEqual(counter.current().total, 3)

        counter.break_frame()

        self.assertIsNone(counter.current())
        self.assertEqual([frame.complete for frame in counter.frames], [False])


class StateFramesTest(unittest.TestCase):
    def test_state_counts_frames_and_clears_on_restart(self):
        state = MonitorState()
        for index in range(9):
            state.apply(Beam(SEARCH, 10000 + index * 10, index % 4, index, 0.0, 0.0, 6.0, 6.0))
        self.assertEqual(len(state.frames.complete_frames()), 2)

        state.apply(Beam(SEARCH, 0, 0, 0, 0.0, 0.0, 6.0, 6.0))  # 처리부 재시작

        self.assertEqual(len(state.frames.frames), 0)


class StatsFramesTest(unittest.TestCase):
    def test_report_has_frame_shares(self):
        records = []
        for index in range(12):
            beam_type = TRACKING if index == 6 else SEARCH
            beam_id = 9 if beam_type == TRACKING else len([r for r in records if r.beam_type == SEARCH]) % 4
            records.append(BeamRecord(index * 10, beam_type, beam_id, 0.0, 0.0, []))

        report = compute_stats(records, TrackingParameters(), "test")

        # 탐색 0~3, 탐색 0·1·추적·탐색 2·3 두 프레임이 완료되고 마지막 0~2는 잘린다.
        self.assertEqual(len(report.frames), 2)
        self.assertEqual(report.frames[1].counts[TRACKING], 1)
        item = next(entry for entry in report.items if entry.name == "탐색 프레임별 점유율")
        self.assertEqual(item.chart, "frames")
        self.assertTrue(item.value.startswith("2개"))


if __name__ == "__main__":
    unittest.main()
