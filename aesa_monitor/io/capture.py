"""통신 캡처 출처와 캡처 파일 읽기.

실시간: 캡처 명령(기본 tcpdump)을 실행해 표준 출력의 pcap을 읽는다. 명령만 바꾸면 이 PC,
처리부 보드(ssh), 포트 미러링 인터페이스 어디서 캡처하든 같은 코드로 받는다.
    tcpdump -i any --immediate-mode -U -s 0 -w - tcp port 3200
    ssh root@192.168.10.20 tcpdump -i eth0 --immediate-mode -U -s 0 -w - tcp port 3200
캡처에는 권한이 필요하다(root 또는 tcpdump에 cap_net_raw). 권한이 없으면 명령이 바로 끝나고
그 오류가 연결 끊김 사유로 보인다.

파일: Wireshark·tcpdump로 저장한 pcap/pcapng를 녹화(Recording)로 바꿔 같은 재생·분석 화면에 쓴다.
"""

from __future__ import annotations

import queue
import shlex
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

from aesa_monitor.core.capture import CaptureDecoder, CapturedFrame, CapturedPeerLink, CaptureEndpoints
from aesa_monitor.core.config import MonitorConfig
from aesa_monitor.core.events import SOURCE_CAPTURE, ConnectionChanged, Event, PeerLinkChanged
from aesa_monitor.core.protocol import ProtocolError, decode
from aesa_monitor.core.settings import DEFAULT_CAPTURE_COMMAND
from aesa_monitor.io.pcap import PcapError, read_packets
from aesa_monitor.io.recording import KIND_CONNECTED, KIND_FRAME, KIND_PEER_LINK, Record, Recording

RESTART_DELAY_SECONDS = 2.0
CAPTURE_SUFFIXES = (".pcap", ".pcapng", ".cap")


def capture_endpoints(config: Optional[MonitorConfig]) -> CaptureEndpoints:
    if config is None:
        return CaptureEndpoints()
    return CaptureEndpoints(processor=config.server, console=config.console, simulator=config.simulator,
                            monitor=config.local)


def format_command(command: str, config: Optional[MonitorConfig]) -> str:
    port = config.server[1] if config is not None else 3200
    return command.replace("{port}", str(port))


class CaptureSource:
    """캡처 명령을 실행하고 받은 메시지를 큐에 넣는다. ProcessorConnection과 같은 방식으로 쓴다.

    명령이 끝나면(권한 없음, ssh 끊김 등) 연결 끊김으로 알리고 잠시 뒤 다시 실행한다.
    recorder가 있으면 꺼낸 프레임과 상대 연결 변화를 해석하기 전에 녹화한다.
    """

    source = SOURCE_CAPTURE

    def __init__(self, command: str, events: "queue.Queue[Event]", config: Optional[MonitorConfig] = None,
                 recorder=None) -> None:
        self.command = format_command(command, config)
        self._endpoints = capture_endpoints(config)
        self._events = events
        self.recorder = recorder
        self.connected = False
        self.decoder: Optional[CaptureDecoder] = None
        self.decode_errors = 0
        self._peer_links: Dict[str, bool] = {}
        self._recorder_lock = threading.Lock()
        self._stop = threading.Event()
        self._process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)

    @property
    def description(self) -> str:
        return f"캡처 명령: {self.command}"

    def recording_meta(self) -> Dict[str, str]:
        return {"source": SOURCE_CAPTURE, "capture_command": self.command}

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._process_lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        self._thread.join(timeout=3.0)

    def attach_recorder(self, recorder) -> None:
        """녹화를 시작한다. 이미 캡처 중이면 지금 상태(캡처, 상대 연결)를 첫 레코드로 남긴다."""
        with self._recorder_lock:
            if recorder is not None and self.connected:
                recorder.write_connection(True)
                for peer, connected in self._peer_links.items():
                    recorder.write_peer_link(peer, connected)
            self.recorder = recorder

    def _publish_connection(self, connected: bool, detail: str = "") -> None:
        with self._recorder_lock:
            self.connected = connected
            if not connected:
                self._peer_links.clear()
            if self.recorder is not None:
                self.recorder.write_connection(connected, detail)
        self._events.put(ConnectionChanged(connected, detail, SOURCE_CAPTURE))

    def _run(self) -> None:
        while not self._stop.is_set():
            detail = self._capture_once()
            if self.connected:
                self._publish_connection(False, detail)
            else:
                # 캡처를 시작하지 못했다(권한 없음 등). 사유만 알린다.
                self._events.put(ConnectionChanged(False, detail, SOURCE_CAPTURE))
            self._stop.wait(RESTART_DELAY_SECONDS)

    def _capture_once(self) -> str:
        try:
            arguments = shlex.split(self.command)
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (OSError, ValueError) as error:
            return f"캡처 명령 실행 실패: {error}"
        with self._process_lock:
            self._process = process
        errors: Deque[str] = deque(maxlen=5)
        reader = threading.Thread(target=_collect_lines, args=(process.stderr, errors), daemon=True)
        reader.start()
        self.decoder = CaptureDecoder(self._endpoints)
        detail = ""
        try:
            packets = read_packets(process.stdout, on_start=lambda: self._publish_connection(True))
            for packet in packets:
                if self._stop.is_set():
                    break
                self._handle(self.decoder.feed(packet.time_s, packet.linktype, packet.data))
        except PcapError as error:
            detail = f"캡처 출력 형식 오류: {error}"
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
            reader.join(timeout=1.0)
            with self._process_lock:
                self._process = None
        if self._stop.is_set():
            return "stopped"
        if detail:
            return detail
        message = errors[-1] if errors else ""
        return f"캡처 종료 (코드 {process.returncode}){': ' + message if message else ''}"

    def _handle(self, items: List[object]) -> None:
        for item in items:
            if isinstance(item, CapturedFrame):
                recorder = self.recorder
                if recorder is not None:
                    recorder.write_frame(item.frame)
                try:
                    self._events.put(decode(item.frame))
                except ProtocolError:
                    self.decode_errors += 1
            elif isinstance(item, CapturedPeerLink):
                with self._recorder_lock:
                    self._peer_links[item.peer] = item.connected
                    if self.recorder is not None:
                        self.recorder.write_peer_link(item.peer, item.connected, item.detail)
                self._events.put(PeerLinkChanged(item.peer, item.connected, item.detail))


def _collect_lines(stream, lines: Deque[str]) -> None:
    for raw in iter(stream.readline, b""):
        text = raw.decode("utf-8", "replace").strip()
        if text:
            lines.append(text)


def is_capture_file(path: Path) -> bool:
    return Path(path).suffix.lower() in CAPTURE_SUFFIXES


def read_capture_file(path: Path, config: Optional[MonitorConfig] = None) -> Recording:
    """캡처 파일을 녹화로 바꾼다. 시각은 첫 패킷 기준 초다.

    모니터 복사본 연결을 빼려고 두 번 읽는다. 처음에는 0xAD01이 오간 연결을 찾고, 다음에 그 연결을 빼고 꺼낸다.
    """
    path = Path(path)
    endpoints = capture_endpoints(config)
    first = CaptureDecoder(endpoints)
    with open(path, "rb") as stream:
        for packet in read_packets(stream):
            first.feed(packet.time_s, packet.linktype, packet.data)

    decoder = CaptureDecoder(endpoints, excluded=first.monitor_connections)
    records: List[Record] = []
    start: Optional[float] = None
    with open(path, "rb") as stream:
        for packet in read_packets(stream):
            if start is None:
                start = packet.time_s
                records.append(Record(0.0, KIND_CONNECTED, b""))
            time_s = max(packet.time_s - start, 0.0)
            for item in decoder.feed(packet.time_s, packet.linktype, packet.data):
                if isinstance(item, CapturedFrame):
                    records.append(Record(time_s, KIND_FRAME, item.frame))
                elif isinstance(item, CapturedPeerLink):
                    payload = f"{item.peer}|{int(item.connected)}|{item.detail}".encode("utf-8")
                    records.append(Record(time_s, KIND_PEER_LINK, payload))
    meta = {
        "source": SOURCE_CAPTURE,
        "capture_file": path.name,
        "packets": decoder.packets,
        "tcp_segments": decoder.segments,
        "skipped_bytes": decoder.skipped_bytes,
        "gaps": decoder.gaps,
        "excluded_monitor_connections": len(first.monitor_connections),
    }
    return Recording(path, meta, records)
