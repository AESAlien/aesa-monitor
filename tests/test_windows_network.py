"""windows/setup_network.ps1을 가짜 네트워크 cmdlet으로 실행한다(pwsh가 있을 때만)."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")

# 2026-11-19 시연 노트북 구성: 이더넷 2(내장)·이더넷(USB)이 네트워크 브리지에 묶여 있다. Ethernet 5는 따로 쓰는 랜카드.
HARNESS = r'''
param([string]$Script, [string]$Adapter, [string]$Mode)
$global:Calls = @()
$global:HasIPv4 = @("네트워크 브리지", "Ethernet 5", "Wi-Fi")
$global:Members = @("이더넷 2", "이더넷")
$adapters = @(
  [pscustomobject]@{Name="이더넷 2"; InterfaceDescription="Realtek PCIe GbE Family Controller"; Status="Up"; HardwareInterface=$true; ifIndex=4},
  [pscustomobject]@{Name="이더넷"; InterfaceDescription="USB2.0 Ethernet Adapter"; Status="Up"; HardwareInterface=$true; ifIndex=5},
  [pscustomobject]@{Name="네트워크 브리지"; InterfaceDescription="Microsoft Network Adapter Multiplexor Driver"; Status="Up"; HardwareInterface=$false; ifIndex=27},
  [pscustomobject]@{Name="Ethernet 5"; InterfaceDescription="ASIX AX88179"; Status="Up"; HardwareInterface=$true; ifIndex=9}
)
function Get-NetAdapter { param($Name, $ErrorAction) if ($Name) { $hit = $adapters | Where-Object Name -eq $Name; if (-not $hit) { throw "no adapter $Name" }; $hit } else { $adapters } }
function Get-NetIPInterface { param($InterfaceAlias, $AddressFamily, $ErrorAction) if ($global:HasIPv4 -contains $InterfaceAlias) { [pscustomobject]@{InterfaceAlias=$InterfaceAlias} } }
function Get-NetAdapterBinding { param($Name, $ComponentID, $ErrorAction) [pscustomobject]@{Name=$Name; Enabled=($global:Members -contains $Name)} }
function Get-NetIPAddress { param($InterfaceAlias, $InterfaceIndex, $AddressFamily, $ErrorAction) @() }
function Remove-NetIPAddress { param([Parameter(ValueFromPipeline)]$InputObject, $Confirm) }
function Get-NetRoute { param($InterfaceAlias, $DestinationPrefix, $ErrorAction) @() }
function Remove-NetRoute { param([Parameter(ValueFromPipeline)]$InputObject, $Confirm) }
function Set-NetIPInterface { param($InterfaceAlias, $AddressFamily, $Dhcp) $global:Calls += "dhcp $InterfaceAlias $Dhcp" }
function New-NetIPAddress { param($InterfaceAlias, $IPAddress, $PrefixLength, $SkipAsSource) $global:Calls += "add $InterfaceAlias $IPAddress" }
$source = (Get-Content -Raw $Script).Replace('if (-not (Test-Administrator))', 'if ($false)')
$copy = Join-Path ([IO.Path]::GetTempPath()) ("setup_network_test_{0}.ps1" -f $PID)
Set-Content -Encoding UTF8 $copy $source
$global:LASTEXITCODE = 0
& $copy -Adapter $Adapter -Mode $Mode *> $null
$code = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
Remove-Item $copy
@{ code = $code; calls = $global:Calls } | ConvertTo-Json -Compress
'''


@unittest.skipUnless(PWSH, "pwsh 없음")
class SetupNetworkTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.harness = self.folder / "harness.ps1"
        self.harness.write_text(HARNESS, encoding="utf-8")

    def run_network(self, adapter, mode):
        result = subprocess.run([PWSH, "-NoProfile", "-File", str(self.harness), "-Script",
                                 str(ROOT / "windows" / "setup_network.ps1"), "-Adapter", adapter, "-Mode", mode],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_bridge_member_puts_address_on_bridge(self):
        result = self.run_network("이더넷 2", "monitor")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["calls"], ["dhcp 네트워크 브리지 Disabled", "add 네트워크 브리지 192.168.10.30"])

    def test_general_use_turns_bridge_back_to_dhcp(self):
        result = self.run_network("이더넷", "dhcp")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["calls"], ["dhcp 네트워크 브리지 Enabled"])

    def test_plain_adapter_is_configured_directly(self):
        result = self.run_network("Ethernet 5", "all")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["calls"], ["dhcp Ethernet 5 Disabled", "add Ethernet 5 192.168.10.30",
                                           "add Ethernet 5 192.168.10.10", "add Ethernet 5 192.168.10.100"])
