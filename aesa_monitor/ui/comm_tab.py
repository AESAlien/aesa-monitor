"""통신 탭 — "연결이 살아 있고 메시지가 제때 오나?"

모니터는 처리부에만 연결된다. 운용통제기·모의기 연결 상태는 처리부가 0xAD01로 알려준 값이고,
메시지 속도는 처리부가 상대에게 보낸 것과 받은 것의 복사본을 모니터가 받은 속도다.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from aesa_monitor.core.events import PEER_CONSOLE, PEER_SIMULATOR, SOURCE_CAPTURE
from aesa_monitor.core.protocol import MessageId
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.traffic import CAPTURE_ONLY_MESSAGES, MESSAGE_NAMES, MESSAGE_ROUTES
from aesa_monitor.core.verdict import comm_verdict, expected_beam_rate
from aesa_monitor.ui.style import (
    BACKGROUND, BAD, FONT_BODY, FONT_TITLE, FONT_VALUE, MUTED, OK, PANEL, TEXT, UNKNOWN, WARN,
    format_duration, format_scale, format_time,
)
from aesa_monitor.ui.widgets import Table, VerdictBar, section_title

LEVEL_FOREGROUND = {"ok": OK, "bad": BAD, "warn": WARN, "info": TEXT}


class LinkCard:
    def __init__(self, parent: tk.Widget, title: str) -> None:
        self.frame = tk.Frame(parent, background=PANEL, padx=12, pady=8)
        self.title = tk.Label(self.frame, text=title, font=FONT_TITLE, background=PANEL, foreground=TEXT,
                              anchor="w")
        self.title.pack(fill=tk.X)
        self._status = tk.Label(self.frame, text="-", font=FONT_VALUE, background=PANEL, foreground=UNKNOWN,
                                anchor="w")
        self._status.pack(fill=tk.X)
        self._lines = tk.Label(self.frame, text="", font=FONT_BODY, background=PANEL, foreground=TEXT,
                               anchor="nw", justify=tk.LEFT)
        self._lines.pack(fill=tk.BOTH, expand=True)

    def set(self, connected: Optional[bool], lines, unknown_text: str = "알 수 없음") -> None:
        if connected is None:
            self._status.configure(text=unknown_text, foreground=UNKNOWN)
        elif connected:
            self._status.configure(text="● 연결됨", foreground=OK)
        else:
            self._status.configure(text="● 끊김", foreground=BAD)
        self._lines.configure(text="\n".join(lines))


class CommTab:
    def __init__(self, parent: tk.Widget, server_text: str) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND, padx=12, pady=8)
        self._server_text = server_text

        self._verdict = VerdictBar(self.frame, "연결이 살아 있고 메시지가 제때 오나?")
        self._verdict.frame.pack(fill=tk.X)

        cards = tk.Frame(self.frame, background=BACKGROUND)
        cards.pack(fill=tk.X, pady=(8, 0))
        self._processor = LinkCard(cards, "처리부 ↔ 모니터")
        self._console = LinkCard(cards, "운용통제기 ↔ 처리부")
        self._simulator = LinkCard(cards, "모의기 ↔ 처리부")
        for index, card in enumerate((self._processor, self._console, self._simulator)):
            card.frame.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            cards.columnconfigure(index, weight=1, uniform="link")

        section_title(self.frame, "모니터가 받은 메시지",
                      "처리부가 상대에게 보낸 것·받은 것의 복사본과 모니터 전용 상태 메시지입니다.\n"
                      "기대 속도: 빔 송신 명령과 탐지 정보는 처리부 시각 1초에 100개(처리부 속도 배율만큼 바뀜), "
                      "상태 메시지는 1초에 10개입니다. 트랙 정보는 탐지 결과를 처리할 때마다 나갑니다.")
        self._messages = Table(self.frame, (
            ("id", "ID", 80, tk.CENTER), ("name", "이름", 120, tk.W), ("route", "경로", 170, tk.W),
            ("count", "받은 수", 100, tk.E), ("rate", "속도", 90, tk.E), ("expected", "기대", 90, tk.E),
            ("age", "마지막 수신", 110, tk.E),
        ), height=5)
        self._messages.frame.pack(fill=tk.X)

        lower = tk.Frame(self.frame, background=BACKGROUND)
        lower.pack(fill=tk.BOTH, expand=True)
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        lower.rowconfigure(0, weight=1)
        events = tk.Frame(lower, background=BACKGROUND)
        events.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        section_title(events, "연결 이벤트 기록", "처리부 연결과 끊김, 운용통제기·모의기 연결 변화, 처리부 정지·재개, "
                      "처리부 재시작(처리부 시각이 되돌아감)을 시간순으로 남깁니다. 최근 것이 위에 있습니다.")
        self._events = Table(events, (
            ("time", "처리부 시각", 110, tk.E), ("ago", "경과", 90, tk.E), ("text", "내용", 360, tk.W),
        ), height=6)
        self._events.frame.pack(fill=tk.BOTH, expand=True)
        radar = tk.Frame(lower, background=BACKGROUND)
        radar.grid(row=0, column=1, sticky="nsew")
        section_title(radar, "레이더 정보 (0xAC02)")
        self._radar = tk.Label(radar, text="", font=FONT_BODY, background=PANEL, foreground=TEXT, anchor="nw",
                               justify=tk.LEFT, padx=12, pady=8)
        self._radar.pack(fill=tk.BOTH, expand=True)

    def refresh(self, state: MonitorState) -> None:
        self._verdict.set(comm_verdict(state))
        traffic = state.traffic
        now = state.clock()
        status = state.fresh_status()

        capture = state.source == SOURCE_CAPTURE
        status_age = traffic.age(MessageId.MONITOR_STATUS)
        self._processor.title.configure(text="통신 캡처 (모니터)" if capture else "처리부 ↔ 모니터")
        lines = [f"출처 {'통신 캡처' if capture else '복사본 수신'}"]
        if not capture:
            lines.append(f"주소 {self._server_text}")
        lines.append(f"{'캡처 시작' if capture else '접속'} {state.connect_count}회")
        if state.connected and state.connected_since is not None:
            lines.append(f"연결 유지 {format_duration(now - state.connected_since)}")
        elif not state.connected:
            lines.append(f"사유: {state.connection_detail or '-'}")
        if status_age is not None:
            lines.append(f"상태 메시지 {status_age * 1000:.0f} ms 전")
        if status is not None:
            lines.append(f"처리부 시각 {format_time(state.now_ms())}  ×{format_scale(status.time_scale)}"
                         + ("  정지" if status.paused else ""))
        self._processor.set(state.connected, lines)

        unknown_text = "알 수 없음 (아직 패킷 없음)" if capture else (
            "알 수 없음 (상태 없음)" if status is None else "알 수 없음")
        console = state.peer_connected(PEER_CONSOLE)
        track_age = traffic.age(MessageId.TRACK_INFORMATION)
        attitude_age = traffic.age(MessageId.RADAR_ATTITUDE_TO_CONSOLE)
        self._console.set(console, [
            f"트랙 정보(0xAC01) {traffic.rate(MessageId.TRACK_INFORMATION):.0f}/s",
            "자세(0xAC02) " + ("받은 적 없음" if attitude_age is None else f"{format_duration(attitude_age)} 전"),
            "" if track_age is None else f"마지막 트랙 정보 {track_age:.1f} s 전",
        ], unknown_text)
        simulator = state.peer_connected(PEER_SIMULATOR)
        beams = traffic.rate(MessageId.BEAM_TRANSMIT)
        detections = traffic.rate(MessageId.PLOT_INFORMATION)
        answer = f"{detections / beams * 100:.0f}%" if beams > 0 else "-"
        self._simulator.set(simulator, [
            f"빔 명령(0xAB01) {beams:.0f}/s",
            f"탐지 정보(0xBA01) {detections:.0f}/s",
            f"응답률 {answer} (빔 1개에 탐지 1개가 정상)",
        ], unknown_text)

        expected = {
            MessageId.BEAM_TRANSMIT: expected_beam_rate(state),
            MessageId.PLOT_INFORMATION: expected_beam_rate(state),
            MessageId.MONITOR_STATUS: 10.0,
        }
        rows = []
        paused = bool(state.paused())
        message_ids = [MessageId.BEAM_TRANSMIT, MessageId.PLOT_INFORMATION, MessageId.TRACK_INFORMATION,
                       MessageId.RADAR_ATTITUDE_TO_CONSOLE]
        if not capture or traffic.counts[MessageId.MONITOR_STATUS] > 0:
            message_ids.append(MessageId.MONITOR_STATUS)
        message_ids.extend(message_id for message_id in CAPTURE_ONLY_MESSAGES
                           if capture or traffic.counts[message_id] > 0)
        for message_id in message_ids:
            rate = traffic.rate(message_id)
            age = traffic.age(message_id)
            target = expected.get(message_id)
            tag = "text"
            if state.connected and target is not None and not paused:
                tag = "ok" if rate >= target * 0.8 else "warn"
            rows.append((str(int(message_id)), (
                f"0x{int(message_id):04X}", MESSAGE_NAMES[message_id], MESSAGE_ROUTES[message_id],
                traffic.counts[message_id], f"{rate:.0f}/s", "-" if target is None else f"{target:.0f}/s",
                "-" if age is None else f"{age:.1f} s 전",
            ), tag))
        self._messages.set_rows(rows)

        event_rows = []
        for index, entry in enumerate(reversed(state.event_log)):
            event_rows.append((str(len(state.event_log) - index), (
                format_time(entry.processor_ms), f"{max(now - entry.clock_s, 0):.0f} s 전", entry.text,
            ), {"ok": "ok", "bad": "bad", "warn": "warn"}.get(entry.level, "text")))
        self._events.set_rows(event_rows[:100])

        attitude = state.attitude
        if attitude is None:
            self._radar.configure(text="아직 받지 못했습니다.\n처리부는 모의기 자세정보를 받으면 운용통제기에 전달합니다.",
                                  foreground=MUTED)
        else:
            self._radar.configure(foreground=TEXT, text=(
                f"위도 {attitude.latitude_deg:.4f}°\n경도 {attitude.longitude_deg:.4f}°\n"
                f"고도 {attitude.altitude_km * 1000:.0f} m\n\n"
                f"roll {attitude.roll_deg:.2f}°\npitch {attitude.pitch_deg:.2f}°\nyaw {attitude.yaw_deg:.2f}°"))
