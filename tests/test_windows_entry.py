"""windows/aesa_monitor_win.py의 캡처 랜카드 자동 선택({interface}) 시험. Windows가 아니어도 돈다."""

import importlib.util
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "windows" / "aesa_monitor_win.py"
spec = importlib.util.spec_from_file_location("aesa_monitor_win", ENTRY)
win = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = win  # dataclass가 모듈을 찾으므로 먼저 등록한다
spec.loader.exec_module(win)

QUERY_OUTPUT = """\
{92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934}|192.168.10.30|24|이더넷 2
{92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934}|192.168.10.10|24|이더넷 2
{11111111-2222-3333-4444-555555555555}|192.168.0.5|24|Wi-Fi
{AAAAAAAA-2222-3333-4444-555555555555}|169.254.10.3|16|VMware Network Adapter VMnet1
줄 형식이 아님
{BBBBBBBB-2222-3333-4444-555555555555}|주소아님|24|이상한 줄
"""
COMMAND = 'dumpcap -i {interface} -f "tcp port 3200" -P -q -w -'


class AdapterParseTest(unittest.TestCase):
    def test_reads_guid_address_prefix_and_name(self):
        adapters = win.parse_adapters(QUERY_OUTPUT)
        self.assertEqual(len(adapters), 4)
        self.assertEqual(adapters[0], win.Adapter("92DAE06A-C50E-4EEB-8A17-0ACBBB9A1934", "192.168.10.30", 24, "이더넷 2"))
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

    def test_adapter_holding_processor_address_is_not_chosen(self):
        adapters = [win.Adapter("11111111-2222-3333-4444-555555555555", "192.168.10.20", 24, "잘못 넣은 주소")]
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


if __name__ == "__main__":
    unittest.main()
