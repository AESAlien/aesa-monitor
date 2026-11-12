"""가짜 데이터 처리부. 모니터 화면 시험과 시연용이다.

처리부와 모의기를 아주 단순하게 흉내 내서 모니터가 받는 메시지(0xAB01, 0xBA01 복사본, 0xAC01,
0xAC02, 0xAD01)를 보내고, 처리부와 같은 형식의 로그 파일(adp_*.log, adp_detection_*.csv)을 남긴다.
추적 알고리즘은 실제 처리부(칼만 필터, NDS)가 아니라 근사다. 값의 정확도를 볼 용도가 아니다.

    python3 tools/fake_processor.py --listen 127.0.0.1:43200 --log-dir /tmp/fake-logs
    python3 -m aesa_monitor --server 127.0.0.1:43200 --local 127.0.0.1:43203

--peers를 주면 가짜 모의기·운용통제기도 이 프로그램 안에서 처리부에 TCP로 접속해 실제와 같은 방향으로
메시지를 주고받는다(빔 명령 처리부→모의기, 탐지·모의기 자세 모의기→처리부, 트랙·레이더 자세
처리부→운용통제기, 운용 명령 운용통제기→처리부). 통신 캡처 시험용이다. 캡처에는 권한이 필요하다.
    python3 tools/fake_processor.py --peers
    sudo python3 -m aesa_monitor --server 127.0.0.1:43200 --local 127.0.0.1:43203 --source capture \
        --capture-command "tcpdump -i lo --immediate-mode -U -s 0 -w - tcp port {port}"
"""

from __future__ import annotations

import argparse
import datetime
import math
import random
import select
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aesa_monitor.core.protocol import (  # noqa: E402
    Beam, BeamType, Detection, MonitorStatus, OperatorControl, Plot, RadarAttitude, SimulatorAttitude, Track,
    TrackList, TrackStatus, encode,
)

TICK_MS = 10
YAW_DEG = 13.02429
SEARCH_WIDTH = 6.0
REQUEST_WIDTH = 3.0
CONFIRM_DELAY_MS = 100
TRACK_INTERVAL_MS = 1000
MISS_LIMIT = 3
NOISE_M = 7.0
COVERAGE_AZ = (-60.0, 60.0)
COVERAGE_EL = (-10.0, 60.0)


def wrap180(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def to_angles(position: Tuple[float, float, float]) -> Tuple[float, float, float]:
    east, north, up = position
    slant = math.sqrt(east * east + north * north + up * up)
    azimuth = math.degrees(math.atan2(east, north)) % 360.0
    elevation = math.degrees(math.atan2(up, math.hypot(east, north)))
    return slant, azimuth, elevation


def from_angles(slant_m: float, azimuth: float, elevation: float) -> Tuple[float, float, float]:
    a, e = math.radians(azimuth), math.radians(elevation)
    return (slant_m * math.cos(e) * math.sin(a), slant_m * math.cos(e) * math.cos(a), slant_m * math.sin(e))


@dataclass
class Target:
    position: Tuple[float, float, float]
    velocity: Tuple[float, float, float]
    turn_rate_deg: float = 0.0
    start_ms: int = 0

    def at(self, time_ms: int) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
        if time_ms < self.start_ms:
            return None
        t = (time_ms - self.start_ms) / 1000.0
        if self.turn_rate_deg == 0.0:
            pos = tuple(p + v * t for p, v in zip(self.position, self.velocity))
            return pos, self.velocity
        omega = math.radians(self.turn_rate_deg)
        vx, vy, vz = self.velocity
        x = self.position[0] + (vx * math.sin(omega * t) - vy * (1 - math.cos(omega * t))) / omega
        y = self.position[1] + (vy * math.sin(omega * t) + vx * (1 - math.cos(omega * t))) / omega
        vxt = vx * math.cos(omega * t) - vy * math.sin(omega * t)
        vyt = vy * math.cos(omega * t) + vx * math.sin(omega * t)
        return (x, y, self.position[2] + vz * t), (vxt, vyt, vz)


@dataclass
class FakeTrack:
    track_id: int
    position: Tuple[float, float, float]
    velocity: Tuple[float, float, float]
    updated_ms: int
    status: int = TrackStatus.INIT
    misses: int = 0
    rejected: bool = False


@dataclass(order=True)
class Request:
    time_ms: int
    beam_type: int = field(compare=False)
    request_id: int = field(compare=False)
    azimuth: float = field(compare=False)
    elevation: float = field(compare=False)


class FakeProcessor:
    def __init__(self, seed: int, log_dir: Optional[Path]) -> None:
        self.random = random.Random(seed)
        self.now_ms = 0
        self.search_index = 0
        self.command_count = 0
        self.next_id = 1
        self.requests: List[Request] = []
        self.tentative: Dict[int, Tuple[float, float, float, int]] = {}
        self.tracks: Dict[int, FakeTrack] = {}
        self.grid = self._make_grid()
        self.targets = [
            Target(from_angles(60000, 20.0, 1.8), (-250.0, 0.0, 0.0)),
            Target(from_angles(80000, 340.0, 3.5), (260.0, -150.0, 0.0), turn_rate_deg=1.0),
            Target(from_angles(30000, 52.0, 3.0), (900.0, -150.0, 0.0), start_ms=3000),
            Target(from_angles(70000, 5.0, 8.0), (-120.0, -200.0, 5.0), start_ms=15000),
        ]
        self.log = ProcessorLogs(log_dir) if log_dir else None

    def _make_grid(self) -> List[Tuple[float, float]]:
        grid = []
        for row in range(9):
            for column in range(21):
                azimuth_ant = -42.0 + 4.2 * column
                elevation = 3.0 + 4.4 * row
                grid.append(((azimuth_ant + YAW_DEG) % 360.0, elevation))
        return grid

    # 빔 선택

    def tick(self) -> List[object]:
        messages: List[object] = []
        self.requests.sort()
        if self.requests and self.requests[0].time_ms <= self.now_ms:
            request = self.requests.pop(0)
            beam = Beam(request.beam_type, self.now_ms, request.request_id, self._count(),
                        request.azimuth, request.elevation, REQUEST_WIDTH, REQUEST_WIDTH)
        else:
            azimuth, elevation = self.grid[self.search_index]
            beam = Beam(BeamType.SEARCH, self.now_ms, self.search_index, self._count(),
                        azimuth, elevation, SEARCH_WIDTH, SEARCH_WIDTH)
            self.search_index = (self.search_index + 1) % len(self.grid)
        messages.append(beam)
        detection = self._simulate(beam)
        messages.append(detection)
        if self.log:
            self.log.detection(detection)
        self._process(beam, detection)
        messages.append(self._track_list())
        return messages

    def _count(self) -> int:
        self.command_count += 1
        return self.command_count

    # 모의기

    def _simulate(self, beam: Beam) -> Detection:
        plots = []
        for target in self.targets:
            state = target.at(self.now_ms)
            if state is None:
                continue
            position, velocity = state
            slant, azimuth, elevation = to_angles(position)
            d_az = abs(wrap180(beam.azimuth_deg - azimuth))
            d_el = abs(beam.elevation_deg - elevation)
            if (2 * d_az / beam.azimuth_width_deg) ** 2 + (2 * d_el / beam.elevation_width_deg) ** 2 > 1:
                continue
            if beam.beam_type == BeamType.TRACKING and self.random.random() < 0.03:
                continue
            noisy = tuple(p + self.random.gauss(0.0, NOISE_M) for p in position)
            slant, azimuth, elevation = to_angles(noisy)
            doppler = sum(p * v for p, v in zip(noisy, velocity)) / slant
            plots.append(Plot(slant / 1000.0, azimuth, elevation, doppler, 80.0))
        return Detection(beam.beam_type, beam.timestamp_ms, beam.beam_id, plots)

    # 처리부 (근사)

    def _process(self, beam: Beam, detection: Detection) -> None:
        self._expire_tracks()
        if beam.beam_type == BeamType.SEARCH:
            for plot in detection.plots:
                if self._near_existing(plot):
                    continue
                request_id = self.next_id
                self.next_id += 1
                self.tentative[request_id] = (plot.slant_range_km * 1000, plot.azimuth_deg, plot.elevation_deg,
                                              self.now_ms)
                self._request(Request(self.now_ms + CONFIRM_DELAY_MS, BeamType.CONFIRMATION, request_id,
                                      plot.azimuth_deg, plot.elevation_deg))
        elif beam.beam_type == BeamType.CONFIRMATION:
            first = self.tentative.pop(beam.beam_id, None)
            if first is None or not detection.plots:
                return
            plot = self._nearest(detection.plots, beam)
            p1 = from_angles(first[0], first[1], first[2])
            p2 = from_angles(plot.slant_range_km * 1000, plot.azimuth_deg, plot.elevation_deg)
            dt = (self.now_ms - first[3]) / 1000.0
            velocity = tuple((b - a) / dt for a, b in zip(p1, p2))
            if math.sqrt(sum(v * v for v in velocity)) > 1500:
                return
            track = FakeTrack(beam.beam_id, p2, velocity, self.now_ms)
            self.tracks[track.track_id] = track
            self._request_tracking(track)
        elif beam.beam_type == BeamType.TRACKING:
            track = self.tracks.get(beam.beam_id)
            if track is None:
                return
            plot = self._nearest(detection.plots, beam) if detection.plots else None
            if plot is not None:
                measured = from_angles(plot.slant_range_km * 1000, plot.azimuth_deg, plot.elevation_deg)
                dt = (self.now_ms - track.updated_ms) / 1000.0
                predicted = tuple(p + v * dt for p, v in zip(track.position, track.velocity))
                new_velocity = tuple(0.7 * v + 0.3 * (m - p) / max(dt, 0.01)
                                     for v, m, p in zip(track.velocity, measured, track.position))
                track.position = tuple(0.4 * p + 0.6 * m for p, m in zip(predicted, measured))
                track.velocity = new_velocity
                track.updated_ms = self.now_ms
                track.status = TrackStatus.TRACKING
                track.misses = 0
            else:
                track.misses += 1
                if track.misses >= MISS_LIMIT:
                    del self.tracks[track.track_id]
                    return
            self._request_tracking(track)

    def _expire_tracks(self) -> None:
        # 결과가 오지 않는 트랙은 추적 주기 × 연속 미탐 한도가 지나면 지운다(bb8b643과 같은 방식).
        for track_id, track in list(self.tracks.items()):
            if self.now_ms - track.updated_ms > TRACK_INTERVAL_MS * (MISS_LIMIT + 1):
                del self.tracks[track_id]

    def _near_existing(self, plot: Plot) -> bool:
        for track in self.tracks.values():
            _, azimuth, elevation = to_angles(track.position)
            if abs(wrap180(azimuth - plot.azimuth_deg)) < 3.0 and abs(elevation - plot.elevation_deg) < 3.0:
                return True
        for _, azimuth, elevation, _ in self.tentative.values():
            if abs(wrap180(azimuth - plot.azimuth_deg)) < 3.0 and abs(elevation - plot.elevation_deg) < 3.0:
                return True
        return False

    @staticmethod
    def _nearest(plots: List[Plot], beam: Beam) -> Plot:
        return min(plots, key=lambda plot: math.hypot(wrap180(plot.azimuth_deg - beam.azimuth_deg),
                                                      plot.elevation_deg - beam.elevation_deg))

    def _request_tracking(self, track: FakeTrack) -> None:
        time_ms = self.now_ms + TRACK_INTERVAL_MS
        dt = (time_ms - track.updated_ms) / 1000.0
        predicted = tuple(p + v * dt for p, v in zip(track.position, track.velocity))
        _, azimuth, elevation = to_angles(predicted)
        self._request(Request(time_ms, BeamType.TRACKING, track.track_id, azimuth, elevation))

    def _request(self, request: Request) -> None:
        azimuth_ant = wrap180(request.azimuth - YAW_DEG)
        inside = (COVERAGE_AZ[0] <= azimuth_ant <= COVERAGE_AZ[1] and
                  COVERAGE_EL[0] <= request.elevation <= COVERAGE_EL[1])
        if not inside:
            if self.log:
                self.log.write("BeamEvent", "warning",
                               f"Beam request rejected: status=OUT_OF_COVERAGE, requestId={request.request_id}",
                               self.now_ms)
            return
        self.requests.append(request)

    def _track_list(self) -> object:
        from aesa_monitor.core.protocol import TrackList
        tracks = []
        for track in sorted(self.tracks.values(), key=lambda item: item.track_id):
            slant, azimuth, elevation = to_angles(track.position)
            vx, vy, vz = track.velocity
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            heading = math.degrees(math.atan2(vx, vy)) % 360.0
            doppler = sum(p * v for p, v in zip(track.position, track.velocity)) / slant
            tracks.append(Track(track.track_id, track.status, slant / 1000.0,
                                math.hypot(track.position[0], track.position[1]) / 1000.0, azimuth, elevation,
                                doppler, 37.5, 127.0, track.position[2] / 1000.0, speed, heading,
                                math.degrees(math.asin(vz / speed)) if speed > 0 else 0.0, track.updated_ms))
        return TrackList(tracks)


class ProcessorLogs:
    """처리부와 같은 이름과 형식으로 로그를 쓴다."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.started_wall = datetime.datetime.now(datetime.timezone.utc)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + f"{datetime.datetime.now().microsecond // 1000:03d}"
        self.message = open(directory / f"adp_{stamp}.log", "w", encoding="utf-8")
        self.csv = open(directory / f"adp_detection_{stamp}.csv", "w", encoding="utf-8")
        self.csv.write("beamType,requestId,transmitTime_ms,plotCount,"
                       "plotIndex,slantRange_m,azimuth_deg,elevation_deg,dopplerVelocity_mps,power_db\n")
        self.paths = (Path(self.message.name), Path(self.csv.name))
        for logger, text in (("main", "Application starting"), ("main", "Communication server opened"),
                             ("BeamWorker", "Worker started"), ("TargetWorker", "Worker started"),
                             ("CommReceive", "Worker started"), ("CommSendSimulator", "Worker started"),
                             ("CommSendConsole", "Worker started"), ("CommSendMonitor", "Worker started"),
                             ("main", "Six worker threads started; beam tick = 10 ms")):
            self.write(logger, "info", text, 0)

    def write(self, logger: str, level: str, text: str, processor_ms: int) -> None:
        wall = self.started_wall + datetime.timedelta(milliseconds=processor_ms)
        stamp = wall.strftime("%Y-%m-%dT%H:%M:%S.") + f"{wall.microsecond // 1000:03d}Z"
        self.message.write(f"{stamp} [{logger}] [{level}] [thread=4242] {text}\n")
        self.message.flush()

    def detection(self, detection: Detection) -> None:
        name = {1: "SEARCH", 2: "CONFIRMATION", 3: "TRACKING"}[detection.beam_type]
        prefix = f"{name},{detection.beam_id},{detection.timestamp_ms},{len(detection.plots)}"
        if not detection.plots:
            self.csv.write(prefix + ",,,,,,\n")
        for index, plot in enumerate(detection.plots):
            self.csv.write(f"{prefix},{index},{plot.slant_range_km * 1000:.6f},{plot.azimuth_deg:.6f},"
                           f"{plot.elevation_deg:.6f},{plot.doppler_mps:.6f},{plot.power_db:.6f}\n")

    def close(self, processor_ms: int) -> None:
        self.write("main", "info", "Shutdown requested", processor_ms)
        self.message.close()
        self.csv.close()


class FakePeers:
    """가짜 모의기·운용통제기. 처리부 서버에 접속해 실제 방향으로 메시지를 주고받는다."""

    def __init__(self, server: Tuple[str, int], simulator: Tuple[str, int], console: Tuple[str, int]) -> None:
        self.simulator_address = simulator
        self.console_address = console
        self.simulator = self._connect(server, simulator)
        self.console = self._connect(server, console)
        # 처리부(서버) 쪽 소켓. accept에서 원격 주소로 구분해 채운다.
        self.to_simulator: Optional[socket.socket] = None
        self.to_console: Optional[socket.socket] = None

    @staticmethod
    def _connect(server: Tuple[str, int], local: Tuple[str, int]) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(local)
        sock.connect(server)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock

    def accept(self, sock: socket.socket, address: Tuple[str, int]) -> bool:
        """처리부가 받은 연결이 가짜 상대의 것이면 맡고 True를 돌려준다."""
        if address == self.simulator_address:
            self.to_simulator = sock
        elif address == self.console_address:
            self.to_console = sock
        else:
            return False
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return True

    @property
    def ready(self) -> bool:
        return self.to_simulator is not None and self.to_console is not None

    def start(self, attitude: RadarAttitude) -> None:
        self.simulator.sendall(encode(SimulatorAttitude(attitude.latitude_deg, attitude.longitude_deg,
                                                        attitude.altitude_km, attitude.roll_deg,
                                                        attitude.pitch_deg, attitude.yaw_deg)))
        self.console.sendall(encode(OperatorControl(1)))
        assert self.to_console is not None
        self.to_console.sendall(encode(attitude))

    def send(self, messages: List[object]) -> None:
        assert self.to_simulator is not None and self.to_console is not None
        for message in messages:
            if isinstance(message, Beam):
                self.to_simulator.sendall(encode(message))
            elif isinstance(message, Detection):
                self.simulator.sendall(encode(message))
            elif isinstance(message, (TrackList, RadarAttitude)):
                self.to_console.sendall(encode(message))
        self.drain()

    def drain(self) -> None:
        """받은 쪽 버퍼가 차지 않도록 읽고 버린다."""
        sockets = [sock for sock in (self.simulator, self.console, self.to_simulator, self.to_console) if sock]
        while True:
            readable, _, _ = select.select(sockets, [], [], 0)
            if not readable:
                return
            for sock in readable:
                if not sock.recv(65536):
                    sockets.remove(sock)

    def close(self) -> None:
        for sock in (self.simulator, self.console, self.to_simulator, self.to_console):
            if sock is not None:
                sock.close()


def parse_endpoint(text: str) -> Tuple[str, int]:
    address, _, port = text.rpartition(":")
    return address, int(port)


def main() -> int:
    parser = argparse.ArgumentParser(description="모니터 시험용 가짜 처리부")
    parser.add_argument("--listen", default="127.0.0.1:43200")
    parser.add_argument("--log-dir", type=Path, help="처리부 형식 로그를 쓸 폴더")
    parser.add_argument("--duration", type=float, help="이 초(처리부 시각)만큼 돌고 끝낸다")
    parser.add_argument("--speed", type=float, default=1.0, help="처리부 시각 배율 (기본 1)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--fast", action="store_true", help="기다리지 않고 최대 속도로 진행 (로그 생성용)")
    parser.add_argument("--peers", action="store_true",
                        help="가짜 모의기·운용통제기를 접속시켜 실제 방향으로 메시지를 보낸다 (통신 캡처 시험)")
    parser.add_argument("--simulator-bind", default="127.0.0.1:43300", help="--peers의 가짜 모의기 주소")
    parser.add_argument("--console-bind", default="127.0.0.1:43100", help="--peers의 가짜 운용통제기 주소")
    options = parser.parse_args()

    processor = FakeProcessor(options.seed, options.log_dir)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(parse_endpoint(options.listen))
    server.listen(4)
    peers: Optional[FakePeers] = None
    if options.peers:
        peers = FakePeers(parse_endpoint(options.listen), parse_endpoint(options.simulator_bind),
                          parse_endpoint(options.console_bind))
        while not peers.ready:
            sock, address = server.accept()
            if not peers.accept(sock, address):
                sock.close()
    server.setblocking(False)
    client: Optional[socket.socket] = None
    started = time.monotonic()
    attitude = RadarAttitude(37.5, 127.0, 0.05, 0.0, 0.0, YAW_DEG)
    if processor.log:
        processor.log.write("BeamEvent", "info", "Operation state changed: ON", 0)
        processor.log.write("BeamEvent", "info", f"Attitude applied: roll=0, pitch=0, yaw={YAW_DEG}", 5)
    if peers is not None:
        peers.start(attitude)
        print(f"fake peers connected: simulator {options.simulator_bind}, console {options.console_bind}",
              flush=True)
    print(f"fake processor listening on {options.listen}", flush=True)
    try:
        while options.duration is None or processor.now_ms < options.duration * 1000:
            if client is None:
                readable, _, _ = select.select([server], [], [], 0)
                if readable:
                    client, address = server.accept()
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    print(f"monitor connected from {address}", flush=True)
                    client.sendall(encode(attitude))
            messages = processor.tick()
            if peers is not None:
                peers.send(messages)
            if processor.now_ms % 100 == 0:
                messages.append(MonitorStatus(processor.now_ms, options.speed, False, True, True))
            if processor.now_ms == 40000 and processor.log:
                processor.log.write("BeamEvent", "warning",
                                    f"Attitude ignored (already set): roll=0, pitch=0, yaw={YAW_DEG}", 40000)
            if client is not None:
                try:
                    client.sendall(b"".join(encode(message) for message in messages))
                except OSError:
                    client.close()
                    client = None
                    print("monitor disconnected", flush=True)
            processor.now_ms += TICK_MS
            if not options.fast:
                target = started + processor.now_ms / 1000.0 / options.speed
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
    except KeyboardInterrupt:
        pass
    finally:
        if processor.log:
            processor.log.close(processor.now_ms)
            print("logs: " + ", ".join(str(path) for path in processor.log.paths), flush=True)
        if client is not None:
            client.close()
        if peers is not None:
            peers.close()
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
