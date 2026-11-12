"""pcap·pcapng 패킷 읽기. 표준 라이브러리만 쓴다.

통신 캡처에서 tcpdump가 표준 출력으로 내보내는 pcap(`tcpdump -U -w -`)과
Wireshark가 저장한 파일(pcapng 또는 pcap)을 같은 코드로 읽는다.
스트림을 앞에서부터 한 번만 읽으므로 끝나지 않는 파이프에도 쓸 수 있다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterator, List, Optional, Tuple

LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LINUX_SLL = 113
LINKTYPE_IPV4 = 228
LINKTYPE_LINUX_SLL2 = 276

PCAPNG_SECTION_HEADER = 0x0A0D0D0A
PCAPNG_BYTE_ORDER_MAGIC = 0x1A2B3C4D
PCAPNG_INTERFACE_DESCRIPTION = 1
PCAPNG_OBSOLETE_PACKET = 2
PCAPNG_SIMPLE_PACKET = 3
PCAPNG_ENHANCED_PACKET = 6
PCAPNG_OPTION_TSRESOL = 9
# 블록 하나의 상한. 이보다 크면 파일이 깨졌다고 본다.
MAXIMUM_BLOCK_SIZE = 16 * 1024 * 1024


class PcapError(ValueError):
    """pcap·pcapng 형식이 맞지 않는다."""


@dataclass(frozen=True)
class Packet:
    time_s: float  # 캡처 시각(유닉스 시각, 초)
    linktype: int
    data: bytes


def read_packets(stream: BinaryIO, on_start: Optional[Callable[[], None]] = None) -> Iterator[Packet]:
    """스트림의 패킷을 차례로 돌려준다. 끝이 잘려 있으면 완전한 패킷까지만 돌려준다.

    on_start는 파일 헤더를 읽은 직후 한 번 부른다. 패킷이 아직 없어도 캡처가 시작됐음을 알 수 있다.
    """
    magic = _read_exact(stream, 4)
    if magic is None:
        return
    if struct.unpack("<I", magic)[0] == PCAPNG_SECTION_HEADER:
        yield from _read_pcapng(stream, magic, on_start)
    else:
        yield from _read_pcap(stream, magic, on_start)


def _read_exact(stream: BinaryIO, size: int) -> Optional[bytes]:
    """size 바이트를 읽는다. 하나도 못 읽고 끝나면 None, 중간에 끝나면 PcapError."""
    chunks: List[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if not data and size > 0:
        return None
    if len(data) < size:
        raise PcapError("truncated")
    return data


# pcap

PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6),
    b"\xa1\xb2\xc3\xd4": (">", 1e-6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9),
    b"\xa1\xb2\x3c\x4d": (">", 1e-9),
}


def _read_pcap(stream: BinaryIO, magic: bytes, on_start: Optional[Callable[[], None]]) -> Iterator[Packet]:
    if magic not in PCAP_MAGICS:
        raise PcapError("pcap·pcapng 파일이 아닙니다")
    endian, resolution = PCAP_MAGICS[magic]
    header = _read_exact(stream, 20)
    if header is None:
        raise PcapError("pcap 헤더가 없습니다")
    _major, _minor, _zone, _sigfigs, _snaplen, linktype = struct.unpack(endian + "HHiIII", header)
    linktype &= 0x0FFFFFFF  # 위쪽 비트는 FCS 정보
    if on_start is not None:
        on_start()
    record = struct.Struct(endian + "IIII")
    while True:
        try:
            raw = _read_exact(stream, record.size)
            if raw is None:
                return
            seconds, fraction, captured, _original = record.unpack(raw)
            if captured > MAXIMUM_BLOCK_SIZE:
                raise PcapError(f"패킷 크기가 비정상입니다 ({captured})")
            data = _read_exact(stream, captured) if captured else b""
        except PcapError as error:
            if str(error) == "truncated":
                return
            raise
        yield Packet(seconds + fraction * resolution, linktype, data or b"")


# pcapng

def _read_pcapng(stream: BinaryIO, first_type: bytes,
                 on_start: Optional[Callable[[], None]]) -> Iterator[Packet]:
    endian = "<"
    interfaces: List[Tuple[int, float]] = []
    block_type_raw: Optional[bytes] = first_type
    while True:
        try:
            if block_type_raw is None:
                block_type_raw = _read_exact(stream, 4)
                if block_type_raw is None:
                    return
            length_raw = _read_exact(stream, 4)
            if length_raw is None:
                return
            if struct.unpack("<I", block_type_raw)[0] == PCAPNG_SECTION_HEADER:
                order = _read_exact(stream, 4)
                if order is None:
                    return
                if struct.unpack("<I", order)[0] == PCAPNG_BYTE_ORDER_MAGIC:
                    endian = "<"
                elif struct.unpack(">I", order)[0] == PCAPNG_BYTE_ORDER_MAGIC:
                    endian = ">"
                else:
                    raise PcapError("pcapng 바이트 순서를 알 수 없습니다")
                (length,) = struct.unpack(endian + "I", length_raw)
                _check_block_length(length, 16)
                _read_exact(stream, length - 12)  # 나머지 본문과 뒤쪽 길이
                interfaces = []
                block_type_raw = None
                if on_start is not None:
                    on_start()
                    on_start = None
                continue
            (block_type,) = struct.unpack(endian + "I", block_type_raw)
            (length,) = struct.unpack(endian + "I", length_raw)
            _check_block_length(length, 12)
            body = _read_exact(stream, length - 12) or b""
            _read_exact(stream, 4)  # 뒤쪽 길이
        except PcapError as error:
            if str(error) == "truncated":
                return
            raise
        block_type_raw = None
        packet = _pcapng_block(endian, block_type, body, interfaces)
        if packet is not None:
            yield packet


def _check_block_length(length: int, minimum: int) -> None:
    if length < minimum or length % 4 != 0 or length > MAXIMUM_BLOCK_SIZE:
        raise PcapError(f"pcapng 블록 길이가 비정상입니다 ({length})")


def _pcapng_block(endian: str, block_type: int, body: bytes,
                  interfaces: List[Tuple[int, float]]) -> Optional[Packet]:
    if block_type == PCAPNG_INTERFACE_DESCRIPTION:
        linktype, _reserved, _snaplen = struct.unpack_from(endian + "HHI", body, 0)
        interfaces.append((linktype, _timestamp_resolution(endian, body[8:])))
        return None
    if block_type == PCAPNG_ENHANCED_PACKET:
        interface, high, low, captured, _original = struct.unpack_from(endian + "IIIII", body, 0)
        return _interface_packet(interfaces, interface, (high << 32) | low, body[20:20 + captured])
    if block_type == PCAPNG_OBSOLETE_PACKET:
        interface, _drops, high, low, captured, _original = struct.unpack_from(endian + "HHIIII", body, 0)
        return _interface_packet(interfaces, interface, (high << 32) | low, body[20:20 + captured])
    if block_type == PCAPNG_SIMPLE_PACKET and interfaces:
        # 시각이 없는 블록. 시각 0으로 둔다.
        return Packet(0.0, interfaces[0][0], body[4:])
    return None


def _interface_packet(interfaces: List[Tuple[int, float]], interface: int, timestamp: int,
                      data: bytes) -> Optional[Packet]:
    if interface >= len(interfaces):
        return None
    linktype, resolution = interfaces[interface]
    return Packet(timestamp * resolution, linktype, data)


def _timestamp_resolution(endian: str, options: bytes) -> float:
    offset = 0
    while offset + 4 <= len(options):
        code, length = struct.unpack_from(endian + "HH", options, offset)
        if code == 0:
            break
        value = options[offset + 4:offset + 4 + length]
        if code == PCAPNG_OPTION_TSRESOL and value:
            exponent = value[0] & 0x7F
            return 2.0 ** -exponent if value[0] & 0x80 else 10.0 ** -exponent
        offset += 4 + (length + 3) // 4 * 4
    return 1e-6


# 시험용 쓰기

def pcap_header(linktype: int = LINKTYPE_ETHERNET) -> bytes:
    return struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, linktype)


def pcap_record(time_s: float, data: bytes) -> bytes:
    seconds = int(time_s)
    micros = int(round((time_s - seconds) * 1e6))
    return struct.pack("<IIII", seconds, micros, len(data), len(data)) + data


def pcapng_bytes(packets: List[Tuple[float, bytes]], linktype: int = LINKTYPE_ETHERNET) -> bytes:
    """시험용 pcapng(마이크로초 해상도, 인터페이스 하나)."""
    def block(block_type: int, body: bytes) -> bytes:
        body += b"\0" * (-len(body) % 4)
        length = len(body) + 12
        return struct.pack("<II", block_type, length) + body + struct.pack("<I", length)

    data = block(PCAPNG_SECTION_HEADER, struct.pack("<IHHq", PCAPNG_BYTE_ORDER_MAGIC, 1, 0, -1))
    data += block(PCAPNG_INTERFACE_DESCRIPTION, struct.pack("<HHI", linktype, 0, 262144))
    for time_s, payload in packets:
        timestamp = int(round(time_s * 1e6))
        body = struct.pack("<IIIII", 0, timestamp >> 32, timestamp & 0xFFFFFFFF, len(payload), len(payload))
        data += block(PCAPNG_ENHANCED_PACKET, body + payload)
    return data
