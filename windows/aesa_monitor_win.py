"""aesa-monitor Windows 실행파일 진입점 (PyInstaller로 aesa-monitor.exe를 만들 때 쓴다).

저장소 코드(aesa_monitor)는 고치지 않고, Windows에서 실행할 때 필요한 것만 여기서 맞춘다.

- 설정·로그 위치를 exe가 있는 폴더로 둔다. onefile exe에서 aesa_monitor의 __file__은 실행할 때마다
  새로 풀리는 임시 폴더라서, 그대로 두면 monitor.env를 못 찾고 종료할 때 저장한 값도 사라진다.
- 캡처 명령을 shlex(POSIX 규칙)로 나누지 않고 문자열 그대로 CreateProcess에 넘긴다.
  POSIX 규칙은 Windows 경로의 역슬래시를 지운다.
- 캡처 명령의 {interface}를 처리부와 같은 네트워크(예: 192.168.10.x)의 랜카드로 바꾼다. PC·USB 랜 어댑터마다
  다른 Npcap 장치 이름(\\Device\\NPF_{GUID})을 monitor.env에 적지 않아도 된다. 캡처를 다시 시작할 때마다 다시 찾는다.
- 창 없는 exe에서 dumpcap·ssh를 띄울 때 콘솔 창이 뜨지 않게 한다(CREATE_NO_WINDOW, stdin=DEVNULL).
- Wireshark 폴더를 PATH 앞에 넣어 MONITOR_CAPTURE_COMMAND에 'dumpcap'만 써도 되게 한다.
- monitor.env의 MONITOR_SERVER(이 진입점에서만 쓰는 키)를 --server 기본값으로 쓴다.
  exe 폴더에 처리부 .env가 있으면 --env 기본값으로 쓴다.
- 글꼴을 맑은 고딕으로 맞춘다(Windows 기본 Segoe UI에는 한글이 없어 글자마다 대체 글꼴이 섞인다).
- 표준 출력·오류를 exe 폴더의 aesa-monitor.log에 남기고, 시작 오류는 메시지 상자로 보인다.
"""

from __future__ import annotations

import datetime
import io
import ipaddress
import os
import subprocess
import sys
import traceback
import types
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, List, Optional, Tuple

FROZEN = getattr(sys, "frozen", False)
WINDOWS = sys.platform == "win32"
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "monitor.env"
LOG_PATH = APP_DIR / "aesa-monitor.log"
SERVER_KEY = "MONITOR_SERVER"
INTERFACE_PLACEHOLDER = "{interface}"
LOOPBACK_DEVICE = r"\Device\NPF_Loopback"
# 랜카드 주소와 브리지 구성을 읽는다. 줄 형식:
#   ADDR|{GUID}|주소|접두 길이|상태|브리지 장치(1/0)|이름
#   MEMBER|{GUID}|상태|이름      브리지(네트워크 어댑터 멀티플렉서)에 묶인 실제 랜카드
# 상태는 Get-NetAdapter의 Status(Up, Disconnected, Disabled 등). 케이블이 빠진 랜카드도 고정 주소는 남아 있을 수 있다.
# Windows 네트워크 브리지는 "Microsoft Network Adapter Multiplexor Driver" 장치에 IP를 두고,
# 묶인 랜카드에는 "멀티플렉서 프로토콜"(ms_implat)이 켜진다.
ADAPTER_QUERY = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { "
    "$adapter = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue; "
    "if ($adapter) { 'ADDR|{0}|{1}|{2}|{3}|{4}|{5}' -f $adapter.InterfaceGuid, $_.IPAddress, $_.PrefixLength, "
    "$adapter.Status, [int]($adapter.InterfaceDescription -like '*Multiplexor*'), $adapter.Name } }; "
    "Get-NetAdapterBinding -ComponentID ms_implat -ErrorAction SilentlyContinue | Where-Object Enabled | "
    "ForEach-Object { $member = Get-NetAdapter -Name $_.Name -ErrorAction SilentlyContinue; "
    "if ($member) { 'MEMBER|{0}|{1}|{2}' -f $member.InterfaceGuid, $member.Status, $member.Name } }"
)
ADAPTER_UP = "Up"
ADAPTER_QUERY_TIMEOUT_SECONDS = 15

if not FROZEN:
    # 소스에서 실행할 때(python windows\aesa_monitor_win.py): 저장소 폴더를 import 경로에 넣는다.
    sys.path.insert(0, str(APP_DIR.parent))

SETTINGS_TEMPLATE = """\
# aesa-monitor 설정 (Windows 실행파일용). 값이 없으면 기본값을 쓴다. 종료할 때 글자 크기 등이 저장된다.

# 처리부(Zynq) 서버 주소 ADDRESS:PORT. Windows 실행파일에서만 쓰는 키다(--server와 같음).
# exe 폴더에 처리부 .env가 있으면 그 파일의 운용통제기·모의기 주소와 추적 설정도 함께 읽는다.
MONITOR_SERVER=192.168.10.20:3200

# 데이터 출처: capture(통신 캡처)
MONITOR_SOURCE=capture
# Wireshark의 dumpcap으로 캡처한다(Wireshark + Npcap 설치 필요). {port}는 처리부 서버 포트로 바뀐다.
# {interface}는 처리부 주소(MONITOR_SERVER)와 같은 네트워크 주소를 가진 랜카드로 자동으로 바뀐다.
#   예: 처리부 192.168.10.20 → 192.168.10.x/24 주소가 있는 랜카드. 노트북 IP는 windows\\setup_network.bat으로 설정한다.
#   그 주소가 네트워크 브리지에 있으면 브리지에 묶인 랜카드 중 연결된 쪽을 쓴다(둘 다 연결이면 직접 넣으라고 안내).
#   처리부가 127.0.0.1이면 루프백(\\Device\\NPF_Loopback)을 쓴다.
# 랜카드를 직접 정하려면 {interface} 대신 장치 이름이나 번호를 넣는다(명령 프롬프트에서 dumpcap -D 로 확인).
# -P: pcap 형식, -q: 패킷 수 출력 안 함
MONITOR_CAPTURE_COMMAND='dumpcap -i {interface} -f "tcp port {port}" -P -q -w -'

# 녹화 세션 폴더를 만들 곳
MONITOR_RECORD_DIR=~/aesa-records
MONITOR_FONT_SIZE=12
MONITOR_AUTO_RECORD=0
# 운용 OFF→ON(새 시나리오)마다 실시간 화면을 지운다(메뉴 '운용 ON마다 화면 초기화'와 같음)
MONITOR_RESET_ON_OPERATION_START=0

# 처리부 로그 가져오기·처리부 제어: local 또는 ssh(보드). ssh는 Windows 10 이상의 OpenSSH 클라이언트를 쓴다.
# 보드 계정·키는 prepare_board_sd.bat(노트북)과 보드의 install_board.sh가 설정한다.
MONITOR_LOG_SOURCE=ssh
MONITOR_LOG_LOCAL_DIR=
MONITOR_LOG_SSH_TARGET=petalinux@192.168.10.20
MONITOR_LOG_SSH_PORT=22
MONITOR_LOG_REMOTE_DIR=/opt/aesa/logs
MONITOR_LOG_SSH_OPTIONS="-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o IdentityFile=~/.ssh/aesa_board_ed25519"
"""


class Tee(io.TextIOBase):
    """여러 스트림에 같이 쓴다. 시작 오류 문구를 메시지 상자에 보이려고 쓴다."""

    def __init__(self, *streams) -> None:
        super().__init__()
        self._streams = [stream for stream in streams if stream is not None]

    def write(self, text: str) -> int:
        for stream in self._streams:
            try:
                stream.write(text)
                stream.flush()
            except (OSError, ValueError):
                pass
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except (OSError, ValueError):
                pass


def setup_streams() -> io.StringIO:
    """창 없는 exe는 sys.stdout·sys.stderr가 None이다. 로그 파일로 돌리고, 오류는 버퍼에도 모은다."""
    try:
        log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        log.write(f"\n--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} 시작, 인자 {sys.argv[1:]}\n")
    except OSError:
        log = None
    errors = io.StringIO()
    sys.stdout = Tee(sys.stdout, log)
    sys.stderr = Tee(sys.stderr, log, errors)
    return errors


def show_error(title: str, text: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, text, parent=root)
        root.destroy()
    except Exception:  # 메시지 상자도 못 띄우면 로그만 남는다.
        pass


def read_settings_values(path: Optional[Path] = None) -> dict:
    """모니터 설정 파일 값. path가 없으면 exe 폴더의 monitor.env."""
    from aesa_monitor.core.config import read_env_file

    path = SETTINGS_PATH if path is None else path
    try:
        return read_env_file(path) if path.is_file() else {}
    except Exception:
        return {}


def option_value(arguments: List[str], option: str) -> Optional[str]:
    """명령줄에서 '--옵션 값' 또는 '--옵션=값'의 값을 찾는다. 없으면 None."""
    for index, item in enumerate(arguments):
        if item == option and index + 1 < len(arguments):
            return arguments[index + 1]
        if item.startswith(option + "="):
            return item[len(option) + 1:]
    return None


def add_wireshark_to_path() -> None:
    roots = [os.environ.get(name) for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
    for root in roots:
        if not root:
            continue
        folder = Path(root) / "Wireshark"
        if (folder / "dumpcap.exe").is_file():
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            return


def patch_subprocess() -> None:
    """자식 프로세스(dumpcap, ssh, scp)가 콘솔 창을 띄우지 않게 한다."""
    original = subprocess.Popen.__init__

    def init(self, args, *positional, **keywords):
        if not positional:
            flags = keywords.get("creationflags", 0) or 0
            if not flags & (subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS):
                keywords["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
            if keywords.get("stdin") is None:
                keywords["stdin"] = subprocess.DEVNULL
        original(self, args, *positional, **keywords)

    subprocess.Popen.__init__ = init


@dataclass(frozen=True)
class Member:
    """네트워크 브리지에 묶인 실제 랜카드."""
    guid: str
    name: str
    status: str = ADAPTER_UP

    @property
    def up(self) -> bool:
        return self.status.strip().lower() == ADAPTER_UP.lower()

    def describe(self) -> str:
        return f"{self.name} [{self.status}]"

    @property
    def device(self) -> str:
        return rf"\Device\NPF_{{{self.guid}}}"


@dataclass(frozen=True)
class Adapter:
    guid: str      # 중괄호 없는 GUID
    address: str   # IPv4 주소
    prefix: int    # 접두 길이(24 = 255.255.255.0)
    name: str      # Windows 연결 이름(예: 이더넷 2)
    status: str = ADAPTER_UP  # Get-NetAdapter Status. Up만 패킷을 받는다
    bridge: bool = False      # 네트워크 브리지 장치(멀티플렉서)인지
    members: Tuple[Member, ...] = ()  # bridge일 때 묶인 랜카드

    @property
    def up(self) -> bool:
        return self.status.strip().lower() == ADAPTER_UP.lower()

    def describe(self) -> str:
        return f"{self.name} {self.address}/{self.prefix} [{self.status}]"

    @property
    def device(self) -> str:
        return rf"\Device\NPF_{{{self.guid}}}"


def parse_adapters(text: str) -> List[Adapter]:
    """ADAPTER_QUERY 출력을 읽는다. 형식이 맞지 않는 줄은 건너뛴다.

    브리지 장치가 하나뿐이면 MEMBER 줄을 그 브리지의 묶인 랜카드로 붙인다.
    브리지(또는 NIC 팀) 장치가 둘 이상이면 어느 쪽에 묶였는지 알 수 없어 붙이지 않는다.
    """
    adapters = []
    members = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if parts[0] == "ADDR" and len(parts) >= 7:
            guid, address, prefix, status, bridge = parts[1:6]
            name = "|".join(parts[6:])
            try:
                ipaddress.IPv4Address(address)
                adapters.append(Adapter(guid.strip("{} ").upper(), address, int(prefix), name.strip(),
                                        status.strip(), bridge.strip() == "1"))
            except ValueError:
                continue
        elif parts[0] == "MEMBER" and len(parts) >= 4:
            members.append(Member(parts[1].strip("{} ").upper(), "|".join(parts[3:]).strip(), parts[2].strip()))
    bridges = {adapter.guid for adapter in adapters if adapter.bridge}
    if len(bridges) == 1 and members:
        adapters = [replace(adapter, members=tuple(members)) if adapter.bridge else adapter for adapter in adapters]
    return adapters


def query_adapters() -> List[Adapter]:
    """PowerShell로 랜카드 주소를 읽는다. 실패하면 ValueError(캡처 실패 사유로 보임, 잠시 뒤 다시 시도)."""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ADAPTER_QUERY],
            capture_output=True, timeout=ADAPTER_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"랜카드 주소를 읽지 못했습니다(PowerShell): {error}") from error
    return parse_adapters(completed.stdout.decode("utf-8", "replace"))


def choose_capture_device(adapters: List[Adapter], server_address: str) -> Tuple[str, str]:
    """처리부 주소와 같은 네트워크의 랜카드를 고른다. (Npcap 장치 이름, 설명)을 돌려준다.

    - 처리부 주소를 가진 랜카드는 고르지 않는다(처리부를 이 PC에서 돌리는 경우가 아니면 잘못 넣은 주소다).
    - 연결된(Up) 랜카드만 고른다. 케이블이 빠진 내장 랜카드에 192.168.10.x가 남아 있으면 그쪽을 골라
      dumpcap이 아무것도 받지 못하는 일이 생긴다.
    - 연결된 랜카드가 둘 이상이면 고르지 않고 오류로 알린다(어느 쪽이 처리부와 이어졌는지 알 수 없다).
      한 랜카드에 주소가 여럿(노트북 1대 구성 .30·.10·.100)인 것은 하나로 센다.
    """
    server = ipaddress.IPv4Address(server_address)
    if server.is_loopback:
        return LOOPBACK_DEVICE, "루프백"
    matches = [adapter for adapter in adapters
               if adapter.address != server_address
               and server in ipaddress.IPv4Network(f"{adapter.address}/{adapter.prefix}", strict=False)]
    manual = (f"monitor.env의 MONITOR_CAPTURE_COMMAND에서 {INTERFACE_PLACEHOLDER} 대신 랜카드를 직접 넣을 수도 "
              f"있습니다(dumpcap -D로 확인)")
    if not matches:
        found = ", ".join(adapter.describe() for adapter in adapters) or "없음"
        raise ValueError(
            f"처리부 {server_address}와 같은 네트워크 주소를 가진 랜카드가 없습니다(지금 주소: {found}). "
            f"windows\\setup_network.bat으로 노트북 IP를 설정하세요. {manual}"
        )
    connected = {}
    for adapter in matches:
        if adapter.up:
            connected.setdefault(adapter.guid, adapter)
    if not connected:
        found = ", ".join(adapter.describe() for adapter in matches)
        raise ValueError(
            f"처리부 {server_address}와 같은 네트워크의 랜카드가 모두 연결되어 있지 않습니다({found}). "
            f"랜선과 USB 랜 어댑터를 확인하세요"
        )
    if len(connected) > 1:
        found = ", ".join(adapter.describe() for adapter in connected.values())
        raise ValueError(
            f"처리부 {server_address}와 같은 네트워크에 연결된 랜카드가 여럿이라 고를 수 없습니다({found}). "
            f"쓰지 않는 랜카드의 192.168.10.x 주소를 지우거나(setup_network.bat → DHCP), {manual}"
        )
    chosen = next(iter(connected.values()))
    if chosen.bridge:
        return choose_bridge_member(chosen, manual)
    return chosen.device, f"{chosen.name} ({chosen.address}/{chosen.prefix})"


def choose_bridge_member(bridge: Adapter, manual: str) -> Tuple[str, str]:
    """IP가 네트워크 브리지에 있으면 브리지 장치 대신 묶인 실제 랜카드를 캡처한다.

    브리지 장치에서는 처리부와 다른 장비 사이를 지나가는 패킷이 대부분 보이지 않는다(2026-11 노트북 실측:
    같은 25초 동안 브리지 장치 3개, 묶인 랜카드 15개). 묶인 랜카드 중 연결된 것이 하나면 그것을 고른다.
    둘 이상이면 어느 쪽이 처리부(보드)와 이어졌는지 알 수 없어 고르지 않는다.
    """
    lines = "; ".join(f"{member.name}: dumpcap -i {member.device}" for member in bridge.members)
    if not bridge.members:
        raise ValueError(
            f"{bridge.name}는 네트워크 브리지인데 묶인 랜카드를 찾지 못했습니다. 브리지 장치로는 처리부 통신을 "
            f"놓칠 수 있습니다. {manual}"
        )
    connected = [member for member in bridge.members if member.up]
    if not connected:
        found = ", ".join(member.describe() for member in bridge.members)
        raise ValueError(f"네트워크 브리지 {bridge.name}에 묶인 랜카드가 모두 연결되어 있지 않습니다({found}). 랜선을 확인하세요")
    if len(connected) > 1:
        found = ", ".join(member.describe() for member in connected)
        raise ValueError(
            f"네트워크 브리지 {bridge.name}에 연결된 랜카드가 여럿이라({found}) 보드가 어느 쪽인지 알 수 없습니다. "
            f"monitor.env의 MONITOR_CAPTURE_COMMAND에서 {INTERFACE_PLACEHOLDER} 대신 보드와 연결된 랜카드를 "
            f"넣으세요({lines})"
        )
    member = connected[0]
    return member.device, f"{member.name} ({bridge.name} {bridge.address}/{bridge.prefix}에 묶인 랜카드)"


def resolve_capture_command(command: str, server_address: Optional[str],
                            query: Optional[Callable[[], List[Adapter]]] = None) -> str:
    """캡처 명령의 {interface}를 랜카드 장치 이름으로 바꾼다. 없으면 그대로 돌려준다."""
    if INTERFACE_PLACEHOLDER not in command:
        return command
    if not server_address:
        raise ValueError(f"처리부 주소를 몰라 {INTERFACE_PLACEHOLDER}를 정할 수 없습니다(MONITOR_SERVER 또는 --server)")
    device, description = choose_capture_device((query or query_adapters)(), server_address)
    print(f"캡처 랜카드: {description} → {device}", file=sys.stdout)
    return command.replace(INTERFACE_PLACEHOLDER, device)


def patch_capture_command() -> None:
    """캡처 명령 문자열을 나누지 않고 그대로 넘긴다(Windows CreateProcess가 직접 해석).

    {interface}는 캡처를 (다시) 시작할 때마다 랜카드를 찾아 바꾼다. 실패하면 ValueError가 되어
    상단 바 처리부 점의 사유로 보이고, 잠시 뒤 다시 시도한다(노트북 IP를 나중에 넣어도 된다).
    """
    from aesa_monitor.io import capture

    server = {"address": None}
    original_format = capture.format_command

    def format_command(command, config):
        if config is not None:
            server["address"] = config.server[0]
        return original_format(command, config)

    capture.format_command = format_command
    capture.shlex = types.SimpleNamespace(split=lambda command: resolve_capture_command(command, server["address"]))


def patch_fonts() -> None:
    import tkinter as tk
    import tkinter.font as tkfont

    original = tk.Tk.__init__
    names = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
             "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont")

    def init(self, *positional, **keywords):
        original(self, *positional, **keywords)
        try:
            families = set(tkfont.families(self))
            family = next((name for name in ("Malgun Gothic", "맑은 고딕") if name in families), None)
            if family is not None:
                for name in names:
                    tkfont.Font(root=self, name=name, exists=True).configure(family=family)
        except tk.TclError:
            pass

    tk.Tk.__init__ = init


def point_settings_to_app_dir() -> None:
    import aesa_monitor.core.settings as settings

    settings.PROJECT_DIRECTORY = APP_DIR
    settings.DEFAULT_SETTINGS_PATH = SETTINGS_PATH
    if not SETTINGS_PATH.exists():
        try:
            SETTINGS_PATH.write_text(SETTINGS_TEMPLATE, encoding="utf-8")
        except OSError:
            pass


def build_arguments(arguments: List[str]) -> List[str]:
    arguments = list(arguments)

    def given(option: str) -> bool:
        return any(item == option or item.startswith(option + "=") for item in arguments)

    if not given("--env") and (APP_DIR / ".env").is_file():
        arguments += ["--env", str(APP_DIR / ".env")]
    # MONITOR_SERVER는 모니터가 실제로 읽을 설정 파일(--settings로 고른 파일, 없으면 exe 폴더의 monitor.env)에서 읽는다.
    # 기본 파일에서 읽으면 --settings로 다른 처리부를 지정해도 기본 처리부에 붙고 그 네트워크의 랜카드를 고른다.
    selected = option_value(arguments, "--settings")
    server = read_settings_values(Path(selected) if selected else None).get(SERVER_KEY, "")
    if server and not given("--server"):
        arguments += ["--server", server]
    return arguments


def main(arguments: Optional[List[str]] = None) -> int:
    errors = setup_streams()
    try:
        if WINDOWS:
            add_wireshark_to_path()
            patch_subprocess()
            patch_fonts()
        point_settings_to_app_dir()
        if WINDOWS:
            patch_capture_command()
        final = build_arguments(sys.argv[1:] if arguments is None else arguments)
        print(f"인자 {final}", file=sys.stdout)

        from aesa_monitor.__main__ import main as monitor_main

        code = monitor_main(final)
    except SystemExit as error:  # argparse 오류·--help
        code = error.code if isinstance(error.code, int) else 1
    except Exception:
        text = traceback.format_exc()
        sys.stderr.write(text)
        show_error("aesa-monitor 오류", f"{text}\n\n로그: {LOG_PATH}")
        return 1
    if code not in (0, None) and errors.getvalue().strip():
        show_error("aesa-monitor", f"{errors.getvalue().strip()}\n\n설정: {SETTINGS_PATH}\n로그: {LOG_PATH}")
    return code or 0


if __name__ == "__main__":
    sys.exit(main())
