# aesa 시연용 노트북 IP 고정 설정 (Windows). setup_network.bat을 두 번 눌러 실행한다(관리자 권한 필요).
#
# 랜카드 하나에 고정 IPv4 주소를 넣는다. Windows가 설정을 저장하므로 재부팅해도 유지된다.
#   monitor : 192.168.10.30            모니터 노트북만 (노트북 2~3대 구성)
#   all     : 192.168.10.30, .10, .100 모니터·운용통제기·모의기를 이 노트북 한 대에서 실행 (노트북 1대 구성)
#   dhcp    : 고정 주소를 지우고 자동(DHCP)으로 되돌림
# 처리부(Zynq) 192.168.10.20은 노트북에 넣지 않는다. 게이트웨이·DNS는 설정하지 않는다(인터넷은 Wi-Fi로).
#
# PowerShell에서 직접: .\setup_network.ps1 -Adapter "이더넷 2" -Mode all

param(
    [string]$Adapter = "",
    [ValidateSet("", "monitor", "all", "dhcp")]
    [string]$Mode = ""
)

$ErrorActionPreference = "Stop"
$Prefix = 24
$Plans = @{
    "monitor" = @("192.168.10.30")
    "all"     = @("192.168.10.30", "192.168.10.10", "192.168.10.100")
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Show-Addresses([string]$Name) {
    Get-NetIPAddress -InterfaceAlias $Name -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Sort-Object IPAddress |
        Format-Table -AutoSize IPAddress, PrefixLength, PrefixOrigin, SkipAsSource | Out-String -Width 120
}

function Select-Adapter {
    $adapters = @(Get-NetAdapter | Where-Object { $_.HardwareInterface -and $_.InterfaceDescription -notmatch "Wi-?Fi|Wireless|Bluetooth|VMware|VirtualBox|Hyper-V" } |
        Sort-Object Name)
    if ($adapters.Count -eq 0) { throw "유선 랜카드를 찾지 못했습니다. -Adapter로 이름을 직접 주세요." }
    Write-Host "유선 랜카드 (Zynq·스위치와 연결된 것을 고르세요):"
    for ($index = 0; $index -lt $adapters.Count; $index++) {
        $item = $adapters[$index]
        $addresses = (Get-NetIPAddress -InterfaceIndex $item.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            ForEach-Object { $_.IPAddress }) -join ", "
        Write-Host ("  {0}. {1}  [{2}]  {3}  주소: {4}" -f ($index + 1), $item.Name, $item.Status, $item.InterfaceDescription, $addresses)
    }
    $answer = Read-Host "번호"
    $number = 0
    if (-not [int]::TryParse($answer, [ref]$number) -or $number -lt 1 -or $number -gt $adapters.Count) {
        throw "잘못된 번호입니다: $answer"
    }
    return $adapters[$number - 1].Name
}

function Select-Mode {
    Write-Host ""
    Write-Host "구성:"
    Write-Host "  1. 모니터 노트북만           192.168.10.30"
    Write-Host "  2. 노트북 1대 (모니터·운용통제기·모의기)  192.168.10.30, .10, .100"
    Write-Host "  3. 자동(DHCP)으로 되돌리기"
    switch (Read-Host "번호") {
        "1" { return "monitor" }
        "2" { return "all" }
        "3" { return "dhcp" }
        default { throw "잘못된 번호입니다." }
    }
}

try {
    if (-not (Test-Administrator)) { throw "관리자 권한이 필요합니다. setup_network.bat으로 실행하세요." }
    if (-not $Adapter) { $Adapter = Select-Adapter }
    if (-not $Mode) { $Mode = Select-Mode }
    $null = Get-NetAdapter -Name $Adapter  # 없는 이름이면 여기서 멈춘다

    Write-Host ""
    Write-Host "바꾸기 전 [$Adapter]:"
    Show-Addresses $Adapter

    # 이 랜카드의 기존 IPv4 주소와 기본 게이트웨이를 지운다(이전 고정 주소·DHCP 주소 모두).
    Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetRoute -InterfaceAlias $Adapter -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue

    if ($Mode -eq "dhcp") {
        Set-NetIPInterface -InterfaceAlias $Adapter -AddressFamily IPv4 -Dhcp Enabled
        Write-Host "자동(DHCP)으로 되돌렸습니다."
    } else {
        Set-NetIPInterface -InterfaceAlias $Adapter -AddressFamily IPv4 -Dhcp Disabled
        $first = $true
        foreach ($address in $Plans[$Mode]) {
            # 첫 주소(.30, 모니터)만 이 PC가 먼저 보내는 연결의 출발 주소로 쓴다.
            # .10·.100은 운용통제기·모의기 프로그램이 직접 그 주소로 연결할 때만 쓰인다(SkipAsSource).
            New-NetIPAddress -InterfaceAlias $Adapter -IPAddress $address -PrefixLength $Prefix `
                -SkipAsSource (-not $first) | Out-Null
            $first = $false
        }
        Write-Host "고정 주소를 넣었습니다."
    }

    Write-Host ""
    Write-Host "바꾼 뒤 [$Adapter]:"
    Show-Addresses $Adapter
    if ($Mode -ne "dhcp") {
        Write-Host "확인: 보드를 켠 뒤 'ping 192.168.10.20'. 모니터는 monitor.env의 {interface}로 이 랜카드를 자동으로 고릅니다."
    }
} catch {
    Write-Host ""
    Write-Host "실패: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
