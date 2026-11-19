# aesa 보드 SD 준비 (Windows). prepare_board_sd.bat을 두 번 눌러 실행한다(관리자 권한 필요 없음).
#
# 1. 보드 스크립트를 SD FAT의 aesa 폴더에 복사한다
#      run_aesa.sh, install_board.sh, README.txt (덮어씀), board.conf (없을 때만)
#    adp, aesa.env, SHA256SUMS, COMMIT(처리부 빌드 결과)은 건드리지 않는다.
# 2. 노트북 ssh 키 %USERPROFILE%\.ssh\aesa_board_ed25519 를 만든다 (이미 있으면 그대로 쓴다)
# 3. 공개키를 SD의 aesa\authorized_keys에 넣는다 (이미 있으면 다시 넣지 않음)
# 그다음 보드에서 install_board.sh를 한 번 실행하면 보드에 키·고정 IP·sudo 규칙이 들어간다.
#
# PowerShell에서 직접: .\prepare_board_sd.ps1 -Drive E:

param([string]$Drive = "")

$ErrorActionPreference = "Stop"
$KeyName = "aesa_board_ed25519"
$CopyAlways = @("run_aesa.sh", "install_board.sh", "README.txt")
$CopyIfMissing = @("board.conf")

function Find-BoardFiles {
    foreach ($candidate in @((Join-Path $PSScriptRoot "board"), (Join-Path $PSScriptRoot "..\board"))) {
        if (Test-Path (Join-Path $candidate "install_board.sh")) { return (Resolve-Path $candidate).Path }
    }
    throw "보드 스크립트(board\install_board.sh)를 찾지 못했습니다. 배포 zip을 통째로 풀었는지 확인하세요."
}

function Find-SdFolder {
    if ($Drive) {
        $folder = Join-Path ($Drive.TrimEnd("\") + "\") "aesa"
        if (-not (Test-Path (Join-Path $folder "adp"))) { throw "$folder 에 처리부(adp)가 없습니다. SD FAT 드라이브가 맞는지 확인하세요." }
        return $folder
    }
    $found = @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { Join-Path $_.Root "aesa" } |
        Where-Object { Test-Path (Join-Path $_ "adp") })
    if ($found.Count -eq 0) { throw "처리부 SD(aesa\adp가 있는 드라이브)를 찾지 못했습니다. SD를 꽂았는지 확인하세요." }
    if ($found.Count -eq 1) { return $found[0] }
    Write-Host "처리부 SD가 여러 개 보입니다:"
    for ($index = 0; $index -lt $found.Count; $index++) { Write-Host ("  {0}. {1}" -f ($index + 1), $found[$index]) }
    $answer = Read-Host "번호"
    $number = 0
    if (-not [int]::TryParse($answer, [ref]$number) -or $number -lt 1 -or $number -gt $found.Count) { throw "잘못된 번호입니다: $answer" }
    return $found[$number - 1]
}

function Find-SshKeygen {
    $command = Get-Command ssh-keygen -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { return @($command)[0].Source }
    if ($env:WINDIR) {
        $builtin = Join-Path $env:WINDIR "System32\OpenSSH\ssh-keygen.exe"
        if (Test-Path $builtin) { return $builtin }
    }
    throw "ssh-keygen이 없습니다. 설정 > 시스템 > 선택적 기능에서 'OpenSSH 클라이언트'를 추가하세요."
}

function New-BoardKey([string]$Path) {
    if (Test-Path $Path) {
        Write-Host "ssh 키: 이미 있음 $Path"
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    $keygen = Find-SshKeygen
    # 빈 암호(-N "")를 정확히 넘기려고 인자를 한 문자열로 준다(PowerShell 버전마다 빈 인자 처리가 다름).
    $arguments = '-q -t ed25519 -N "" -C "aesa-monitor@{0}" -f "{1}"' -f $env:COMPUTERNAME, $Path
    $process = Start-Process -FilePath $keygen -ArgumentList $arguments -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0 -or -not (Test-Path "$Path.pub")) { throw "ssh 키를 만들지 못했습니다 (ssh-keygen 종료 코드 $($process.ExitCode))." }
    Write-Host "ssh 키: 새로 만듦 $Path"
}

function Write-LfText([string]$Path, [string[]]$Lines) {
    $text = ($Lines -join "`n") + "`n"
    [IO.File]::WriteAllText($Path, $text, (New-Object Text.UTF8Encoding $false))
}

try {
    $boardFiles = Find-BoardFiles
    $sd = Find-SdFolder
    Write-Host "SD: $sd"
    Write-Host "보드 스크립트: $boardFiles"
    Write-Host ""

    foreach ($name in $CopyAlways) {
        Copy-Item -Force (Join-Path $boardFiles $name) (Join-Path $sd $name)
        Write-Host "복사: $name"
    }
    foreach ($name in $CopyIfMissing) {
        $target = Join-Path $sd $name
        if (Test-Path $target) { Write-Host "그대로 둠: $name (이미 있음)" }
        else { Copy-Item (Join-Path $boardFiles $name) $target; Write-Host "복사: $name" }
    }

    $key = Join-Path $env:USERPROFILE ".ssh\$KeyName"
    New-BoardKey $key
    $publicKey = (Get-Content -Raw "$key.pub").Trim()
    $keysPath = Join-Path $sd "authorized_keys"
    $existing = @()
    if (Test-Path $keysPath) { $existing = @(Get-Content $keysPath | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
    if ($existing -contains $publicKey) {
        Write-Host "공개키: 이미 authorized_keys에 있음"
    } else {
        Write-LfText $keysPath ($existing + $publicKey)
        Write-Host "공개키: authorized_keys에 추가 ($($existing.Count + 1)개)"
    }

    Write-Host ""
    Write-Host "완료. 다음 순서:"
    Write-Host "  1) 작업 표시줄에서 SD '안전하게 제거' 후 보드에 꽂기"
    Write-Host "  2) 보드 시리얼에서 petalinux 로그인 후: sudo sh /run/media/mmcblk0p1/aesa/install_board.sh"
    Write-Host "  3) 모니터 [메뉴] -> [처리부 제어] -> [상태]"
    Write-Host "     또는: ssh -i `"$key`" petalinux@192.168.10.20 `"sudo -n aesactl status`""
} catch {
    Write-Host ""
    Write-Host "실패: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
