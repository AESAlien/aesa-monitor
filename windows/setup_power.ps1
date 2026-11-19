# aesa 시연용 노트북 전원 설정 (Windows). setup_power.bat을 두 번 눌러 실행한다(관리자 권한 필요).
#
# 시연 중 노트북이 잠들거나 USB 랜 어댑터 전원이 꺼지면 캡처가 끊긴다. 현재 전원 계획에서 아래를 끈다.
#   절전·최대 절전(AC/배터리), 화면 끄기(AC), 덮개 닫을 때 동작(AC/배터리), USB 선택적 절전(AC/배터리)
#   유선 랜카드의 "전원을 아끼기 위해 컴퓨터가 이 장치를 끌 수 있음"
# 바꾸기 전 값은 %LOCALAPPDATA%\aesa-monitor\power-backup.json에 저장하고, 메뉴 2번으로 되돌린다.
# (랜카드 전원 관리는 되돌리지 않는다. 장치 관리자 > 랜카드 > 전원 관리에서 직접 켤 수 있다.)
#
# PowerShell에서 직접: .\setup_power.ps1 -Mode apply | restore

param(
    [ValidateSet("", "apply", "restore")]
    [string]$Mode = ""
)

$ErrorActionPreference = "Stop"
$BackupPath = Join-Path $env:LOCALAPPDATA "aesa-monitor\power-backup.json"
# 이름, 하위 그룹, 설정, AC에 넣을 값, 배터리(DC)에 넣을 값($null이면 그대로)
$Settings = @(
    @{ Name = "절전까지 시간";         Group = "SUB_SLEEP";   Setting = "STANDBYIDLE";   Ac = 0; Dc = 0 },
    @{ Name = "최대 절전까지 시간";    Group = "SUB_SLEEP";   Setting = "HIBERNATEIDLE"; Ac = 0; Dc = 0 },
    @{ Name = "화면 끄기까지 시간";    Group = "SUB_VIDEO";   Setting = "VIDEOIDLE";     Ac = 0; Dc = $null },
    @{ Name = "덮개 닫을 때(0=아무것도 안 함)"; Group = "SUB_BUTTONS"; Setting = "LIDACTION"; Ac = 0; Dc = 0 },
    @{ Name = "USB 선택적 절전";       Group = "2a737441-1930-4402-8d77-b2bebba308a3"; Setting = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"; Ac = 0; Dc = 0 }
)

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-PowerCfg([string[]]$Arguments) {
    $output = & powercfg.exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "powercfg $($Arguments -join ' ') 실패: $output" }
    return $output
}

function Get-CurrentValue($Item) {
    # powercfg /query 출력은 언어마다 다르지만, 마지막 두 16진수 값이 현재 AC, DC 값이다.
    $text = (Invoke-PowerCfg @("/query", "SCHEME_CURRENT", $Item.Group, $Item.Setting)) -join "`n"
    $values = @([regex]::Matches($text, "0x[0-9a-fA-F]{8}") | ForEach-Object { [Convert]::ToInt64($_.Value, 16) })
    if ($values.Count -lt 2) { return $null }
    return @{ Ac = $values[-2]; Dc = $values[-1] }
}

function Set-Value($Item, $Ac, $Dc) {
    if ($null -ne $Ac) { Invoke-PowerCfg @("/setacvalueindex", "SCHEME_CURRENT", $Item.Group, $Item.Setting, "$Ac") | Out-Null }
    if ($null -ne $Dc) { Invoke-PowerCfg @("/setdcvalueindex", "SCHEME_CURRENT", $Item.Group, $Item.Setting, "$Dc") | Out-Null }
}

function Invoke-Apply {
    $backup = @{}
    if (Test-Path $BackupPath) {
        Write-Host "이전 백업이 있어 그대로 둡니다 (처음 값 보존): $BackupPath"
    }
    foreach ($item in $Settings) {
        $current = $null
        try { $current = Get-CurrentValue $item } catch { }
        if ($null -eq $current) { Write-Host ("  건너뜀: {0} (이 노트북에 없는 설정)" -f $item.Name); continue }
        $backup[$item.Setting] = $current
        Set-Value $item $item.Ac $item.Dc
        Write-Host ("  {0}: AC {1} -> {2}, 배터리 {3} -> {4}" -f $item.Name, $current.Ac, $item.Ac, $current.Dc,
            $(if ($null -eq $item.Dc) { "그대로" } else { $item.Dc }))
    }
    Invoke-PowerCfg @("/setactive", "SCHEME_CURRENT") | Out-Null
    if (-not (Test-Path $BackupPath)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $BackupPath) | Out-Null
        $backup | ConvertTo-Json | Set-Content -Encoding UTF8 $BackupPath
        Write-Host "바꾸기 전 값 저장: $BackupPath"
    }
    $wired = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object {
        $_.InterfaceDescription -notmatch "Wi-?Fi|Wireless|Bluetooth" })
    foreach ($adapter in $wired) {
        try {
            Disable-NetAdapterPowerManagement -Name $adapter.Name -NoRestart -ErrorAction Stop
            Write-Host ("  랜카드 전원 관리 끔: {0} ({1})" -f $adapter.Name, $adapter.InterfaceDescription)
        } catch {
            Write-Host ("  랜카드 전원 관리 건너뜀: {0} ({1})" -f $adapter.Name, $_.Exception.Message)
        }
    }
    Write-Host ""
    Write-Host "완료. 시연이 끝나면 setup_power.bat에서 2번으로 되돌릴 수 있습니다."
}

function Invoke-Restore {
    if (-not (Test-Path $BackupPath)) { throw "백업이 없습니다: $BackupPath (1번을 실행한 적이 없거나 이미 되돌림)" }
    $backup = Get-Content -Raw $BackupPath | ConvertFrom-Json
    foreach ($item in $Settings) {
        $saved = $backup.($item.Setting)
        if ($null -eq $saved) { continue }
        Set-Value $item $saved.Ac $saved.Dc
        Write-Host ("  {0}: AC {1}, 배터리 {2}로 되돌림" -f $item.Name, $saved.Ac, $saved.Dc)
    }
    Invoke-PowerCfg @("/setactive", "SCHEME_CURRENT") | Out-Null
    Remove-Item $BackupPath
    Write-Host "완료. 랜카드 전원 관리는 장치 관리자에서 직접 켜야 합니다."
}

try {
    if (-not (Test-Administrator)) { throw "관리자 권한이 필요합니다. setup_power.bat으로 실행하세요." }
    if (-not $Mode) {
        Write-Host "aesa 시연용 노트북 전원 설정"
        Write-Host "  1. 시연용으로 바꾸기 (절전·덮개·USB 절전·랜카드 절전 끄기)"
        Write-Host "  2. 원래대로 되돌리기"
        $answer = Read-Host "번호"
        switch ($answer) { "1" { $Mode = "apply" } "2" { $Mode = "restore" } default { throw "잘못된 번호입니다: $answer" } }
    }
    if ($Mode -eq "apply") { Invoke-Apply } else { Invoke-Restore }
} catch {
    Write-Host ""
    Write-Host "실패: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
