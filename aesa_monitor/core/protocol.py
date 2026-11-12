"""처리부·모의기·운용통제기가 주고받는 메시지와 모니터 상태 메시지의 바이트 배치와 디코딩.

C++의 src/comm/src/protocol/ST_*.hpp와 같은 배치를 사용한다.
모든 구조체는 1바이트 정렬(#pragma pack(1))이며 리틀 엔디언이다.
복사본 수신은 처리부가 보내는 메시지만, 통신 캡처는 세 장비 사이의 모든 메시지를 받는다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Union


class MessageId(IntEnum):
    OPERATOR_CONTROL = 0xCA01
    TRACK_INFORMATION = 0xAC01
    RADAR_ATTITUDE_TO_CONSOLE = 0xAC02
    TRACK_DELETION = 0xAC03
    BEAM_TRANSMIT = 0xAB01
    PLOT_INFORMATION = 0xBA01
    RADAR_ATTITUDE_FROM_SIMULATOR = 0xBA02
    MONITOR_STATUS = 0xAD01


class BeamType(IntEnum):
    SEARCH = 1
    CONFIRMATION = 2
    TRACKING = 3


class TrackStatus(IntEnum):
    INIT = 1
    TRACKING = 2


# ST_MsgHeader
HEADER = struct.Struct("<HBBIIIBBH")
# ST_BeamTx
BEAM_TX = struct.Struct("<B3xIIIffff28x")
# ST_TrkHeader
TRACK_HEADER = struct.Struct("<H2x")
# ST_Trk
TRACK = struct.Struct("<HBB11fIII28x")
# ST_PlotHeader
PLOT_HEADER = struct.Struct("<H2x")
# ST_Plot
PLOT = struct.Struct("<I5f")
# ST_RadarAttMsg의 헤더 이후 부분
RADAR_ATTITUDE = struct.Struct("<6d")
# ST_MonitorStatusMsg의 헤더 이후 부분
MONITOR_STATUS = struct.Struct("<IfBBBx")
# ST_OperatorMsg의 헤더 이후 부분
OPERATOR = struct.Struct("<B3x")
# ST_DelTrkMsg의 트랙 ID 하나
DELETED_TRACK_ID = struct.Struct("<H")

BEAM_TX_MESSAGE_SIZE = HEADER.size + BEAM_TX.size
TRACK_MESSAGE_MAXIMUM_TRACKS = 100
TRACK_MESSAGE_MAXIMUM_SIZE = HEADER.size + TRACK_HEADER.size + TRACK.size * TRACK_MESSAGE_MAXIMUM_TRACKS
PLOT_MESSAGE_MAXIMUM_PLOTS = 100
PLOT_DATA_OFFSET = HEADER.size + BEAM_TX.size + PLOT_HEADER.size
PLOT_MESSAGE_MAXIMUM_SIZE = PLOT_DATA_OFFSET + PLOT.size * PLOT_MESSAGE_MAXIMUM_PLOTS
RADAR_ATTITUDE_MESSAGE_SIZE = HEADER.size + RADAR_ATTITUDE.size
MONITOR_STATUS_MESSAGE_SIZE = HEADER.size + MONITOR_STATUS.size
OPERATOR_MESSAGE_SIZE = HEADER.size + OPERATOR.size
TRACK_DELETION_MAXIMUM_TRACKS = 100
TRACK_DELETION_DATA_OFFSET = HEADER.size + TRACK_HEADER.size
TRACK_DELETION_MAXIMUM_SIZE = TRACK_DELETION_DATA_OFFSET + DELETED_TRACK_ID.size * TRACK_DELETION_MAXIMUM_TRACKS
MAXIMUM_FRAME_SIZE = max(TRACK_MESSAGE_MAXIMUM_SIZE, PLOT_MESSAGE_MAXIMUM_SIZE)


class ProtocolError(ValueError):
    """메시지 형식이 프로토콜과 맞지 않는다."""


@dataclass(frozen=True)
class Header:
    message_id: int
    version: int
    block_size: int
    time_sec: int
    time_nsec: int
    source_id: int
    dest_id: int


@dataclass(frozen=True)
class Beam:
    beam_type: int
    timestamp_ms: int
    beam_id: int
    command_count: int
    azimuth_deg: float
    elevation_deg: float
    azimuth_width_deg: float
    elevation_width_deg: float


@dataclass(frozen=True)
class Track:
    track_id: int
    status: int
    slant_range_km: float
    ground_range_km: float
    azimuth_deg: float
    elevation_deg: float
    doppler_mps: float
    latitude_deg: float
    longitude_deg: float
    altitude_km: float
    speed_mps: float
    heading_deg: float
    flight_path_angle_deg: float
    timestamp_ms: int


@dataclass(frozen=True)
class TrackList:
    tracks: List[Track]


@dataclass(frozen=True)
class Plot:
    slant_range_km: float
    azimuth_deg: float
    elevation_deg: float
    doppler_mps: float
    power_db: float


@dataclass(frozen=True)
class Detection:
    """모의기가 빔 하나에 대해 처리부에 보낸 탐지 결과(0xBA01)의 복사본.

    처리부는 빔 종류, 송신 시각, ID만 채운다. 방향은 같은 시각과 ID의 빔 송신 명령에 있다.
    """

    beam_type: int
    timestamp_ms: int
    beam_id: int
    plots: List[Plot]


@dataclass(frozen=True)
class RadarAttitude:
    latitude_deg: float
    longitude_deg: float
    altitude_km: float
    roll_deg: float
    pitch_deg: float
    yaw_deg: float


@dataclass(frozen=True)
class MonitorStatus:
    processor_time_ms: int
    time_scale: float
    paused: bool
    operator_console_connected: bool
    scenario_simulator_connected: bool


@dataclass(frozen=True)
class OperatorControl:
    """운용통제기가 처리부에 보낸 운용 명령(0xCA01). 처리부는 0을 정지(OFF), 1을 운용(ON)으로 해석한다."""

    status: int

    @property
    def running(self) -> bool:
        return self.status == 1


@dataclass(frozen=True)
class TrackDeletion:
    """트랙 삭제(0xAC03)."""

    track_ids: List[int]


@dataclass(frozen=True)
class SimulatorAttitude:
    """모의기가 처리부에 보낸 레이더 위치·자세(0xBA02). 배치는 처리부가 운용통제기에 보내는 0xAC02와 같다."""

    latitude_deg: float
    longitude_deg: float
    altitude_km: float
    roll_deg: float
    pitch_deg: float
    yaw_deg: float


Message = Union[Beam, TrackList, Detection, RadarAttitude, MonitorStatus,
                OperatorControl, TrackDeletion, SimulatorAttitude]


def decode_header(data: bytes) -> Header:
    if len(data) < HEADER.size:
        raise ProtocolError("header is too short")
    message_id, version, _, block_size, time_sec, time_nsec, source_id, dest_id, _ = (
        HEADER.unpack_from(data)
    )
    return Header(message_id, version, block_size, time_sec, time_nsec, source_id, dest_id)


def is_known_message_id(message_id: int) -> bool:
    return message_id in MessageId._value2member_map_


def is_valid_frame_size(message_id: int, block_size: int) -> bool:
    """메시지 종류에 맞는 프레임 크기인지. 캡처에서 메시지 경계를 다시 찾을 때 쓴다."""
    if message_id == MessageId.BEAM_TRANSMIT:
        return block_size == BEAM_TX_MESSAGE_SIZE
    if message_id in (MessageId.RADAR_ATTITUDE_TO_CONSOLE, MessageId.RADAR_ATTITUDE_FROM_SIMULATOR):
        return block_size == RADAR_ATTITUDE_MESSAGE_SIZE
    if message_id == MessageId.MONITOR_STATUS:
        return block_size == MONITOR_STATUS_MESSAGE_SIZE
    if message_id == MessageId.OPERATOR_CONTROL:
        return block_size == OPERATOR_MESSAGE_SIZE
    if message_id == MessageId.TRACK_INFORMATION:
        rest = block_size - HEADER.size - TRACK_HEADER.size
        return 0 <= rest <= TRACK.size * TRACK_MESSAGE_MAXIMUM_TRACKS and rest % TRACK.size == 0
    if message_id == MessageId.PLOT_INFORMATION:
        rest = block_size - PLOT_DATA_OFFSET
        return 0 <= rest <= PLOT.size * PLOT_MESSAGE_MAXIMUM_PLOTS and rest % PLOT.size == 0
    if message_id == MessageId.TRACK_DELETION:
        rest = block_size - TRACK_DELETION_DATA_OFFSET
        return 0 <= rest <= TRACK_DELETION_MAXIMUM_SIZE - TRACK_DELETION_DATA_OFFSET
    return False


def decode(frame: bytes) -> Message:
    """완성된 프레임 하나를 메시지로 디코딩한다."""
    header = decode_header(frame)
    if header.block_size != len(frame):
        raise ProtocolError("block size does not match frame size")

    if header.message_id == MessageId.BEAM_TRANSMIT:
        return _decode_beam(frame)
    if header.message_id == MessageId.TRACK_INFORMATION:
        return _decode_tracks(frame)
    if header.message_id == MessageId.PLOT_INFORMATION:
        return _decode_detection(frame)
    if header.message_id == MessageId.RADAR_ATTITUDE_TO_CONSOLE:
        return _decode_attitude(frame)
    if header.message_id == MessageId.MONITOR_STATUS:
        return _decode_status(frame)
    if header.message_id == MessageId.OPERATOR_CONTROL:
        return _decode_operator(frame)
    if header.message_id == MessageId.RADAR_ATTITUDE_FROM_SIMULATOR:
        _require_size(frame, RADAR_ATTITUDE_MESSAGE_SIZE)
        return SimulatorAttitude(*RADAR_ATTITUDE.unpack_from(frame, HEADER.size))
    if header.message_id == MessageId.TRACK_DELETION:
        return _decode_track_deletion(frame)
    raise ProtocolError(f"unsupported message id 0x{header.message_id:04X}")


def _require_size(frame: bytes, size: int) -> None:
    if len(frame) != size:
        raise ProtocolError(f"expected {size} bytes, got {len(frame)}")


def _decode_beam(frame: bytes) -> Beam:
    _require_size(frame, BEAM_TX_MESSAGE_SIZE)
    return Beam(*BEAM_TX.unpack_from(frame, HEADER.size))


def _decode_tracks(frame: bytes) -> TrackList:
    if len(frame) < HEADER.size + TRACK_HEADER.size:
        raise ProtocolError("track message is too short")
    (count,) = TRACK_HEADER.unpack_from(frame, HEADER.size)
    if count > TRACK_MESSAGE_MAXIMUM_TRACKS:
        raise ProtocolError("too many tracks")
    _require_size(frame, HEADER.size + TRACK_HEADER.size + TRACK.size * count)

    tracks = []
    offset = HEADER.size + TRACK_HEADER.size
    for _ in range(count):
        values = TRACK.unpack_from(frame, offset)
        track_id, status, _mem_flag = values[0:3]
        floats = values[3:14]
        timestamp_ms = values[14]
        tracks.append(Track(track_id, status, *floats, timestamp_ms))
        offset += TRACK.size
    return TrackList(tracks)


def _decode_detection(frame: bytes) -> Detection:
    if len(frame) < PLOT_DATA_OFFSET:
        raise ProtocolError("plot message is too short")
    beam = Beam(*BEAM_TX.unpack_from(frame, HEADER.size))
    (count,) = PLOT_HEADER.unpack_from(frame, HEADER.size + BEAM_TX.size)
    if count > PLOT_MESSAGE_MAXIMUM_PLOTS:
        raise ProtocolError("too many plots")
    _require_size(frame, PLOT_DATA_OFFSET + PLOT.size * count)

    plots = []
    for index in range(count):
        values = PLOT.unpack_from(frame, PLOT_DATA_OFFSET + PLOT.size * index)
        plots.append(Plot(*values[1:]))
    return Detection(beam.beam_type, beam.timestamp_ms, beam.beam_id, plots)


def _decode_attitude(frame: bytes) -> RadarAttitude:
    _require_size(frame, RADAR_ATTITUDE_MESSAGE_SIZE)
    return RadarAttitude(*RADAR_ATTITUDE.unpack_from(frame, HEADER.size))


def _decode_status(frame: bytes) -> MonitorStatus:
    _require_size(frame, MONITOR_STATUS_MESSAGE_SIZE)
    processor_time_ms, time_scale, paused, console, simulator = MONITOR_STATUS.unpack_from(
        frame, HEADER.size
    )
    return MonitorStatus(processor_time_ms, time_scale, bool(paused), bool(console), bool(simulator))


def _decode_operator(frame: bytes) -> OperatorControl:
    _require_size(frame, OPERATOR_MESSAGE_SIZE)
    (status,) = OPERATOR.unpack_from(frame, HEADER.size)
    return OperatorControl(status)


def _decode_track_deletion(frame: bytes) -> TrackDeletion:
    if len(frame) < TRACK_DELETION_DATA_OFFSET:
        raise ProtocolError("track deletion message is too short")
    (count,) = TRACK_HEADER.unpack_from(frame, HEADER.size)
    if count > TRACK_DELETION_MAXIMUM_TRACKS:
        raise ProtocolError("too many deleted tracks")
    if len(frame) < TRACK_DELETION_DATA_OFFSET + DELETED_TRACK_ID.size * count:
        raise ProtocolError("track deletion message is too short")
    ids = [DELETED_TRACK_ID.unpack_from(frame, TRACK_DELETION_DATA_OFFSET + DELETED_TRACK_ID.size * index)[0]
           for index in range(count)]
    return TrackDeletion(ids)


class MessageFramer:
    """TCP 바이트 흐름을 헤더의 block_size 단위 프레임으로 나눈다.

    resync가 False(복사본 수신)이면 헤더가 잘못됐을 때 ProtocolError를 던진다. 경계를 다시 찾을 수
    없으므로 호출자는 연결을 끊어야 한다.
    resync가 True(통신 캡처)이면 1바이트씩 버리며 다음 올바른 헤더를 찾는다. 캡처는 연결 도중부터
    시작하거나 패킷을 놓칠 수 있기 때문이다. 버린 바이트 수는 skipped_bytes에 쌓인다.
    올바른 헤더는 알려진 메시지 ID이면서 그 종류에 맞는 크기(is_valid_frame_size)인 것이다.
    """

    def __init__(self, resync: bool = False) -> None:
        self._buffer = bytearray()
        self.resync = resync
        self.skipped_bytes = 0

    def reset(self) -> None:
        """쌓인 바이트를 버린다. 캡처에서 놓친 구간이 생겼을 때 쓴다."""
        self.skipped_bytes += len(self._buffer)
        self._buffer.clear()

    def feed(self, data: bytes) -> List[bytes]:
        """받은 바이트를 추가하고 완성된 프레임을 반환한다."""
        self._buffer.extend(data)
        frames = []
        while len(self._buffer) >= HEADER.size:
            header = decode_header(bytes(self._buffer[: HEADER.size]))
            error = self._header_error(header)
            if error is not None:
                if not self.resync:
                    raise ProtocolError(error)
                del self._buffer[:1]
                self.skipped_bytes += 1
                continue
            if len(self._buffer) < header.block_size:
                break
            frames.append(bytes(self._buffer[: header.block_size]))
            del self._buffer[: header.block_size]
        return frames

    def _header_error(self, header: Header) -> Optional[str]:
        if not is_known_message_id(header.message_id):
            return f"unknown message id 0x{header.message_id:04X}"
        if header.block_size < HEADER.size or header.block_size > MAXIMUM_FRAME_SIZE:
            return f"invalid frame size {header.block_size}"
        if self.resync and not is_valid_frame_size(header.message_id, header.block_size):
            return f"invalid frame size {header.block_size} for 0x{header.message_id:04X}"
        return None


# 인코딩. 시험과 가짜 처리부(tools/fake_processor.py)에서 처리부와 같은 프레임을 만들 때 쓴다.

def encode_header(message_id: int, block_size: int) -> bytes:
    return HEADER.pack(message_id, 1, 0, block_size, 0, 0, 0, 0, 0)


def encode_beam(beam: Beam) -> bytes:
    body = BEAM_TX.pack(beam.beam_type, beam.timestamp_ms, beam.beam_id, beam.command_count,
                        beam.azimuth_deg, beam.elevation_deg, beam.azimuth_width_deg,
                        beam.elevation_width_deg)
    return encode_header(MessageId.BEAM_TRANSMIT, BEAM_TX_MESSAGE_SIZE) + body


def encode_tracks(tracks: List[Track]) -> bytes:
    size = HEADER.size + TRACK_HEADER.size + TRACK.size * len(tracks)
    data = bytearray(encode_header(MessageId.TRACK_INFORMATION, size))
    data += TRACK_HEADER.pack(len(tracks))
    for track in tracks:
        data += TRACK.pack(
            track.track_id, track.status, 0, track.slant_range_km, track.ground_range_km,
            track.azimuth_deg, track.elevation_deg, track.doppler_mps, track.latitude_deg,
            track.longitude_deg, track.altitude_km, track.speed_mps, track.heading_deg,
            track.flight_path_angle_deg, track.timestamp_ms, track.track_id, 0,
        )
    return bytes(data)


def encode_detection(detection: Detection) -> bytes:
    size = PLOT_DATA_OFFSET + PLOT.size * len(detection.plots)
    data = bytearray(encode_header(MessageId.PLOT_INFORMATION, size))
    data += BEAM_TX.pack(detection.beam_type, detection.timestamp_ms, detection.beam_id, 0,
                         0.0, 0.0, 0.0, 0.0)
    data += PLOT_HEADER.pack(len(detection.plots))
    for plot in detection.plots:
        data += PLOT.pack(0, plot.slant_range_km, plot.azimuth_deg, plot.elevation_deg,
                          plot.doppler_mps, plot.power_db)
    return bytes(data)


def encode_attitude(attitude: RadarAttitude) -> bytes:
    body = RADAR_ATTITUDE.pack(attitude.latitude_deg, attitude.longitude_deg, attitude.altitude_km,
                               attitude.roll_deg, attitude.pitch_deg, attitude.yaw_deg)
    return encode_header(MessageId.RADAR_ATTITUDE_TO_CONSOLE, RADAR_ATTITUDE_MESSAGE_SIZE) + body


def encode_status(status: MonitorStatus) -> bytes:
    body = MONITOR_STATUS.pack(status.processor_time_ms, status.time_scale, int(status.paused),
                               int(status.operator_console_connected),
                               int(status.scenario_simulator_connected))
    return encode_header(MessageId.MONITOR_STATUS, MONITOR_STATUS_MESSAGE_SIZE) + body


def encode_operator(control: OperatorControl) -> bytes:
    return encode_header(MessageId.OPERATOR_CONTROL, OPERATOR_MESSAGE_SIZE) + OPERATOR.pack(control.status)


def encode_simulator_attitude(attitude: SimulatorAttitude) -> bytes:
    body = RADAR_ATTITUDE.pack(attitude.latitude_deg, attitude.longitude_deg, attitude.altitude_km,
                               attitude.roll_deg, attitude.pitch_deg, attitude.yaw_deg)
    return encode_header(MessageId.RADAR_ATTITUDE_FROM_SIMULATOR, RADAR_ATTITUDE_MESSAGE_SIZE) + body


def encode_track_deletion(deletion: TrackDeletion) -> bytes:
    size = TRACK_DELETION_DATA_OFFSET + DELETED_TRACK_ID.size * len(deletion.track_ids)
    data = bytearray(encode_header(MessageId.TRACK_DELETION, size))
    data += TRACK_HEADER.pack(len(deletion.track_ids))
    for track_id in deletion.track_ids:
        data += DELETED_TRACK_ID.pack(track_id)
    return bytes(data)


def encode(message: Message) -> bytes:
    if isinstance(message, Beam):
        return encode_beam(message)
    if isinstance(message, TrackList):
        return encode_tracks(message.tracks)
    if isinstance(message, Detection):
        return encode_detection(message)
    if isinstance(message, RadarAttitude):
        return encode_attitude(message)
    if isinstance(message, MonitorStatus):
        return encode_status(message)
    if isinstance(message, OperatorControl):
        return encode_operator(message)
    if isinstance(message, SimulatorAttitude):
        return encode_simulator_attitude(message)
    if isinstance(message, TrackDeletion):
        return encode_track_deletion(message)
    raise ProtocolError(f"cannot encode {type(message).__name__}")
