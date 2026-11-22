"""통신 탭 — "연결이 살아 있고 메시지가 제때 오나?"

모니터는 처리부에만 연결된다. 운용통제기·모의기 연결 상태는 처리부가 0xAD01로 알려준 값이고,
메시지 속도는 처리부가 상대에게 보낸 것과 받은 것의 복사본을 모니터가 받은 속도다.

배치(#6): 판정 줄 / 연결 카드 3개(상태는 카드 제목 줄 오른쪽) / [받은 메시지 표 | 연결 이벤트 기록].
1366×768·글자 16에서 연결 이벤트 기록과 레이더 정보가 아래로 밀려 보이지 않던 것을 고쳤다.
- 처리부 시각·배율은 상단 바에, 출처(복사본/캡처)는 카드 제목에 있으므로 카드 줄에서 뺐다.
- 레이더 자세(0xAC02)는 처리부가 운용통제기에 보내는 메시지이므로 운용통제기 카드 안에 둔다.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional, Sequence

from aesa_monitor.core.events import PEER_CONSOLE, PEER_SIMULATOR, SOURCE_CAPTURE, SOURCE_LOG
from aesa_monitor.core.protocol import MessageId
from aesa_monitor.core.state import MonitorState
from aesa_monitor.core.traffic import CAPTURE_ONLY_MESSAGES, MESSAGE_NAMES, MESSAGE_ROUTES
from aesa_monitor.core.verdict import comm_verdict, expected_beam_rate
from aesa_monitor.ui.style import (
    BACKGROUND, BAD, FONT_BODY, FONT_TITLE, OK, PANEL, TEXT, UNKNOWN, WARN, format_duration, format_time,
)
from aesa_monitor.ui.widgets import Table, Tooltip, VerdictBar, section_title

LEVEL_FOREGROUND = {"ok": OK, "bad": BAD, "warn": WARN, "info": TEXT}


class LinkCard:
    """연결 하나: 제목 줄(이름 … ● 연결됨)과 그 아래 몇 줄."""

    def __init__(self, parent: tk.Widget, title: str) -> None:
        self.frame = tk.Frame(parent, background=PANEL, padx=10, pady=6)
        row = tk.Frame(self.frame, background=PANEL)
        row.pack(fill=tk.X)
        self.title = tk.Label(row, text=title, font=FONT_TITLE, background=PANEL, foreground=TEXT, anchor="w")
        self.title.pack(side=tk.LEFT)
        self._status = tk.Label(row, text="-", font=FONT_TITLE, background=PANEL, foreground=UNKNOWN, anchor="e")
        self._status.pack(side=tk.RIGHT, padx=(8, 0))
        self._lines = tk.Label(self.frame, text="", font=FONT_BODY, background=PANEL, foreground=TEXT,
                               anchor="nw", justify=tk.LEFT)
        self._lines.pack(fill=tk.BOTH, expand=True)
        # 좁은 창에서 줄이 카드 밖으로 잘리지 않고 접히게 한다.
        self._lines.bind("<Configure>", lambda event: self._lines.configure(wraplength=max(event.width - 4, 80)))

    def set(self, connected: Optional[bool], lines: Sequence[str], unknown_text: str = "알 수 없음") -> None:
        if connected is None:
            status, color = unknown_text, UNKNOWN
        elif connected:
            status, color = "● 연결됨", OK
        else:
            status, color = "● 끊김", BAD
        self._status.configure(text=status, foreground=color)
        self._lines.configure(text="\n".join(line for line in lines if line))


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
        Tooltip(self._console.frame, "레이더 자세(0xAC02): 처리부는 모의기 자세정보를 받으면 운용통제기에 전달합니다.\n"
                "위치는 위도·경도·고도입니다.")
        Tooltip(self._simulator.frame, "응답률: 빔 명령 1개에 탐지 정보 1개가 오면 100%입니다.")

        # 아래: 받은 메시지 표 | 연결 이벤트 기록. 위아래로 쌓으면 1366×768에서 연결 이벤트 기록이 보이지 않았다.
        lower = tk.Frame(self.frame, background=BACKGROUND)
        lower.pack(fill=tk.BOTH, expand=True)
        lower.columnconfigure(0, weight=9, uniform="comm")
        lower.columnconfigure(1, weight=5, uniform="comm")
        lower.rowconfigure(0, weight=1)
        messages = tk.Frame(lower, background=BACKGROUND)
        messages.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        section_title(messages, "모니터가 받은 메시지",
                      "처리부가 상대에게 보낸 것·받은 것의 복사본과 모니터 전용 상태 메시지입니다.\n"
                      "기대 속도: 빔 송신 명령과 탐지 정보는 처리부 시각 1초에 100개(처리부 속도 배율만큼 바뀜), "
                      "상태 메시지는 1초에 10개입니다. 트랙 정보는 탐지 결과를 처리할 때마다 나갑니다.")
        self._messages = Table(messages, (
            ("id", "ID", 85, tk.CENTER), ("name", "이름", 130, tk.W), ("route", "경로", 200, tk.W),
            ("count", "받은 수", 90, tk.E), ("rate", "속도", 80, tk.E), ("expected", "기대", 80, tk.E),
            ("age", "마지막 수신", 115, tk.E),
        ), height=5)
        self._messages.frame.pack(fill=tk.BOTH, expand=True)
        events = tk.Frame(lower, background=BACKGROUND)
        events.grid(row=0, column=1, sticky="nsew")
        section_title(events, "연결 이벤트 기록", "처리부 연결과 끊김, 운용통제기·모의기 연결 변화, 처리부 정지·재개, "
                      "처리부 재시작(처리부 시각이 되돌아감)을 시간순으로 남깁니다. 최근 것이 위에 있습니다.")
        self._events = Table(events, (
            ("time", "처리부 시각", 125, tk.E), ("ago", "경과", 80, tk.E), ("text", "내용", 250, tk.W),
        ), height=3)
        self._events.frame.pack(fill=tk.BOTH, expand=True)
        # 내용 열이 좁아 잘리는 긴 사유(예: 연결 끊김 사유)를 마우스를 올리면 다 보이게 한다.
        Tooltip(self._events.tree, self._hovered_event_text)
        self._event_texts: dict = {}

    def _hovered_event_text(self) -> str:
        tree = self._events.tree
        row = tree.identify_row(tree.winfo_pointery() - tree.winfo_rooty())
        return self._event_texts.get(row, "")

    def refresh(self, state: MonitorState) -> None:
        self._verdict.set(comm_verdict(state))
        traffic = state.traffic
        now = state.clock()
        status = state.fresh_status()

        capture = state.source == SOURCE_CAPTURE
        status_age = traffic.age(MessageId.MONITOR_STATUS)
        self._processor.title.configure(text="통신 캡처 (모니터)" if capture else "처리부 ↔ 모니터")
        lines = []
        if not capture:
            lines.append(f"주소 {self._server_text}")
        connect = f"{'캡처 시작' if capture else '접속'} {state.connect_count}회"
        if state.connected and state.connected_since is not None:
            connect += f" · 유지 {format_duration(now - state.connected_since)}"
        lines.append(connect)
        if not state.connected:
            lines.append(f"사유: {state.connection_detail or '-'}")
        if status_age is not None:
            lines.append(f"상태 메시지 {status_age * 1000:.0f} ms 전" + ("  (처리부 정지)" if status and status.paused else ""))
        if state.source == SOURCE_LOG:
            # 처리부 로그 재생: 연결·통신 기록이 없다. 아래 메시지 수는 탐지 CSV로 만든 값이다.
            self._processor.title.configure(text="처리부 로그 재생")
            lines = ["통신 기록 없음 (녹화가 없어 처리부 로그로 재생)",
                     "메시지 수: 탐지 CSV로 만든 빔·탐지·트랙 목록"]
            self._processor.set(None, lines, "기록 없음")
        else:
            self._processor.set(state.connected, lines)

        unknown_text = "알 수 없음"
        if capture and not traffic.counts[MessageId.MONITOR_STATUS]:
            unknown_text = "알 수 없음 (패킷 없음)"
        elif not capture and status is None:
            unknown_text = "알 수 없음 (상태 없음)"
        console = state.peer_connected(PEER_CONSOLE)
        track_age = traffic.age(MessageId.TRACK_INFORMATION)
        console_lines = [f"트랙 정보(0xAC01) {traffic.rate(MessageId.TRACK_INFORMATION):.0f}/s"
                         + ("" if track_age is None else f" · {track_age:.1f} s 전")]
        # 마지막으로 받은 시각은 아래 메시지 표(0xAC02 마지막 수신)에 있다.
        attitude = state.attitude
        if attitude is None:
            console_lines.append("레이더 자세(0xAC02) 받은 적 없음")
        else:
            console_lines.append(f"자세 {attitude.latitude_deg:.4f}°, {attitude.longitude_deg:.4f}°, "
                                 f"{attitude.altitude_km * 1000:.0f} m")
            console_lines.append(f"roll {attitude.roll_deg:.2f}°  pitch {attitude.pitch_deg:.2f}°  "
                                 f"yaw {attitude.yaw_deg:.2f}°")
        self._console.set(console, console_lines, unknown_text)
        simulator = state.peer_connected(PEER_SIMULATOR)
        beams = traffic.rate(MessageId.BEAM_TRANSMIT)
        detections = traffic.rate(MessageId.PLOT_INFORMATION)
        answer = f"{detections / beams * 100:.0f}%" if beams > 0 else "-"
        self._simulator.set(simulator, [
            f"빔 명령(0xAB01) {beams:.0f}/s",
            f"탐지 정보(0xBA01) {detections:.0f}/s",
            f"응답률 {answer}",
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
        # 표 높이를 메시지 종류 수(실시간 5, 캡처 8)에 맞춰 스크롤 없이 다 보이게 한다.
        if int(self._messages.tree.cget("height")) != len(rows):
            self._messages.tree.configure(height=len(rows))

        event_rows = []
        texts = {}
        for index, entry in enumerate(reversed(state.event_log)):
            iid = str(len(state.event_log) - index)
            texts[iid] = entry.text
            event_rows.append((iid, (
                format_time(entry.processor_ms), f"{max(now - entry.clock_s, 0):.0f} s 전", entry.text,
            ), {"ok": "ok", "bad": "bad", "warn": "warn"}.get(entry.level, "text")))
        self._event_texts = texts
        self._events.set_rows(event_rows[:100])
