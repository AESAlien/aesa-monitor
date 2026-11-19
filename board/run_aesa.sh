#!/bin/sh
# AESA 데이터 처리부(adp) 실행·관리 스크립트 - Zybo Z7 PetaLinux 2022 (SD 부팅)
#
# 두 곳에서 같은 스크립트를 쓴다.
#   SD FAT:  sh <SD FAT>/aesa/run_aesa.sh <명령>        (설치 전, 시리얼에서)
#   설치 후: sudo aesactl <명령>                        (install_board.sh가 /usr/local/sbin/aesactl로 복사)
#
# 명령
#   start [시각]    IP 설정 -> SD 원본 복사·검증 -> 백그라운드 실행 -> 포트 확인
#                   시각("YYYY-MM-DD HH:MM:SS")을 주면 먼저 보드 시각을 맞춘다(로그 파일 이름이 이 시각을 쓴다).
#   stop            SIGTERM으로 종료 -> 로그를 SD FAT(aesa/logs)로 복사
#   restart [시각]
#   status          실행 여부, IP, 포트, 연결, 최근 로그
#   log             최신 adp_*.log 실시간 보기 (Ctrl+C로 나가도 처리부는 계속 실행)
#   savelog         로그만 SD FAT로 복사
#   settime 시각    보드 시각만 맞춘다
#   net             eth0에 처리부 IP만 넣는다
#   boot            부팅 때 init이 부른다: net, 그리고 board.conf의 AESA_AUTOSTART=1이면 start
#   poweroff        처리부 종료·로그 저장 후 보드를 끈다 (ext4라 전원을 바로 뽑지 않는다)
#
# 원본(SD FAT)은 실행 권한이 없으므로 ext4의 작업 폴더로 복사해서 실행한다.
# 처리부는 작업 폴더의 .env와 logs/를 상대 경로로 쓴다.
# 작업 폴더는 /opt/aesa다. petalinux 계정(ssh)이 로그를 읽을 수 있게 755/644로 둔다.

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH
export PATH
umask 022

WORK_DIR=/opt/aesa
IFACE=eth0
PREFIX=24
DEFAULT_SERVER_IP=192.168.10.20
DEFAULT_SERVER_PORT=3200
SD_DEVICE=/dev/mmcblk0p1
SD_MOUNT=/mnt/sd
PID_FILE="$WORK_DIR/adp.pid"
BIN=adp
ENV_SRC=aesa.env

say() { echo "[aesa] $*"; }
die() { echo "[aesa] 오류: $*" >&2; exit 1; }

# root가 아니면 sudo로 다시 실행한다 (IP 설정, /opt 쓰기, 시각 설정에 root 필요).
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        exec sudo sh "$0" "$@"
    fi
    die "root 권한이 필요합니다 (sudo 없음)."
fi

SELF_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)

sd_mount_point() {
    # SD FAT가 이미 마운트된 곳 (PetaLinux 자동 마운트는 보통 /run/media/mmcblk0p1).
    awk -v dev="$SD_DEVICE" '$1 == dev { print $2; exit }' /proc/mounts 2>/dev/null
}

find_src() {
    # SD FAT의 aesa 폴더를 찾는다. 스크립트가 그 폴더에 있으면 그곳, 아니면 SD FAT 마운트 위치.
    # 같은 FAT를 두 곳에 마운트하지 않도록, 마운트되어 있지 않을 때만 직접 마운트한다.
    if [ -f "$SELF_DIR/$BIN" ]; then
        echo "$SELF_DIR"
        return 0
    fi
    mount_point=$(sd_mount_point)
    if [ -z "$mount_point" ] && [ -b "$SD_DEVICE" ]; then
        mkdir -p "$SD_MOUNT" && mount -t vfat "$SD_DEVICE" "$SD_MOUNT" 2>/dev/null && mount_point=$SD_MOUNT
    fi
    [ -n "$mount_point" ] && [ -f "$mount_point/aesa/$BIN" ] || return 1
    echo "$mount_point/aesa"
}

SRC_DIR=$(find_src)

env_file() {
    # 설정 원본: SD FAT의 aesa.env, 없으면 마지막으로 복사한 작업 폴더의 .env
    if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/$ENV_SRC" ]; then
        echo "$SRC_DIR/$ENV_SRC"
    elif [ -f "$WORK_DIR/.env" ]; then
        echo "$WORK_DIR/.env"
    fi
}

env_value() {
    # KEY=VALUE 값을 읽는다 (CR 제거). 파일이나 값이 없으면 빈 문자열.
    file=$(env_file)
    [ -n "$file" ] || return 0
    sed -n "s/^$1=//p" "$file" | tr -d '\r' | head -n 1
}

conf_value() {
    # SD FAT의 board.conf (노트북에서 고칠 수 있다). 없으면 빈 문자열.
    [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/board.conf" ] || return 0
    sed -n "s/^$1=//p" "$SRC_DIR/board.conf" | tr -d '\r' | head -n 1
}

SERVER_IP=$(env_value COMM_SERVER_ADDRESS)
SERVER_PORT=$(env_value COMM_SERVER_PORT)
[ -n "$SERVER_IP" ] || SERVER_IP=$DEFAULT_SERVER_IP
[ -n "$SERVER_PORT" ] || SERVER_PORT=$DEFAULT_SERVER_PORT

need_src() {
    [ -n "$SRC_DIR" ] || die "SD FAT의 aesa 폴더를 찾지 못했습니다 ($SD_DEVICE 마운트 확인)."
}

latest_log() {
    # 처리부 로그 이름은 logs/adp_<날짜>_<시각>.log (실행할 때마다 새 파일).
    ls -t "$WORK_DIR"/logs/adp_[0-9]*.log 2>/dev/null | head -n 1
}

running_pid() {
    [ -f "$PID_FILE" ] || return 1
    pid=$(cat "$PID_FILE" 2>/dev/null)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && [ -d "/proc/$pid" ] || return 1
    echo "$pid"
}

listening() {
    netstat -ltn 2>/dev/null | grep -q "[:.]${SERVER_PORT}[[:space:]]"
}

has_ip() {
    ip -4 addr show dev "$IFACE" 2>/dev/null | grep -q "inet $SERVER_IP/"
}

stop_dhcp_client() {
    # eth0이 DHCP(udhcpc)로 설정되어 있으면, 주소를 못 받았을 때 고정 IP를 지울 수 있어 멈춘다.
    for p in /proc/[0-9]*; do
        cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
        case "$cmd" in
            *udhcpc*"$IFACE"*|*dhclient*"$IFACE"*)
                say "DHCP 클라이언트 종료: ${p#/proc/} ($cmd)"
                kill "${p#/proc/}" 2>/dev/null ;;
        esac
    done
}

setup_ip() {
    stop_dhcp_client
    ip link set "$IFACE" up || die "$IFACE를 켤 수 없습니다."
    if ! has_ip; then
        ip addr add "$SERVER_IP/$PREFIX" dev "$IFACE" || die "$IFACE에 $SERVER_IP/$PREFIX를 넣을 수 없습니다."
    fi
    has_ip || die "$IFACE에 $SERVER_IP가 없습니다."
    say "IP: $(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | tr '\n' ' ')"
}

set_time() {
    # 보드에 RTC가 없어 부팅하면 예전 시각이다. 노트북 시각을 받아 맞춘다.
    case "$1" in
        [12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]" "[0-2][0-9]:[0-5][0-9]:[0-5][0-9]) ;;
        *) die "시각 형식이 아닙니다: '$1' (YYYY-MM-DD HH:MM:SS)" ;;
    esac
    date -s "$1" >/dev/null || die "시각을 맞추지 못했습니다: $1"
    say "보드 시각: $(date '+%Y-%m-%d %H:%M:%S')"
}

copy_files() {
    need_src
    for f in "$BIN" "$ENV_SRC"; do
        [ -f "$SRC_DIR/$f" ] || die "$SRC_DIR/$f가 없습니다."
    done
    if [ -f "$SRC_DIR/SHA256SUMS" ] && command -v sha256sum >/dev/null 2>&1; then
        (cd "$SRC_DIR" && sha256sum -c SHA256SUMS >/dev/null 2>&1) \
            || die "SD 원본의 sha256이 맞지 않습니다 (SHA256SUMS). 파일이 손상되었을 수 있습니다."
        say "SD 원본 sha256 확인"
    else
        say "sha256 확인 생략 (SHA256SUMS 또는 sha256sum 없음)"
    fi
    mkdir -p "$WORK_DIR/logs" || die "$WORK_DIR를 만들 수 없습니다."
    chmod 755 "$WORK_DIR" "$WORK_DIR/logs"
    cp "$SRC_DIR/$BIN" "$WORK_DIR/$BIN" || die "실행 파일 복사 실패"
    cp "$SRC_DIR/$ENV_SRC" "$WORK_DIR/.env" || die ".env 복사 실패"
    chmod 755 "$WORK_DIR/$BIN"
    [ -f "$SRC_DIR/COMMIT" ] && cp "$SRC_DIR/COMMIT" "$WORK_DIR/COMMIT"
    sync
}

save_logs() {
    [ -d "$WORK_DIR/logs" ] || { say "저장할 로그가 없습니다."; return 0; }
    [ -n "$SRC_DIR" ] || { say "SD FAT를 찾지 못해 로그를 복사하지 않았습니다 (보드: $WORK_DIR/logs)."; return 0; }
    mkdir -p "$SRC_DIR/logs" && cp "$WORK_DIR"/logs/* "$SRC_DIR/logs/" 2>/dev/null
    sync
    say "로그를 SD FAT로 복사: $SRC_DIR/logs"
}

do_start() {
    [ -n "$1" ] && set_time "$1"
    pid=$(running_pid) && { say "이미 실행 중 (PID $pid)"; return 0; }
    setup_ip
    copy_files
    cd "$WORK_DIR" || die "$WORK_DIR로 이동 실패"
    say "처리부 시작 ($(head -n 1 COMMIT 2>/dev/null))"
    nohup "./$BIN" >> logs/console.out 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    i=0
    while [ $i -lt 10 ]; do
        sleep 1
        pid=$(running_pid) || { say "처리부가 바로 종료되었습니다. 최근 로그:"; tail -n 15 logs/console.out "$(latest_log)" 2>/dev/null; exit 1; }
        if listening; then
            say "실행 중: PID $pid, $SERVER_IP:$SERVER_PORT 대기"
            return 0
        fi
        i=$((i + 1))
    done
    say "주의: 10초가 지나도 $SERVER_PORT 포트가 열리지 않았습니다. status로 확인하세요."
}

do_stop() {
    pid=$(running_pid) || { say "실행 중이 아닙니다."; rm -f "$PID_FILE"; return 0; }
    say "종료 요청 (SIGTERM, PID $pid)"
    kill -TERM "$pid"
    i=0
    while [ $i -lt 10 ] && kill -0 "$pid" 2>/dev/null; do sleep 1; i=$((i + 1)); done
    if kill -0 "$pid" 2>/dev/null; then
        say "10초 안에 끝나지 않아 강제 종료 (SIGKILL)"
        kill -KILL "$pid" 2>/dev/null
    fi
    rm -f "$PID_FILE"
    save_logs
}

do_status() {
    if pid=$(running_pid); then say "처리부: 실행 중 (PID $pid)"; else say "처리부: 실행 중 아님"; fi
    if listening; then say "포트 $SERVER_PORT: 열림"; else say "포트 $SERVER_PORT: 닫힘"; fi
    say "IP($IFACE): $(ip -4 -o addr show dev "$IFACE" 2>/dev/null | awk '{print $4}' | tr '\n' ' ')"
    say "보드 시각: $(date '+%Y-%m-%d %H:%M:%S')"
    say "빌드: $(head -n 1 "$WORK_DIR/COMMIT" 2>/dev/null || echo '(없음)')"
    say "연결:"; netstat -tn 2>/dev/null | grep "[:.]${SERVER_PORT}[[:space:]]" || echo "  (없음)"
    log=$(latest_log)
    [ -n "$log" ] && { say "$(basename "$log") 마지막 10줄:"; tail -n 10 "$log"; }
    return 0
}

do_boot() {
    setup_ip
    case "$(conf_value AESA_AUTOSTART)" in
        1|yes|on|true) do_start ;;
        *) say "자동 실행 꺼짐 (board.conf AESA_AUTOSTART=0). 모니터 [처리부 시작] 또는 aesactl start" ;;
    esac
}

do_poweroff() {
    running_pid >/dev/null && do_stop
    sync
    say "보드를 끕니다. 10초쯤 기다린 뒤 전원을 빼세요."
    (sleep 1; poweroff) >/dev/null 2>&1 &
}

case "$1" in
    start)    do_start "$2" ;;
    stop)     do_stop ;;
    restart)  do_stop; do_start "$2" ;;
    status)   do_status ;;
    log)      log=$(latest_log); [ -n "$log" ] || die "로그가 없습니다."; tail -f "$log" ;;
    savelog)  save_logs ;;
    settime)  [ -n "$2" ] || die "사용법: settime 'YYYY-MM-DD HH:MM:SS'"; set_time "$2" ;;
    net)      setup_ip ;;
    boot)     do_boot ;;
    poweroff) do_poweroff ;;
    *) echo "사용법: $0 {start [시각]|stop|restart [시각]|status|log|savelog|settime 시각|net|boot|poweroff}"; exit 1 ;;
esac
