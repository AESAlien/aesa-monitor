"""통신 캡처: 패킷 → TCP 흐름 재조립 → 메시지 프레임.

데이터 출처가 캡처일 때 쓴다. 처리부·모의기·운용통제기가 실제로 주고받은 바이트를 그대로 보므로
처리부 코드를 바꾸지 않아도 된다. 대신 처리부 내부 상태(0xAD01)는 얻을 수 없다.

처리 순서
    1. 링크 계층(이더넷, Linux cooked, loopback 등)을 벗겨 IPv4/TCP 세그먼트를 꺼낸다.
    2. 연결 방향마다 순서 번호(seq)로 바이트 흐름을 다시 맞춘다. 재전송은 버리고, 순서가 바뀐
       세그먼트는 잠시 보관한다. 놓친 구간이 생기면 그 뒤부터 이어서 받는다.
    3. 방향마다 MessageFramer(resync=True)로 메시지를 자른다. 캡처를 연결 도중부터 시작했거나
       구간을 놓쳤으면 다음 올바른 헤더까지 바이트를 버린다.
    4. 연결의 상대(운용통제기, 모의기)를 정하고 연결 열림·닫힘을 알린다.

상대 판정
    .env의 주소(COMM_OPERATOR_CONSOLE_*, COMM_SCENARIO_SIMULATOR_*)가 있으면 그 주소·포트로 정한다.
    없으면 처음 본 메시지 종류로 정한다(0xAB01·0xBA01·0xBA02는 모의기, 0xAC0x·0xCA01은 운용통제기).
    모니터 복사본 연결(COMM_MONITOR_*, 또는 0xAD01이 오가는 연결)은 같은 메시지가 두 번 들어가지
    않도록 버린다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from aesa_monitor.core.events import PEER_CONSOLE, PEER_SIMULATOR
from aesa_monitor.core.protocol import HEADER, MessageFramer, MessageId, decode_header

Endpoint = Tuple[str, int]
ConnectionKey = FrozenSet[Endpoint]

LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LINUX_SLL = 113
LINKTYPE_IPV4 = 228
LINKTYPE_LINUX_SLL2 = 276

ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_VLAN = (0x8100, 0x88A8)
IP_PROTOCOL_TCP = 6

TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04

SEQUENCE_MASK = 0xFFFFFFFF
SEQUENCE_HALF = 0x80000000
# 순서가 바뀐 세그먼트를 이만큼 넘게 보관하면 앞 구간을 놓쳤다고 보고 건너뛴다.
MAXIMUM_PENDING_BYTES = 256 * 1024

SIMULATOR_MESSAGES = (MessageId.BEAM_TRANSMIT, MessageId.PLOT_INFORMATION, MessageId.RADAR_ATTITUDE_FROM_SIMULATOR)
CONSOLE_MESSAGES = (MessageId.TRACK_INFORMATION, MessageId.RADAR_ATTITUDE_TO_CONSOLE, MessageId.TRACK_DELETION,
                    MessageId.OPERATOR_CONTROL)


@dataclass(frozen=True)
class TcpSegment:
    source: Endpoint
    destination: Endpoint
    sequence: int
    flags: int
    payload: bytes


@dataclass(frozen=True)
class CapturedFrame:
    """캡처에서 꺼낸 메시지 프레임 하나."""

    time_s: float
    source: Endpoint
    destination: Endpoint
    peer: Optional[str]
    frame: bytes


@dataclass(frozen=True)
class CapturedPeerLink:
    """처리부와 상대의 연결이 열리거나 닫혔다."""

    time_s: float
    peer: str
    connected: bool
    detail: str = ""


@dataclass(frozen=True)
class CaptureEndpoints:
    """.env에서 읽은 주소. 모르는 값은 None이다."""

    processor: Optional[Endpoint] = None
    console: Optional[Endpoint] = None
    simulator: Optional[Endpoint] = None
    monitor: Optional[Endpoint] = None


def parse_tcp_segment(linktype: int, data: bytes) -> Optional[TcpSegment]:
    """링크 계층 프레임에서 IPv4/TCP 세그먼트를 꺼낸다. TCP가 아니면 None이다."""
    ip = _ip_payload(linktype, data)
    if ip is None or len(ip) < 20 or ip[0] >> 4 != 4:
        return None
    header_length = (ip[0] & 0x0F) * 4
    total_length = struct.unpack_from(">H", ip, 2)[0]
    flags_fragment = struct.unpack_from(">H", ip, 6)[0]
    if ip[9] != IP_PROTOCOL_TCP or header_length < 20:
        return None
    if flags_fragment & 0x3FFF:
        return None  # 조각난 IP 패킷. 처리부 통신에서는 생기지 않는다고 보고 버린다.
    if total_length == 0:
        total_length = len(ip)  # TSO로 캡처되면 0일 수 있다
    ip = ip[:total_length]
    source_address = ".".join(str(byte) for byte in ip[12:16])
    destination_address = ".".join(str(byte) for byte in ip[16:20])
    tcp = ip[header_length:]
    if len(tcp) < 20:
        return None
    source_port, destination_port, sequence = struct.unpack_from(">HHI", tcp, 0)
    tcp_header_length = (tcp[12] >> 4) * 4
    flags = tcp[13]
    if tcp_header_length < 20 or tcp_header_length > len(tcp):
        return None
    return TcpSegment((source_address, source_port), (destination_address, destination_port), sequence, flags,
                      bytes(tcp[tcp_header_length:]))


def _ip_payload(linktype: int, data: bytes) -> Optional[bytes]:
    if linktype == LINKTYPE_ETHERNET:
        offset = 12
        if len(data) < offset + 2:
            return None
        ethertype = struct.unpack_from(">H", data, offset)[0]
        while ethertype in ETHERTYPE_VLAN and len(data) >= offset + 6:
            offset += 4
            ethertype = struct.unpack_from(">H", data, offset)[0]
        return data[offset + 2:] if ethertype == ETHERTYPE_IPV4 else None
    if linktype == LINKTYPE_LINUX_SLL:
        if len(data) < 16 or struct.unpack_from(">H", data, 14)[0] != ETHERTYPE_IPV4:
            return None
        return data[16:]
    if linktype == LINKTYPE_LINUX_SLL2:
        if len(data) < 20 or struct.unpack_from(">H", data, 0)[0] != ETHERTYPE_IPV4:
            return None
        return data[20:]
    if linktype == LINKTYPE_NULL:
        if len(data) < 4:
            return None
        family = struct.unpack_from("<I", data, 0)[0]
        if family != 2 and struct.unpack_from(">I", data, 0)[0] != 2:
            return None
        return data[4:]
    if linktype in (LINKTYPE_RAW, LINKTYPE_IPV4):
        return data
    return None


class TcpStream:
    """TCP 연결의 한 방향. 순서 번호로 바이트를 다시 맞추고 메시지로 자른다."""

    def __init__(self) -> None:
        self.next_sequence: Optional[int] = None
        self._pending: Dict[int, bytes] = {}
        self._pending_bytes = 0
        self.framer = MessageFramer(resync=True)
        self.gaps = 0
        self.duplicate_bytes = 0

    def restart(self, sequence: int) -> None:
        """SYN을 봤다. 같은 포트로 새 연결이 열렸을 수 있으므로 처음부터 다시 받는다."""
        self.next_sequence = (sequence + 1) & SEQUENCE_MASK
        self._pending.clear()
        self._pending_bytes = 0
        self.framer = MessageFramer(resync=True)

    def feed(self, sequence: int, payload: bytes) -> List[bytes]:
        """세그먼트를 넣고 완성된 메시지 프레임을 돌려준다."""
        if not payload:
            return []
        if self.next_sequence is None:
            # SYN을 못 본 연결(캡처를 도중에 시작). 처음 본 세그먼트부터 받는다.
            self.next_sequence = sequence
        frames: List[bytes] = []
        offset = (sequence - self.next_sequence) & SEQUENCE_MASK
        if offset >= SEQUENCE_HALF:
            overlap = (self.next_sequence - sequence) & SEQUENCE_MASK
            if overlap >= len(payload):
                self.duplicate_bytes += len(payload)
                return []
            self.duplicate_bytes += overlap
            payload = payload[overlap:]
            sequence = self.next_sequence
            offset = 0
        if offset == 0:
            frames.extend(self._deliver(payload))
            frames.extend(self._drain())
            return frames
        if sequence not in self._pending or len(self._pending[sequence]) < len(payload):
            self._pending_bytes += len(payload) - len(self._pending.get(sequence, b""))
            self._pending[sequence] = payload
        if self._pending_bytes > MAXIMUM_PENDING_BYTES:
            frames.extend(self._skip_gap())
        return frames

    def _deliver(self, payload: bytes) -> List[bytes]:
        assert self.next_sequence is not None
        self.next_sequence = (self.next_sequence + len(payload)) & SEQUENCE_MASK
        return self.framer.feed(payload)

    def _drain(self) -> List[bytes]:
        frames: List[bytes] = []
        progressed = True
        while progressed and self._pending:
            progressed = False
            for sequence in list(self._pending):
                assert self.next_sequence is not None
                offset = (sequence - self.next_sequence) & SEQUENCE_MASK
                payload = self._pending[sequence]
                if offset == 0 or offset >= SEQUENCE_HALF:
                    del self._pending[sequence]
                    self._pending_bytes -= len(payload)
                    overlap = 0 if offset == 0 else (self.next_sequence - sequence) & SEQUENCE_MASK
                    if overlap < len(payload):
                        frames.extend(self._deliver(payload[overlap:]))
                    progressed = True
        return frames

    def _skip_gap(self) -> List[bytes]:
        """앞 구간을 놓쳤다. 보관한 것 중 가장 앞 세그먼트부터 이어서 받는다."""
        assert self.next_sequence is not None
        base = self.next_sequence
        first = min(self._pending, key=lambda sequence: (sequence - base) & SEQUENCE_MASK)
        self.gaps += 1
        self.framer.reset()
        self.next_sequence = first
        return self._drain()


@dataclass
class _Connection:
    peer: Optional[str] = None
    open: bool = False
    streams: Dict[Endpoint, TcpStream] = field(default_factory=dict)


class CaptureDecoder:
    """패킷을 넣으면 메시지 프레임과 상대 연결 변화를 돌려준다. 입출력은 하지 않는다."""

    def __init__(self, endpoints: Optional[CaptureEndpoints] = None,
                 excluded: Iterable[ConnectionKey] = ()) -> None:
        self.endpoints = endpoints or CaptureEndpoints()
        self._excluded: Set[ConnectionKey] = set(excluded)
        self._connections: Dict[ConnectionKey, _Connection] = {}
        self._open_count: Dict[str, int] = {}
        self.packets = 0
        self.segments = 0
        self.ignored_packets = 0
        # 모니터 복사본 연결(0xAD01이 오간 연결). 파일을 두 번 읽을 때 처음 읽기에서 모은다.
        self.monitor_connections: Set[ConnectionKey] = set()

    @property
    def skipped_bytes(self) -> int:
        return sum(stream.framer.skipped_bytes for connection in self._connections.values()
                   for stream in connection.streams.values())

    @property
    def gaps(self) -> int:
        return sum(stream.gaps for connection in self._connections.values() for stream in connection.streams.values())

    def feed(self, time_s: float, linktype: int, data: bytes) -> List[object]:
        self.packets += 1
        segment = parse_tcp_segment(linktype, data)
        if segment is None:
            self.ignored_packets += 1
            return []
        self.segments += 1
        key: ConnectionKey = frozenset((segment.source, segment.destination))
        if key in self._excluded or self._is_monitor(segment):
            return []
        connection = self._connections.get(key)
        if connection is None:
            connection = _Connection(peer=self._peer_by_address(segment))
            self._connections[key] = connection
        stream = connection.streams.get(segment.source)
        if stream is None:
            stream = connection.streams[segment.source] = TcpStream()

        output: List[object] = []
        if segment.flags & TCP_SYN:
            stream.restart(segment.sequence)
            output.extend(self._open(connection, time_s))
        frames = stream.feed(segment.sequence, segment.payload)
        if frames and not connection.open:
            output.extend(self._open(connection, time_s))
        for frame in frames:
            message_id = decode_header(frame[:HEADER.size]).message_id
            if message_id == MessageId.MONITOR_STATUS:
                # 모니터 복사본 연결이다. 이번에 꺼낸 프레임까지 모두 버리고 이 연결은 더 보지 않는다.
                self.monitor_connections.add(key)
                self._excluded.add(key)
                output = [item for item in output if not isinstance(item, CapturedFrame)]
                output.extend(self._close(connection, time_s, ""))
                del self._connections[key]
                return output
            if connection.peer is None:
                connection.peer = _peer_by_message(message_id)
                if connection.open and connection.peer is not None:
                    output.extend(self._announce(connection.peer, time_s))
            output.append(CapturedFrame(time_s, segment.source, segment.destination, connection.peer, frame))
        if segment.flags & (TCP_FIN | TCP_RST):
            output.extend(self._close(connection, time_s, "RST" if segment.flags & TCP_RST else "FIN"))
        return output

    def _is_monitor(self, segment: TcpSegment) -> bool:
        monitor = self.endpoints.monitor
        return monitor is not None and monitor in (segment.source, segment.destination)

    def _peer_by_address(self, segment: TcpSegment) -> Optional[str]:
        for peer, endpoint in ((PEER_CONSOLE, self.endpoints.console), (PEER_SIMULATOR, self.endpoints.simulator)):
            if endpoint is not None and endpoint in (segment.source, segment.destination):
                return peer
        return None

    def _open(self, connection: _Connection, time_s: float) -> List[object]:
        if connection.open:
            return []
        connection.open = True
        if connection.peer is None:
            return []
        return self._announce(connection.peer, time_s)

    def _announce(self, peer: str, time_s: float) -> List[object]:
        count = self._open_count.get(peer, 0) + 1
        self._open_count[peer] = count
        return [CapturedPeerLink(time_s, peer, True)] if count == 1 else []

    def _close(self, connection: _Connection, time_s: float, detail: str) -> List[object]:
        if not connection.open:
            return []
        connection.open = False
        peer = connection.peer
        if peer is None:
            return []
        count = max(self._open_count.get(peer, 0) - 1, 0)
        self._open_count[peer] = count
        return [CapturedPeerLink(time_s, peer, False, detail)] if count == 0 else []


def _peer_by_message(message_id: int) -> Optional[str]:
    if message_id in SIMULATOR_MESSAGES:
        return PEER_SIMULATOR
    if message_id in CONSOLE_MESSAGES:
        return PEER_CONSOLE
    return None
