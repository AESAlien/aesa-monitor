"""windows/setup_power.ps1을 가짜 powercfg로 실행한다(pwsh가 있을 때만)."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
SCHEME_A = "381b4222-f694-41f0-9685-ff5bb260df2e"  # 균형 조정
SCHEME_B = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"  # 고성능

# 가짜 powercfg: 구성표마다 값을 따로 두고, 활성 구성표를 바꿀 수 있다.
# Steps 예: "apply", "apply,switch:<GUID>,restore"
HARNESS = r'''
param([string]$Script, [string]$Steps, [string]$FailOn, [string]$LocalAppData, [string]$Active)
$env:LOCALAPPDATA = $LocalAppData
$global:Store = @{}
$global:Active = $Active
$global:BackupBeforeFirstSet = $null
$backupPath = Join-Path $LocalAppData "aesa-monitor/power-backup.json"
function powercfg.exe {
    $global:LASTEXITCODE = 0
    $a = $args
    switch ($a[0]) {
        "/getactivescheme" { "전원 구성표 GUID: $($global:Active)  (테스트)" }
        "/setactive" { $global:Active = $a[1] }
        "/query" {
            $k = "$($a[1])|$($a[3])"
            $ac = if ($global:Store.ContainsKey("$k-ac")) { $global:Store["$k-ac"] } else { 900 }
            $dc = if ($global:Store.ContainsKey("$k-dc")) { $global:Store["$k-dc"] } else { 600 }
            "  최소 가능한 설정: 0x00000000`n  현재 AC 전원 설정 인덱스: 0x{0:x8}`n  현재 DC 전원 설정 인덱스: 0x{1:x8}" -f $ac, $dc
        }
        { $_ -in "/setacvalueindex", "/setdcvalueindex" } {
            if ($null -eq $global:BackupBeforeFirstSet) { $global:BackupBeforeFirstSet = Test-Path $backupPath }
            if ($FailOn -and $a[3] -eq $FailOn -and [int]$a[4] -eq 0) { $global:LASTEXITCODE = 1; "실패"; return }
            $side = if ($a[0] -eq "/setacvalueindex") { "ac" } else { "dc" }
            $global:Store["$($a[1])|$($a[3])-$side"] = [int]$a[4]
        }
    }
}
function Get-NetAdapter { @() }
$source = (Get-Content -Raw $Script).Replace('if (-not (Test-Administrator))', 'if ($false)')
$copy = Join-Path $LocalAppData "setup_power_test.ps1"
Set-Content -Encoding UTF8 $copy $source
$codes = @()
foreach ($step in $Steps.Split(",")) {
    if ($step.StartsWith("switch:")) { $global:Active = $step.Substring(7); continue }
    & $copy -Mode $step *> $null
    $codes += $LASTEXITCODE
}
$backup = if (Test-Path $backupPath) { Get-Content -Raw $backupPath | ConvertFrom-Json } else { $null }
@{ codes = $codes; store = $global:Store; backup = $backup; active = $global:Active;
   backupBeforeFirstSet = $global:BackupBeforeFirstSet } | ConvertTo-Json -Depth 6 -Compress
'''


@unittest.skipUnless(PWSH, "pwsh 없음")
class SetupPowerTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.harness = self.folder / "harness.ps1"
        self.harness.write_text(HARNESS, encoding="utf-8")
        self.appdata = self.folder / "appdata"
        self.appdata.mkdir()

    def run_power(self, steps, fail_on="", active=SCHEME_A):
        result = subprocess.run([PWSH, "-NoProfile", "-File", str(self.harness), "-Script",
                                 str(ROOT / "windows" / "setup_power.ps1"), "-Steps", steps, "-FailOn", fail_on,
                                 "-LocalAppData", str(self.appdata), "-Active", active],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_backup_is_written_before_first_change(self):
        result = self.run_power("apply")
        self.assertEqual(result["codes"], [0])
        self.assertTrue(result["backupBeforeFirstSet"])
        self.assertEqual(result["backup"]["Scheme"], SCHEME_A)
        self.assertEqual(result["backup"]["Values"]["STANDBYIDLE"], {"Ac": 900, "Dc": 600})
        self.assertEqual(result["store"][f"{SCHEME_A}|STANDBYIDLE-ac"], 0)

    def test_failure_midway_rolls_back_and_keeps_backup(self):
        result = self.run_power("apply", fail_on="LIDACTION")  # 네 번째 설정에서 실패
        self.assertEqual(result["codes"], [1])
        self.assertEqual(result["backup"]["Values"]["STANDBYIDLE"], {"Ac": 900, "Dc": 600})
        for key in ("STANDBYIDLE-ac", "STANDBYIDLE-dc", "HIBERNATEIDLE-ac", "VIDEOIDLE-ac"):
            self.assertEqual(result["store"][f"{SCHEME_A}|{key}"], 900 if key.endswith("ac") else 600, key)

    def test_backup_write_failure_changes_nothing(self):
        (self.appdata / "aesa-monitor").write_text("폴더 자리에 파일", encoding="utf-8")
        result = self.run_power("apply")
        self.assertEqual(result["codes"], [1])
        self.assertIsNone(result["backupBeforeFirstSet"])  # 설정을 한 번도 바꾸지 않음
        self.assertEqual(result["store"], {})

    def test_restore_targets_backed_up_scheme_after_plan_switch(self):
        result = self.run_power(f"apply,switch:{SCHEME_B},restore")
        self.assertEqual(result["codes"], [0, 0])
        self.assertEqual(result["store"][f"{SCHEME_A}|STANDBYIDLE-ac"], 900)   # A가 원래 값으로
        self.assertFalse(any(key.startswith(SCHEME_B) for key in result["store"]), "B를 건드렸음")
        self.assertEqual(result["active"], SCHEME_A)
        self.assertIsNone(result["backup"])

    def test_apply_refuses_when_backup_belongs_to_other_scheme(self):
        result = self.run_power(f"apply,switch:{SCHEME_B},apply")
        self.assertEqual(result["codes"], [0, 1])
        self.assertFalse(any(key.startswith(SCHEME_B) for key in result["store"]), "B를 바꿨음")
        self.assertEqual(result["backup"]["Scheme"], SCHEME_A)
