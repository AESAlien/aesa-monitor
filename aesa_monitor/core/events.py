"""모니터 내부에서 주고받는 이벤트."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from aesa_monitor.core.protocol import Message

# 데이터 출처
SOURCE_COPY = "copy"        # 처리부가 모니터에 보내는 복사본(0xAD01 포함)을 받는다
SOURCE_CAPTURE = "capture"  # 처리부·모의기·운용통제기 사이 패킷을 캡처한다

# 처리부와 통신하는 상대
PEER_CONSOLE = "console"
PEER_SIMULATOR = "simulator"
PEER_NAMES = {PEER_CONSOLE: "운용통제기", PEER_SIMULATOR: "모의기"}


@dataclass(frozen=True)
class ConnectionChanged:
    """모니터의 데이터 출처 상태가 바뀌었다.

    복사본(copy): 처리부와 모니터 사이 TCP 연결. 캡처(capture): 캡처 프로그램이 돌고 있는지.
    """

    connected: bool
    detail: str = ""
    source: str = SOURCE_COPY


@dataclass(frozen=True)
class PeerLinkChanged:
    """캡처에서 본 처리부와 상대(운용통제기, 모의기) 사이 TCP 연결 상태.

    복사본 수신에서는 이 정보가 상태 메시지(0xAD01)에 들어 있으므로 쓰지 않는다.
    """

    peer: str
    connected: bool
    detail: str = ""


Event = Union[Message, ConnectionChanged, PeerLinkChanged]
