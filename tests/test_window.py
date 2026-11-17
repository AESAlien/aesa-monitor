import unittest

from aesa_monitor.ui.window import MINIMUM_SIZE, PREFERRED_SIZE, initial_window_size


class InitialWindowSizeTest(unittest.TestCase):
    def test_large_screen_uses_preferred_size_centered(self):
        size = initial_window_size(2560, 1440)
        self.assertFalse(size.maximize)
        self.assertEqual((size.width, size.height), PREFERRED_SIZE)
        self.assertEqual(size.x, (2560 - PREFERRED_SIZE[0]) // 2)
        self.assertEqual((size.min_width, size.min_height), MINIMUM_SIZE)

    def test_full_hd_maximizes(self):
        # 1920×1080은 제목 줄·작업 표시줄을 빼면 높이 1000이 들어가지 않아 최대화로 시작한다.
        size = initial_window_size(1920, 1080)
        self.assertTrue(size.maximize)
        self.assertLessEqual(size.height, 1080 - 100)

    def test_small_laptop_fits_screen(self):
        size = initial_window_size(1366, 768)
        self.assertTrue(size.maximize)
        self.assertLessEqual(size.width, 1366)
        self.assertLessEqual(size.height, 768)
        self.assertLessEqual(size.min_height, 768 - 100)
        self.assertGreaterEqual(size.x, 0)
        self.assertGreaterEqual(size.y, 0)

    def test_geometry_text(self):
        self.assertEqual(initial_window_size(2560, 1440).geometry, "1680x1000+440+200")


if __name__ == "__main__":
    unittest.main()
