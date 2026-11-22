"""추적 탭 전시기: 바뀐 층만 다시 그리기(탐색 빔을 자주 그려도 바탕·트랙은 그대로 둔다).

화면(Tk 디스플레이)이 있어야 한다. 없으면(CI 등) 건너뛴다.
"""

import time
import unittest

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.protocol import Beam, BeamType, Detection, Plot, RadarAttitude, Track, TrackList
from aesa_monitor.core.state import MonitorState

try:
    import tkinter as tk

    from aesa_monitor.ui import track_tab
    from aesa_monitor.ui.style import Fonts, apply_theme
    _ROOT = tk.Tk()
    _ROOT.withdraw()
    apply_theme(_ROOT)
    _FONTS = Fonts(_ROOT)   # 글꼴 이름은 창마다 한 번만 만들 수 있다
except Exception:  # tkinter 없음 또는 디스플레이 없음
    _ROOT = None


def track(track_id: int, time_ms: int, range_km: float = 60.0) -> Track:
    return Track(track_id, 2, range_km, range_km, 10.0, 2.0, -200.0, 0.0, 0.0, 2.0, 250.0, 90.0, 0.0, time_ms)


@unittest.skipIf(_ROOT is None, "Tk 디스플레이 없음")
class TrackMapLayersTest(unittest.TestCase):
    def setUp(self):
        self.clock = [0.0]
        self.state = MonitorState(clock=lambda: self.clock[0])
        self.window = tk.Toplevel(_ROOT)
        self.window.geometry("1200x800")
        self.tab = track_tab.TrackTab(self.window, TrackingParameters())
        self.tab.frame.pack(fill=tk.BOTH, expand=True)
        self.window.update()
        self.state.apply(RadarAttitude(0.0, 0.0, 0.0, 0.0, 0.0, 10.0))
        self.time_ms = 1000
        self.search_id = 0
        # 트랙 하나: 확인 빔·탐지·트랙 목록
        self.state.apply(Beam(BeamType.CONFIRMATION, self.time_ms, 1, 1, 10.0, 2.0, 3.0, 3.0))
        self.state.apply(Detection(BeamType.CONFIRMATION, self.time_ms, 1, [Plot(60.0, 10.0, 2.0, -200.0, 80.0)]))
        self.state.apply(TrackList([track(1, self.time_ms)]))

    def tearDown(self):
        self.window.destroy()

    def search_beams(self, count: int) -> None:
        for _ in range(count):
            self.time_ms += 10
            self.state.apply(Beam(BeamType.SEARCH, self.time_ms, self.search_id, 2, -30.0 + self.search_id * 4.0,
                                  5.0, 6.0, 6.0))
            self.search_id = (self.search_id + 1) % 15

    def items(self, tag: str):
        return set(self.tab._map.find_withtag(tag))

    def test_only_beam_layer_redrawn_when_tracks_unchanged(self):
        self.search_beams(5)
        self.tab.refresh(self.state)
        self.window.update()
        background, tracks, beams = (self.items(track_tab.MAP_TAG_BACKGROUND), self.items(track_tab.MAP_TAG_TRACKS),
                                     self.items(track_tab.MAP_TAG_BEAMS))
        self.assertTrue(background and tracks and beams)
        self.search_beams(5)
        self.tab.refresh(self.state)   # 바로 다음 갱신(PANEL_REFRESH_SECONDS 안): 전시기만
        self.assertEqual(self.items(track_tab.MAP_TAG_BACKGROUND), background)
        self.assertEqual(self.items(track_tab.MAP_TAG_TRACKS), tracks)
        self.assertTrue(self.items(track_tab.MAP_TAG_BEAMS))
        self.assertFalse(self.items(track_tab.MAP_TAG_BEAMS) & beams)  # 빔은 새로 그림
        # 빔이 트랙 아래에 있다(트랙 층이 맨 위).
        order = self.tab._map.find_all()
        self.assertLess(max(order.index(item) for item in self.items(track_tab.MAP_TAG_BEAMS)),
                        min(order.index(item) for item in self.items(track_tab.MAP_TAG_TRACKS)))

    def test_track_update_redraws_track_layer_only(self):
        self.tab.refresh(self.state)
        background, tracks = self.items(track_tab.MAP_TAG_BACKGROUND), self.items(track_tab.MAP_TAG_TRACKS)
        self.time_ms += 1000
        self.state.apply(Beam(BeamType.TRACKING, self.time_ms, 1, 3, 10.0, 2.0, 3.0, 3.0))
        self.state.apply(Detection(BeamType.TRACKING, self.time_ms, 1, [Plot(60.2, 10.0, 2.0, -200.0, 80.0)]))
        self.state.apply(TrackList([track(1, self.time_ms, 60.2)]))
        self.tab.refresh(self.state)
        self.assertEqual(self.items(track_tab.MAP_TAG_BACKGROUND), background)
        self.assertFalse(self.items(track_tab.MAP_TAG_TRACKS) & tracks)

    def test_panels_refresh_at_slower_rate(self):
        self.tab.refresh(self.state)
        first = self.tab._panels_at
        self.tab.refresh(self.state)
        self.assertEqual(self.tab._panels_at, first)
        time.sleep(track_tab.PANEL_REFRESH_SECONDS)
        self.tab.refresh(self.state)
        self.assertGreater(self.tab._panels_at, first)

    def test_view_change_redraws_background(self):
        self.tab.refresh(self.state)
        background = self.items(track_tab.MAP_TAG_BACKGROUND)
        self.tab._map_mode.set("full")
        self.tab._redraw()
        self.assertFalse(self.items(track_tab.MAP_TAG_BACKGROUND) & background)
        self.assertTrue(self.items(track_tab.MAP_TAG_TRACKS))


if __name__ == "__main__":
    unittest.main()
