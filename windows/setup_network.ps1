# aesa 시연용 노트북 IP 고정 설정 (Windows). setup_network.bat을 두 번 눌러 실행한다(관리자 권한 필요).
#
# 랜카드 하나에 고정 IPv4 주소를 넣는다. Windows가 설정을 저장하므로 재부팅해도 유지된다.
#   monitor : 192.168.10.30            모니터 노트북만 (노트북 2~3대 구성)
#   all     : 192.168.10.30, .10, .100 모니터·운용통제기·모의기를 이 노트북 한 대에서 실행 (노트북 1대 구성)
#   dhcp    : 일반 용도. 고정 주소를 지우고 자동(DHCP)으로 되돌림 (인터넷 랜선을 꽂아 쓸 때)
# 고른 랜카드가 Windows 네트워크 브리지에 묶여 있으면 IP는 그 랜카드가 아니라 브리지에 넣는다
# (브리지에 묶인 랜카드에는 IPv4 설정이 없다). 브리지에 묶인 랜카드 어느 쪽에 선을 꽂아도 같은 주소가 된다.
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

function Test-HasIPv4([string]$Name) {
    return $null -ne (Get-NetIPInterface -InterfaceAlias $Name -AddressFamily IPv4 -ErrorAction SilentlyContinue)
}

function Get-Bridge {
    # Windows 네트워크 브리지 장치(이름은 보통 "네트워크 브리지", 설명은 Microsoft Network Adapter Multiplexor Driver).
    return @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -like "*Multiplexor*" })
}

function Test-BridgeMember([string]$Name) {
    # 브리지에 묶인 랜카드는 "멀티플렉서 프로토콜"(ms_implat)이 켜지고 IPv4 설정이 없다.
    $binding = Get-NetAdapterBinding -Name $Name -ComponentID ms_implat -ErrorAction SilentlyContinue
    return ($binding -and $binding.Enabled -and -not (Test-HasIPv4 $Name))
}

function Resolve-IpTarget([string]$Name) {
    # IP를 넣을 곳: 그 랜카드, 또는 그 랜카드가 묶인 네트워크 브리지.
    if (Test-HasIPv4 $Name) { return $Name }
    if (Test-BridgeMember $Name) {
        $bridges = @(Get-Bridge)
        if ($bridges.Count -eq 1) {
            Write-Host ("[{0}]는 네트워크 브리지에 묶여 있어 IP를 [{1}]에 넣습니다." -f $Name, $bridges[0].Name)
            return $bridges[0].Name
        }
        throw ("[{0}]는 브리지(또는 NIC 팀)에 묶여 있는데 브리지 장치가 {1}개라 어디에 넣을지 알 수 없습니다. " +
            "-Adapter에 브리지 이름을 직접 주세요.") -f $Name, $bridges.Count
    }
    throw "[$Name]에 IPv4 설정이 없습니다(IPv4 프로토콜이 꺼져 있을 수 있음). 랜카드 속성에서 'TCP/IPv4'를 켜세요."
}

function Select-Adapter {
    $adapters = @(Get-NetAdapter | Where-Object { $_.HardwareInterface -and $_.InterfaceDescription -notmatch "Wi-?Fi|Wireless|Bluetooth|VMware|VirtualBox|Hyper-V" } |
        Sort-Object Name)
    if ($adapters.Count -eq 0) { throw "유선 랜카드를 찾지 못했습니다. -Adapter로 이름을 직접 주세요." }
    $bridges = @(Get-Bridge)
    Write-Host "유선 랜카드 (Zynq·스위치와 연결된 것을 고르세요):"
    for ($index = 0; $index -lt $adapters.Count; $index++) {
        $item = $adapters[$index]
        $note = ""
        $source = $item.Name
        if ((Test-BridgeMember $item.Name) -and $bridges.Count -eq 1) {
            $note = "  (브리지 [{0}]에 묶임)" -f $bridges[0].Name
            $source = $bridges[0].Name
        }
        $addresses = (Get-NetIPAddress -InterfaceAlias $source -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            ForEach-Object { $_.IPAddress }) -join ", "
        Write-Host ("  {0}. {1}  [{2}]  {3}  주소: {4}{5}" -f ($index + 1), $item.Name, $item.Status, $item.InterfaceDescription, $addresses, $note)
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
    Write-Host "  3. 일반 용도: 자동(DHCP)으로 되돌리기 (인터넷 랜선)"
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
    $Adapter = Resolve-IpTarget $Adapter   # 브리지에 묶인 랜카드면 브리지

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
        Write-Host "      (브리지면 묶인 랜카드 중 연결된 쪽. 둘 다 연결되어 있으면 모니터가 보드 쪽 랜카드를 직접 적으라고 안내합니다)"
    } else {
        Write-Host "일반 용도: 인터넷 랜선을 꽂으면 주소를 받습니다. 시연할 때는 다시 1번(또는 2번)을 실행하세요."
        Write-Host "주의: 브리지 한쪽에 인터넷 선, 다른 쪽에 시연 장비를 꽂으면 두 네트워크가 이어집니다."
    }
} catch {
    Write-Host ""
    Write-Host "실패: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
