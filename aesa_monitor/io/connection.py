"""복사본 수신 출처: 데이터 처리부 TCP 서버 접속과 재접속.

다른 출처(io/capture.py의 CaptureSource)와 같은 방식으로 쓴다:
start(), stop(), attach_recorder(recorder), connected, source, description, recording_meta().
create_source()가 설정(MONITOR_SOURCE)에 따라 둘 중 하나를 만든다.
"""

from __future__ import annotations

import queue
import socket
import threading
from typing import Dict, Optional

from aesa_monitor.core.config import MonitorConfig
from aesa_monitor.core.events import SOURCE_COPY, ConnectionChanged, Event
from aesa_monitor.core.protocol import MessageFramer, ProtocolError, decode

RECONNECT_DELAY_SECONDS = 1.0
RECEIVE_SIZE = 65536


class ProcessorConnection:
    """수신 스레드에서 처리부에 접속하고 받은 메시지를 큐에 넣는다.

    연결이 끊기거나 프레임이 잘못되면 연결을 닫고 일정 시간 뒤 다시 접속한다.
    모니터는 처리부에 아무것도 보내지 않는다.
    recorder가 있으면 받은 프레임을 해석하기 전에 그대로 녹화한다.
    """

    source = SOURCE_COPY

    def __init__(self, config: MonitorConfig, events: "queue.Queue[Event]", recorder=None) -> None:
        self._config = config
        self._events = events
        self.recorder = recorder
        self.connected = False
        self._recorder_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="processor-connection", daemon=True)
        self._socket: Optional[socket.socket] = None
        self._lock = threading.Lock()

    @property
    def description(self) -> str:
        return f"처리부 {self._config.server[0]}:{self._config.server[1]} ← {self._config.local[0]}:{self._config.local[1]}"

    def recording_meta(self) -> Dict[str, str]:
        return {"source": SOURCE_COPY,
                "server": f"{self._config.server[0]}:{self._config.server[1]}",
                "local": f"{self._config.local[0]}:{self._config.local[1]}"}

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self._thread.join(timeout=2.0)

    def attach_recorder(self, recorder) -> None:
        """녹화를 시작한다. 이미 연결되어 있으면 녹화 첫 레코드로 연결 상태를 남긴다."""
        with self._recorder_lock:
            if recorder is not None and self.connected:
                recorder.write_connection(True)
            self.recorder = recorder

    def _publish_connection(self, connected: bool, detail: str = "") -> None:
        with self._recorder_lock:
            self.connected = connected
            recorder = self.recorder
            if recorder is not None:
                recorder.write_connection(connected, detail)
        self._events.put(ConnectionChanged(connected, detail))

    def _run(self) -> None:
        while not self._stop.is_set():
            detail = self._connect_and_receive()
            self._publish_connection(False, detail)
            self._stop.wait(RECONNECT_DELAY_SECONDS)

    def _connect_and_receive(self) -> str:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError as error:
            return f"socket: {error}"

        with self._lock:
            self._socket = sock
        try:
            # 이전 연결이 TIME_WAIT 상태여도 같은 로컬 포트로 다시 접속할 수 있게 한다.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(self._config.local)
            sock.connect(self._config.server)
            self._publish_connection(True)
            return self._receive(sock)
        except OSError as error:
            return str(error)
        finally:
            with self._lock:
                self._socket = None
            sock.close()

    def _receive(self, sock: socket.socket) -> str:
        framer = MessageFramer()
        while not self._stop.is_set():
            data = sock.recv(RECEIVE_SIZE)
            if not data:
                return "closed by processor"
            try:
                for frame in framer.feed(data):
                    recorder = self.recorder
                    if recorder is not None:
                        recorder.write_frame(frame)
                    self._events.put(decode(frame))
            except ProtocolError as error:
                return f"protocol error: {error}"
        return "stopped"


def create_source(kind: str, config: MonitorConfig, events: "queue.Queue[Event]", capture_command: str = ""):
    """데이터 출처를 만든다. kind는 copy(복사본 수신) 또는 capture(통신 캡처)다."""
    if kind == SOURCE_COPY:
        return ProcessorConnection(config, events)
    if kind == "capture":
        from aesa_monitor.core.settings import DEFAULT_CAPTURE_COMMAND
        from aesa_monitor.io.capture import CaptureSource

        return CaptureSource(capture_command or DEFAULT_CAPTURE_COMMAND, events, config)
    raise ValueError(f"unknown data source: {kind}")
