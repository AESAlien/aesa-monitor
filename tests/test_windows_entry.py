"""windows/aesa_monitor_win.py의 캡처 랜카드 자동 선택({interface}) 시험. Windows가 아니어도 돈다."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "windows" / "aesa_monitor_win.py"
spec = importlib.util.spec_from_file_location("aesa_monitor_win", ENTRY)
win = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = win  # dataclass가 모듈을 찾으므로 먼저 등록한다
spec.loader.exec_module(win)

QUERY_OUTPUT = """\
{92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934}|192.168.10.30|24|Up|이더넷 2
{92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934}|192.168.10.10|24|Up|이더넷 2
{11111111-2222-3333-4444-555555555555}|192.168.0.5|24|Up|Wi-Fi
{AAAAAAAA-2222-3333-4444-555555555555}|169.254.10.3|16|Up|VMware Network Adapter VMnet1
줄 형식이 아님
{CCCCCCCC-2222-3333-4444-555555555555}|192.168.10.31|24|이전 형식(상태 없음)
{BBBBBBBB-2222-3333-4444-555555555555}|주소아님|24|Up|이상한 줄
"""
BUILT_IN = "{DDDDDDDD-2222-3333-4444-555555555555}|192.168.10.30|24|Disconnected|이더넷"
USB = "{EEEEEEEE-2222-3333-4444-555555555555}|192.168.10.30|24|Up|이더넷 3"
COMMAND = 'dumpcap -i {interface} -f "tcp port 3200" -P -q -w -'


class AdapterParseTest(unittest.TestCase):
    def test_reads_guid_address_prefix_and_name(self):
        adapters = win.parse_adapters(QUERY_OUTPUT)
        self.assertEqual(len(adapters), 4)
        self.assertEqual(adapters[0],
                         win.Adapter("92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934", "192.168.10.30", 24, "이더넷 2", "Up"))
        self.assertEqual(adapters[0].device, r"\Device\NPF_{92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934}")


class ChooseDeviceTest(unittest.TestCase):
    def setUp(self):
        self.adapters = win.parse_adapters(QUERY_OUTPUT)

    def test_picks_adapter_on_processor_network(self):
        device, description = win.choose_capture_device(self.adapters, "192.168.10.20")
        self.assertEqual(device, r"\Device\NPF_{92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934}")
        self.assertIn("이더넷 2", description)

    def test_loopback_processor_uses_loopback_adapter(self):
        self.assertEqual(win.choose_capture_device(self.adapters, "127.0.0.1")[0], r"\Device\NPF_Loopback")

    def test_no_adapter_on_network_explains_what_to_do(self):
        with self.assertRaises(ValueError) as raised:
            win.choose_capture_device(self.adapters, "10.0.0.20")
        self.assertIn("setup_network.bat", str(raised.exception))
        self.assertIn("192.168.10.30/24", str(raised.exception))

    def test_prefers_connected_adapter_over_disconnected_one_on_same_network(self):
        # 케이블이 빠진 내장 랜카드에 192.168.10.x가 남아 있어도 연결된 USB 랜 어댑터를 고른다.
        for lines in ((BUILT_IN, USB), (USB, BUILT_IN)):
            device, description = win.choose_capture_device(win.parse_adapters("\n".join(lines)), "192.168.10.20")
            self.assertEqual(device, r"\Device\NPF_{EEEEEEEE-2222-3333-4444-555555555555}")
            self.assertIn("이더넷 3", description)

    def test_only_disconnected_adapters_is_an_error(self):
        with self.assertRaises(ValueError) as raised:
            win.choose_capture_device(win.parse_adapters(BUILT_IN), "192.168.10.20")
        self.assertIn("연결되어 있지 않습니다", str(raised.exception))
        self.assertIn("Disconnected", str(raised.exception))

    def test_two_connected_adapters_on_network_is_ambiguous(self):
        other = USB.replace("EEEEEEEE", "FFFFFFFF").replace("이더넷 3", "이더넷 4").replace(".30", ".31")
        with self.assertRaises(ValueError) as raised:
            win.choose_capture_device(win.parse_adapters("\n".join((USB, other))), "192.168.10.20")
        self.assertIn("여럿", str(raised.exception))
        self.assertIn("이더넷 3", str(raised.exception))
        self.assertIn("이더넷 4", str(raised.exception))

    def test_several_addresses_on_one_adapter_is_not_ambiguous(self):
        # 노트북 1대 구성(.30·.10·.100이 한 랜카드)
        adapters = win.parse_adapters(QUERY_OUTPUT)
        self.assertEqual(sum(1 for adapter in adapters if adapter.guid.startswith("92DAE06A")), 2)
        self.assertTrue(win.choose_capture_device(adapters, "192.168.10.20")[0].endswith("0ACBBB9A1934}"))

    def test_adapter_holding_processor_address_is_not_chosen(self):
        adapters = [win.Adapter("11111111-2222-3333-4444-555555555555", "192.168.10.20", 24, "잘못 넣은 주소", "Up")]
        with self.assertRaises(ValueError):
            win.choose_capture_device(adapters, "192.168.10.20")


class ResolveCommandTest(unittest.TestCase):
    def test_replaces_interface_placeholder(self):
        adapters = win.parse_adapters(QUERY_OUTPUT)
        command = win.resolve_capture_command(COMMAND, "192.168.10.20", lambda: adapters)
        self.assertEqual(
            command, r'dumpcap -i \Device\NPF_{92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934} -f "tcp port 3200" -P -q -w -')

    def test_explicit_interface_is_kept_without_query(self):
        def query():
            raise AssertionError("랜카드를 직접 적었으면 찾지 않는다")
        command = r'dumpcap -i \Device\NPF_Loopback -f "tcp port 3200" -P -q -w -'
        self.assertEqual(win.resolve_capture_command(command, "192.168.10.20", query), command)

    def test_unknown_server_address_is_an_error(self):
        with self.assertRaises(ValueError):
            win.resolve_capture_command(COMMAND, None, lambda: [])

    def test_adapter_query_failure_becomes_capture_error(self):
        # PowerShell이 없거나 멈추면 캡처 실패 사유(ValueError)로 바뀌어야 캡처 스레드가 죽지 않는다.
        with mock.patch.object(win.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell", 15)):
            with self.assertRaises(ValueError):
                win.resolve_capture_command(COMMAND, "192.168.10.20")
        with mock.patch.object(win.subprocess, "run", side_effect=FileNotFoundError("powershell")):
            with self.assertRaises(ValueError):
                win.resolve_capture_command(COMMAND, "192.168.10.20")

    def test_settings_template_uses_placeholder(self):
        self.assertIn("MONITOR_CAPTURE_COMMAND='dumpcap -i {interface}", win.SETTINGS_TEMPLATE)


class BuildArgumentsTest(unittest.TestCase):
    """MONITOR_SERVER는 실제로 쓸 설정 파일(--settings)에서 읽는다."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.default = root / "monitor.env"
        self.default.write_text("MONITOR_SERVER=192.168.10.20:3200\n", encoding="utf-8")
        self.custom = root / "custom.env"
        self.custom.write_text("MONITOR_SERVER=127.0.0.1:43200\n", encoding="utf-8")
        patches = [mock.patch.object(win, "SETTINGS_PATH", self.default), mock.patch.object(win, "APP_DIR", root)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.directory.cleanup)

    def server_of(self, arguments):
        result = win.build_arguments(arguments)
        return result[result.index("--server") + 1] if "--server" in result else None

    def test_default_settings_file(self):
        self.assertEqual(self.server_of([]), "192.168.10.20:3200")

    def test_selected_settings_file(self):
        self.assertEqual(self.server_of(["--settings", str(self.custom)]), "127.0.0.1:43200")
        self.assertEqual(self.server_of([f"--settings={self.custom}"]), "127.0.0.1:43200")

    def test_selected_file_without_server_does_not_fall_back_to_default(self):
        empty = Path(self.directory.name) / "empty.env"
        empty.write_text("MONITOR_FONT_SIZE=14\n", encoding="utf-8")
        self.assertIsNone(self.server_of(["--settings", str(empty)]))

    def test_explicit_server_wins(self):
        self.assertEqual(self.server_of(["--settings", str(self.custom), "--server", "10.0.0.1:3200"]), "10.0.0.1:3200")


class PackageFilesTest(unittest.TestCase):
    """배포 zip에 넣는 파일이 저장소에 실제로 있는지(빌드 결과물 windows/dist는 제외)."""

    def test_files_copied_by_workflow_exist(self):
        import re

        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "windows-exe.yml").read_text(encoding="utf-8")
        copies = [line for line in workflow.splitlines() if line.strip().startswith("Copy-Item")]
        paths = {path for line in copies for path in re.findall(r"windows/[\w./-]+", line)
                 if not path.startswith("windows/dist/")}
        self.assertIn("windows/processor.env.example", paths)
        for path in sorted(paths):
            self.assertTrue((root / path).is_file(), path)

    def test_processor_env_has_communication_endpoints(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "windows" / "processor.env.example").read_text(encoding="utf-8")
        for key in ("COMM_SERVER_ADDRESS", "COMM_OPERATOR_CONSOLE_ADDRESS", "COMM_SCENARIO_SIMULATOR_ADDRESS"):
            self.assertIn(key, text)


if __name__ == "__main__":
    unittest.main()
