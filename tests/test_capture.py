import io
import struct
import tempfile
import unittest
from pathlib import Path

from aesa_monitor.core.capture import (
    CaptureDecoder, CapturedFrame, CapturedPeerLink, CaptureEndpoints, TcpStream, parse_tcp_segment,
)
from aesa_monitor.core.events import PEER_CONSOLE, PEER_SIMULATOR, SOURCE_CAPTURE, ConnectionChanged, PeerLinkChanged
from aesa_monitor.core.protocol import (
    Beam, Detection, MonitorStatus, OperatorControl, Plot, RadarAttitude, SimulatorAttitude, Track, TrackList,
    encode,
)
from aesa_monitor.core.state import MonitorState
from aesa_monitor.io.capture import read_capture_file
from aesa_monitor.io.pcap import (
    LINKTYPE_ETHERNET, LINKTYPE_LINUX_SLL, LINKTYPE_LINUX_SLL2, LINKTYPE_NULL, pcap_header, pcap_record,
    pcapng_bytes, read_packets,
)
from aesa_monitor.io.replay import ReplayPlayer

PROCESSOR = ("192.168.10.20", 3200)
SIMULATOR = ("192.168.10.100", 3300)
CONSOLE = ("192.168.10.10", 3100)
MONITOR = ("192.168.10.30", 3400)
SYN, FIN, ACK, PSH, RST = 0x02, 0x01, 0x10, 0x08, 0x04


def ipv4_tcp(source, destination, sequence, flags, payload=b""):
    tcp = struct.pack(">HHIIBBHHH", source[1], destination[1], sequence, 0, 5 << 4, flags, 65535, 0, 0) + payload
    total = 20 + len(tcp)
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, total, 1, 0x4000, 64, 6, 0,
                     bytes(int(part) for part in source[0].split(".")),
                     bytes(int(part) for part in destination[0].split(".")))
    return ip + tcp


def ethernet(source, destination, sequence, flags, payload=b"", vlan=False):
    tag = struct.pack(">HH", 0x8100, 5) if vlan else b""
    return b"\x00" * 12 + tag + struct.pack(">H", 0x0800) + ipv4_tcp(source, destination, sequence, flags, payload)


def beam(beam_id, time_ms, beam_type=1):
    return Beam(beam_type, time_ms, beam_id, 1, 10.0, 5.0, 6.0, 6.0)


def detection(beam_id, time_ms, beam_type=1):
    return Detection(beam_type, time_ms, beam_id, [Plot(12.5, 10.0, 5.0, -100.0, 80.0)])


class Conversation:
    """두 끝점 사이 TCP 연결을 흉내 내서 이더넷 프레임을 만든다."""

    def __init__(self, client, server, start=1000, server_start=5000):
        self.client, self.server = client, server
        self.sequence = {client: start, server: server_start}

    def open(self):
        frames = [ethernet(self.client, self.server, self.sequence[self.client], SYN),
                  ethernet(self.server, self.client, self.sequence[self.server], SYN | ACK)]
        self.sequence[self.client] += 1
        self.sequence[self.server] += 1
        return frames

    def send(self, sender, payload):
        receiver = self.server if sender == self.client else self.client
        frame = ethernet(sender, receiver, self.sequence[sender], PSH | ACK, payload)
        self.sequence[sender] += len(payload)
        return frame

    def close(self, sender):
        receiver = self.server if sender == self.client else self.client
        return ethernet(sender, receiver, self.sequence[sender], FIN | ACK)


def pcap_file(frames, start=1000.0, step=0.01):
    data = pcap_header(LINKTYPE_ETHERNET)
    for index, frame in enumerate(frames):
        data += pcap_record(start + index * step, frame)
    return data


class PcapTest(unittest.TestCase):
    def test_reads_pcap_and_pcapng(self):
        frames = [b"abc", b"defgh"]
        packets = list(read_packets(io.BytesIO(pcap_file(frames))))
        self.assertEqual([packet.data for packet in packets], frames)
        self.assertAlmostEqual(packets[1].time_s, 1000.01, places=5)
        self.assertEqual(packets[0].linktype, LINKTYPE_ETHERNET)

        packets = list(read_packets(io.BytesIO(pcapng_bytes([(5.5, b"abc"), (6.25, b"defgh")]))))
        self.assertEqual([packet.data for packet in packets], frames)
        self.assertAlmostEqual(packets[1].time_s, 6.25)

    def test_truncated_file_stops_at_last_complete_packet(self):
        data = pcap_file([b"abc", b"defgh"])
        packets = list(read_packets(io.BytesIO(data[:-2])))
        self.assertEqual([packet.data for packet in packets], [b"abc"])
        self.assertEqual(list(read_packets(io.BytesIO(b""))), [])

    def test_on_start_is_called_before_any_packet(self):
        started = []
        self.assertEqual(list(read_packets(io.BytesIO(pcap_header()), on_start=lambda: started.append(1))), [])
        self.assertEqual(started, [1])


class SegmentTest(unittest.TestCase):
    def test_parses_link_layers(self):
        ip = ipv4_tcp(SIMULATOR, PROCESSOR, 7, PSH, b"xy")
        cases = {
            LINKTYPE_ETHERNET: ethernet(SIMULATOR, PROCESSOR, 7, PSH, b"xy", vlan=True),
            LINKTYPE_LINUX_SLL: b"\x00" * 14 + struct.pack(">H", 0x0800) + ip,
            LINKTYPE_LINUX_SLL2: struct.pack(">H", 0x0800) + b"\x00" * 18 + ip,
            LINKTYPE_NULL: struct.pack("<I", 2) + ip,
        }
        for linktype, data in cases.items():
            segment = parse_tcp_segment(linktype, data + b"\x00\x00")  # 이더넷 패딩은 IP 길이로 잘라낸다
            self.assertIsNotNone(segment, linktype)
            self.assertEqual((segment.source, segment.destination, segment.sequence, segment.payload),
                             (SIMULATOR, PROCESSOR, 7, b"xy"))

    def test_ignores_non_tcp(self):
        udp = bytearray(ipv4_tcp(SIMULATOR, PROCESSOR, 7, PSH, b"xy"))
        udp[9] = 17
        self.assertIsNone(parse_tcp_segment(LINKTYPE_ETHERNET, b"\x00" * 12 + b"\x08\x00" + bytes(udp)))
        self.assertIsNone(parse_tcp_segment(LINKTYPE_ETHERNET, b"\x00" * 12 + b"\x86\xdd" + b"\x60" + b"\x00" * 60))


class TcpStreamTest(unittest.TestCase):
    def setUp(self):
        self.data = b"".join(encode(beam(index, index * 10)) for index in range(6))
        self.size = len(encode(beam(0, 0)))

    def test_reassembles_split_retransmitted_and_reordered_segments(self):
        stream = TcpStream()
        stream.restart(99)
        data = self.data
        frames = []
        frames += stream.feed(100, data[:30])
        frames += stream.feed(100, data[:30])            # 재전송
        frames += stream.feed(100 + 90, data[90:])       # 순서 바뀜: 뒤쪽 먼저
        frames += stream.feed(100 + 20, data[20:90])     # 앞 일부와 겹침
        self.assertEqual(b"".join(frames), data)
        self.assertEqual(stream.gaps, 0)

    def test_starts_in_the_middle_of_a_message(self):
        stream = TcpStream()
        frames = stream.feed(5000, self.data[25:])  # SYN 없이 메시지 중간부터
        self.assertEqual(len(frames), 5)
        self.assertEqual(b"".join(frames), self.data[self.size:])
        self.assertGreater(stream.framer.skipped_bytes, 0)

    def test_skips_a_lost_segment(self):
        stream = TcpStream()
        stream.restart(0)
        frames = stream.feed(1, self.data[:self.size + 10])
        # 다음 구간을 놓치고 그 뒤만 계속 온다. 보관 한도를 넘기면 건너뛰고 이어서 받는다.
        later = b"".join(encode(beam(index, index * 10)) for index in range(100, 6000))
        start = 1 + len(self.data)
        for offset in range(0, len(later), 1000):
            frames += stream.feed(start + offset, later[offset:offset + 1000])
        self.assertEqual(stream.gaps, 1)
        self.assertEqual(frames[0], self.data[:self.size])
        self.assertGreater(len(frames), 5000)

    def test_handles_sequence_wraparound(self):
        stream = TcpStream()
        stream.restart(0xFFFFFFF0)
        frames = stream.feed(0xFFFFFFF1, self.data[:20])
        frames += stream.feed((0xFFFFFFF1 + 20) & 0xFFFFFFFF, self.data[20:])
        self.assertEqual(b"".join(frames), self.data)


class DecoderTest(unittest.TestCase):
    def test_classifies_peers_by_message_and_reports_links(self):
        simulator = Conversation(SIMULATOR, PROCESSOR)
        console = Conversation(CONSOLE, PROCESSOR)
        frames = simulator.open() + console.open() + [
            console.send(CONSOLE, encode(OperatorControl(1))),
            simulator.send(PROCESSOR, encode(beam(1, 10))),
            simulator.send(SIMULATOR, encode(detection(1, 10))),
            console.send(PROCESSOR, encode(TrackList([]))),
            simulator.close(SIMULATOR),
        ]
        decoder = CaptureDecoder()
        items = []
        for index, frame in enumerate(frames):
            items += decoder.feed(index * 0.01, LINKTYPE_ETHERNET, frame)
        links = [(item.peer, item.connected) for item in items if isinstance(item, CapturedPeerLink)]
        self.assertEqual(links, [(PEER_CONSOLE, True), (PEER_SIMULATOR, True), (PEER_SIMULATOR, False)])
        captured = [item for item in items if isinstance(item, CapturedFrame)]
        self.assertEqual([item.peer for item in captured], [PEER_CONSOLE, PEER_SIMULATOR, PEER_SIMULATOR, PEER_CONSOLE])
        self.assertEqual(captured[1].source, PROCESSOR)

    def test_classifies_by_address_on_syn(self):
        decoder = CaptureDecoder(CaptureEndpoints(PROCESSOR, CONSOLE, SIMULATOR, MONITOR))
        items = decoder.feed(0.0, LINKTYPE_ETHERNET, Conversation(SIMULATOR, PROCESSOR).open()[0])
        self.assertEqual(items, [CapturedPeerLink(0.0, PEER_SIMULATOR, True)])

    def test_excludes_monitor_copy_connection(self):
        copy = Conversation(MONITOR, PROCESSOR)
        frames = copy.open() + [copy.send(PROCESSOR, encode(beam(1, 10))),
                                copy.send(PROCESSOR, encode(MonitorStatus(10, 1.0, False, True, True)))]
        by_address = CaptureDecoder(CaptureEndpoints(monitor=MONITOR))
        self.assertEqual(sum((by_address.feed(0.0, LINKTYPE_ETHERNET, frame) for frame in frames), []), [])
        by_content = CaptureDecoder()
        for frame in frames:
            by_content.feed(0.0, LINKTYPE_ETHERNET, frame)
        self.assertEqual(len(by_content.monitor_connections), 1)


class CaptureFileTest(unittest.TestCase):
    def build(self):
        simulator = Conversation(SIMULATOR, PROCESSOR)
        console = Conversation(CONSOLE, PROCESSOR)
        copy = Conversation(MONITOR, PROCESSOR)
        frames = simulator.open() + console.open() + copy.open()
        frames.append(simulator.send(SIMULATOR, encode(SimulatorAttitude(37.5, 127.0, 0.05, 0.0, 0.0, 13.0))))
        frames.append(console.send(PROCESSOR, encode(RadarAttitude(37.5, 127.0, 0.05, 0.0, 0.0, 13.0))))
        for index in range(20):
            frames.append(simulator.send(PROCESSOR, encode(beam(index, index * 10))))
            frames.append(copy.send(PROCESSOR, encode(beam(index, index * 10))))  # 복사본(빼야 함)
            frames.append(simulator.send(SIMULATOR, encode(detection(index, index * 10))))
            if index % 10 == 0:
                frames.append(copy.send(PROCESSOR, encode(MonitorStatus(index * 10, 1.0, False, True, True))))
        frames.append(console.send(PROCESSOR, encode(TrackList([Track(1, 2, *([1.0] * 11), 100)]))))
        return pcap_file(frames)

    def test_reads_capture_file_as_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.pcap"
            path.write_bytes(self.build())
            recording = read_capture_file(path)
        self.assertEqual(recording.meta["source"], SOURCE_CAPTURE)
        self.assertEqual(recording.meta["excluded_monitor_connections"], 1)
        events, errors = recording.events()
        self.assertEqual(errors, 0)
        kinds = [type(event).__name__ for _, event in events]
        self.assertEqual(kinds.count("Beam"), 20)        # 복사본 빔은 빠짐
        self.assertEqual(kinds.count("Detection"), 20)
        self.assertEqual(kinds.count("MonitorStatus"), 0)
        self.assertIsInstance(events[0][1], ConnectionChanged)
        self.assertEqual(events[0][1].source, SOURCE_CAPTURE)
        self.assertIn(PeerLinkChanged(PEER_SIMULATOR, True, ""), [event for _, event in events])

        player = ReplayPlayer(recording)
        player.seek(player.duration_s)
        state = player.state
        self.assertEqual(state.source, SOURCE_CAPTURE)
        self.assertTrue(state.peer_connected(PEER_SIMULATOR))
        self.assertTrue(state.peer_connected(PEER_CONSOLE))
        self.assertEqual(len(state.beams), 20)
        self.assertEqual(state.simulator_attitude.yaw_deg, 13.0)


class StateTest(unittest.TestCase):
    def test_peer_status_prefers_monitor_status(self):
        now = [0.0]
        state = MonitorState(clock=lambda: now[0])
        state.apply(ConnectionChanged(True, source=SOURCE_CAPTURE))
        self.assertIsNone(state.peer_connected(PEER_CONSOLE))
        self.assertIsNone(state.paused())
        state.apply(PeerLinkChanged(PEER_CONSOLE, True))
        state.apply(OperatorControl(0))
        self.assertTrue(state.peer_connected(PEER_CONSOLE))
        self.assertTrue(state.paused())
        state.apply(MonitorStatus(0, 1.0, False, False, True))
        self.assertFalse(state.peer_connected(PEER_CONSOLE))   # 상태 메시지가 있으면 그것을 쓴다
        self.assertFalse(state.paused())
        now[0] = 5.0                                           # 상태 메시지가 오래되면 캡처 값으로 돌아간다
        self.assertTrue(state.peer_connected(PEER_CONSOLE))
        state.apply(ConnectionChanged(False, "stopped", SOURCE_CAPTURE))
        self.assertIsNone(state.peer_connected(PEER_CONSOLE))
        self.assertIn("캡처 중지", state.event_log[-1].text)


if __name__ == "__main__":
    unittest.main()
