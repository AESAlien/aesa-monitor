import unittest

from aesa_monitor.core.view_clock import LAG_MS, ViewClock


class ViewClockTest(unittest.TestCase):
    def test_advances_smoothly_between_bursts(self):
        clock = ViewClock()
        # 캡처가 150 ms마다 묶음으로 넘긴다. 화면은 50 ms마다 그린다.
        latest = 10000
        views = []
        wall = 0.0
        for step in range(60):
            wall = step * 0.05
            if step % 3 == 0:
                latest = 10000 + step * 50 + 150  # 묶음이 오면 150 ms 앞까지 한꺼번에 받음
            views.append(clock.update(latest, wall))
        steps = [b - a for a, b in zip(views[20:], views[21:])]
        # 받은 시각은 0, 150, 0, 0, 150...으로 튀지만 표시 시각은 매번 비슷하게(약 50 ms) 나아간다.
        self.assertTrue(all(30 <= value <= 70 for value in steps), steps)

    def test_never_shows_time_not_received(self):
        clock = ViewClock()
        views = [clock.update(10000, step * 0.05) for step in range(100)]  # 데이터가 멈춤(운용 OFF)
        self.assertTrue(all(view <= 10000 for view in views))
        self.assertEqual(views[-1], 10000)  # 마지막으로 받은 시각까지만 가서 멈춘다

    def test_starts_lag_behind_latest(self):
        self.assertEqual(ViewClock().update(10000, 0.0), 10000 - LAG_MS)

    def test_jumps_when_time_moves_far(self):
        clock = ViewClock()
        clock.update(10000, 0.0)
        self.assertEqual(clock.update(40000, 0.05), 40000 - LAG_MS)  # OFF→ON 뒤 재개
        self.assertEqual(clock.update(500, 0.10), 500 - LAG_MS)      # 처리부 재시작

    def test_none_resets(self):
        clock = ViewClock()
        clock.update(10000, 0.0)
        self.assertIsNone(clock.update(None, 0.05))
        self.assertEqual(clock.update(20000, 0.10), 20000 - LAG_MS)


if __name__ == "__main__":
    unittest.main()
