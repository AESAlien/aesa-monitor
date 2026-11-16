import json
import tempfile
import unittest
from pathlib import Path

from aesa_monitor.io.bookmarks import (
    BOOKMARK_FILE, Bookmark, BookmarkError, bookmark_path, load_bookmarks, parse_time, save_bookmarks,
)


class BookmarkFileTest(unittest.TestCase):
    def test_round_trip_keeps_fields_and_sorts_by_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / BOOKMARK_FILE
            save_bookmarks(path, [
                Bookmark(130000, "탐색 프레임 시간 튐", tag="참고"),
                Bookmark(29150, "#3 영역 밖 거부 직전 미탐", tag="이슈", track_id=3, note="한 줄\n두 줄"),
                Bookmark(70000, "#2 차이 증가 구간", end_ms=95000, tag="확인 필요", track_id=2),
            ])

            loaded = load_bookmarks(path)

            self.assertEqual([bookmark.start_ms for bookmark in loaded], [29150, 70000, 130000])
            self.assertEqual(loaded[0].title, "#3 영역 밖 거부 직전 미탐")
            self.assertEqual((loaded[0].track_id, loaded[0].note), (3, "한 줄\n두 줄"))
            self.assertEqual((loaded[1].end_ms, loaded[1].tag, loaded[1].is_range), (95000, "확인 필요", True))
            self.assertIsNone(loaded[2].track_id)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_missing_file_is_empty_and_broken_file_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / BOOKMARK_FILE
            self.assertEqual(load_bookmarks(path), [])
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(BookmarkError):
                load_bookmarks(path)

    def test_reversed_range_is_swapped_and_zero_length_range_is_a_point(self):
        self.assertEqual((Bookmark(5000, "a", end_ms=2000).start_ms, Bookmark(5000, "a", end_ms=2000).end_ms),
                         (2000, 5000))
        self.assertFalse(Bookmark(5000, "a", end_ms=5000).is_range)

    def test_bookmark_path_for_folder_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.assertEqual(bookmark_path(folder), folder / "bookmarks.json")
            recording = folder / "monitor.aesarec"
            recording.write_bytes(b"")
            self.assertEqual(bookmark_path(recording), folder / "monitor.aesarec.bookmarks.json")


class SessionBookmarkTest(unittest.TestCase):
    def test_session_loads_and_saves_bookmarks_next_to_session(self):
        from aesa_monitor.core.config import TrackingParameters
        from aesa_monitor.io.session import load_session

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            save_bookmarks(folder / BOOKMARK_FILE, [Bookmark(1000, "처음")])
            session = load_session(folder, TrackingParameters())
            self.assertEqual([bookmark.title for bookmark in session.bookmarks], ["처음"])

            session.bookmarks.append(Bookmark(500, "앞"))
            session.save_bookmarks()
            self.assertEqual([bookmark.title for bookmark in load_bookmarks(folder / BOOKMARK_FILE)], ["앞", "처음"])

    def test_broken_bookmark_file_is_reported_not_fatal(self):
        from aesa_monitor.core.config import TrackingParameters
        from aesa_monitor.io.session import load_session

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / BOOKMARK_FILE).write_text("[", encoding="utf-8")
            session = load_session(folder, TrackingParameters())
            self.assertEqual(session.bookmarks, [])
            self.assertTrue(any("북마크" in error for error in session.errors))


class ParseTimeTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("01:10.250"), 70250)
        self.assertEqual(parse_time("1:02:03.5"), 3723500)
        self.assertEqual(parse_time("70.25"), 70250)
        self.assertEqual(parse_time(" 00:29.150 "), 29150)
        self.assertIsNone(parse_time(""))
        self.assertIsNone(parse_time("   "))

    def test_invalid(self):
        for text in ("abc", "1:xx", "-5", "1::2"):
            with self.assertRaises(BookmarkError):
                parse_time(text)


if __name__ == "__main__":
    unittest.main()
