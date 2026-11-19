#!/bin/sh
# AESA 데이터 처리부(adp) 실행·관리 스크립트 - Zybo Z7 PetaLinux 2022 (SD 부팅)
#
# 두 곳에서 같은 스크립트를 쓴다.
#   SD FAT:  sh <SD FAT>/aesa/run_aesa.sh <명령>        (설치 전, 시리얼에서)
#   설치 후: sudo /usr/local/sbin/aesactl <명령>        (install_board.sh가 복사)
#
# 명령
#   start [시각]    IP 설정 -> SD 원본 복사·검증 -> 백그라운드 실행 -> 포트 확인
#                   시각("YYYY-MM-DD HH:MM:SS")을 주면 먼저 보드 시각을 맞춘다(로그 파일 이름이 이 시각을 쓴다).
#                   PORT_WAIT_SECONDS 안에 포트가 열리지 않으면 실패(종료 코드 1). 처리부 프로세스는 남겨 둔다.
#                   이미 실행 중이어도 포트가 열려 있는지 확인하고, 끝내 닫혀 있으면 실패다.
#   stop            SIGTERM으로 종료 -> 로그를 SD FAT(aesa/logs)로 복사
#                   처리부가 이미 끝나 있어도(비정상 종료) 로그는 복사한다. 복사에 실패하면 종료 코드 1.
#   restart [시각]  stop의 로그 복사가 실패해도 다시 시작하고, 끝에 실패(종료 코드 1)로 알린다.
#   status          실행 여부, IP, 포트, 연결, 최근 로그
#   log             최신 adp_*.log 실시간 보기 (Ctrl+C로 나가도 처리부는 계속 실행)
#   savelog         로그만 SD FAT로 복사 (SD에 같은 크기로 이미 있는 파일은 건너뜀)
#                   로그를 복사하는 명령(stop·restart·poweroff·savelog)은 ssh가 끊겨도 끝까지 복사하고,
#                   결과는 status의 "마지막 로그 저장"에 남는다.
#   settime 시각    보드 시각만 맞춘다
#   net             eth0에 처리부 IP만 넣는다
#   boot            부팅 때 init이 부른다: net, 그리고 board.conf의 AESA_AUTOSTART=1이면 start
#   poweroff        처리부 종료·로그 저장 후 보드를 끈다 (ext4라 전원을 바로 뽑지 않는다)
#   (start·stop·restart·poweroff·savelog·boot는 잠금으로 한 번에 하나만 실행한다. 겹치면 나중 명령이 실패)
#                   로그 복사가 실패해도 보드는 끈다(로그는 ext4의 /opt/aesa/logs에 남는다). 이때 종료 코드 1.
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
PID_FILE="$WORK_DIR/adp.pid"   # "PID 시작시각 부팅ID" (시작시각: /proc/PID/stat 22번째 칸, 부팅 후 클럭 틱)
PORT_WAIT_SECONDS=15   # 처리부가 서버 포트를 열 때까지 기다리는 시간
STOP_WAIT_SECONDS=10   # SIGTERM 뒤 처리부가 끝나기를 기다리는 시간
SAVE_STATE="$WORK_DIR/savelog.state"   # 마지막 로그 저장 결과 (status에 보임)
LOCK_LINK=/run/aesactl.lock-owner     # 바꾸는 명령은 한 번에 하나만 (심볼릭 링크, 재부팅하면 사라지는 /run)
BIN=adp
ENV_SRC=aesa.env

say() { echo "[aesa] $*"; }
fail() { echo "[aesa] 오류: $*" >&2; }
die() { fail "$@"; exit 1; }

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

proc_field() {
    # proc_field PID N : /proc/PID/stat에서 "PID (이름) " 뒤의 N번째 칸 (1=상태, 20=시작시각)
    sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | cut -d' ' -f"$2"
}

boot_id() {
    # 부팅마다 바뀌는 ID. 비정상 재부팅 뒤 남은 PID 파일을 알아보는 데 쓴다.
    cat /proc/sys/kernel/random/boot_id 2>/dev/null
}

saved_pid() {
    # PID 파일의 PID (검증하지 않음, 메시지용)
    [ -f "$PID_FILE" ] && cut -d' ' -f1 "$PID_FILE" 2>/dev/null
}

running_pid() {
    # PID 파일의 처리부가 지금도 살아 있으면 PID를 출력한다.
    # 처리부가 죽거나 보드가 재부팅된 뒤 같은 PID를 다른 프로세스가 받았을 수 있으므로 셋을 모두 확인한다.
    #   1. 실행 파일이 작업 폴더의 adp인지 (항상)
    #   2. 시작시각이 저장한 값과 같은지 (PID 파일에 있을 때)
    #   3. 부팅 ID가 지금과 같은지 (PID 파일에 있을 때). 시작시각은 부팅마다 0부터 다시 센다.
    [ -f "$PID_FILE" ] || return 1
    read -r pid started booted < "$PID_FILE" || [ -n "$pid" ] || return 1
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    [ -d "/proc/$pid" ] || return 1
    # 끝났지만 아직 거둬지지 않은 프로세스(좀비, 상태 Z)는 실행 중이 아니다.
    [ "$(proc_field "$pid" 1)" != "Z" ] || return 1
    case "$(readlink "/proc/$pid/exe" 2>/dev/null)" in
        "$WORK_DIR/$BIN"|"$WORK_DIR/$BIN (deleted)") ;;
        *) return 1 ;;
    esac
    if [ -n "$started" ]; then
        [ "$(proc_field "$pid" 20)" = "$started" ] || return 1
    fi
    if [ -n "$booted" ]; then
        [ "$(boot_id)" = "$booted" ] || return 1
    fi
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

save_state() {
    # 마지막 로그 저장 결과를 남긴다(status가 보여 준다). ssh가 먼저 끊겨도 결과를 나중에 확인할 수 있게.
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" > "$SAVE_STATE" 2>/dev/null
}

save_logs() {
    # 작업 폴더의 로그를 SD FAT로 복사한다. SD에 같은 크기로 이미 있는 파일은 건너뛴다(로그는 덧붙이기만 한다).
    # 하나라도 복사하지 못하면 1을 돌려준다(로그는 보드에 그대로 남는다).
    set -- "$WORK_DIR"/logs/*
    [ -e "$1" ] || { say "저장할 로그가 없습니다."; return 0; }
    if [ -z "$SRC_DIR" ]; then
        save_state "실패: SD FAT를 찾지 못함"
        fail "SD FAT를 찾지 못해 로그를 복사하지 못했습니다 (보드: $WORK_DIR/logs)."
        return 1
    fi
    if ! mkdir -p "$SRC_DIR/logs" 2>/dev/null; then
        save_state "실패: SD FAT에 logs 폴더를 만들지 못함"
        fail "SD FAT에 logs 폴더를 만들지 못했습니다: $SRC_DIR/logs (보드: $WORK_DIR/logs)"
        return 1
    fi
    save_state "진행 중: 로그 $#개 확인·복사"
    say "로그 $#개를 SD FAT로 복사하는 중 (이미 같은 크기로 있는 파일은 건너뜀)..."
    copied=0
    same=0
    for file in "$@"; do
        target="$SRC_DIR/logs/$(basename "$file")"
        if [ -f "$target" ] && [ "$(wc -c < "$file")" = "$(wc -c < "$target")" ]; then
            same=$((same + 1))
            continue
        fi
        if ! cp "$file" "$target"; then
            sync
            save_state "실패: $(basename "$file") 복사 실패 (복사 $copied개 뒤)"
            fail "로그를 SD FAT로 복사하지 못했습니다 (SD가 가득 찼거나 읽기 전용일 수 있음). 보드: $WORK_DIR/logs"
            return 1
        fi
        copied=$((copied + 1))
    done
    if ! sync; then
        save_state "실패: SD FAT 쓰기(sync) 실패"
        fail "SD FAT 쓰기(sync)에 실패했습니다. 보드: $WORK_DIR/logs"
        return 1
    fi
    save_state "완료: 복사 ${copied}개, 이미 같음 ${same}개"
    say "로그 복사 완료: ${copied}개 복사, ${same}개는 이미 같음 → $SRC_DIR/logs"
}

wait_for_port() {
    # wait_for_port PID : 처리부가 서버 포트를 열 때까지 PORT_WAIT_SECONDS 기다린다.
    # 프로세스가 살아 있어도 포트가 끝내 닫혀 있으면(멈춤·설정 오류) 실패로 끝낸다.
    # 성공으로 알리면 접속을 받지 못하는 처리부로 실험을 시작해 버린다.
    i=0
    while :; do
        if listening; then
            say "실행 중: PID $1, $SERVER_IP:$SERVER_PORT 대기"
            return 0
        fi
        [ $i -lt $PORT_WAIT_SECONDS ] || break
        sleep 1
        i=$((i + 1))
        running_pid >/dev/null || { fail "처리부가 종료되었습니다. 최근 로그:"; tail -n 15 "$WORK_DIR/logs/console.out" "$(latest_log)" >&2 2>/dev/null; exit 1; }
    done
    tail -n 15 "$WORK_DIR/logs/console.out" "$(latest_log)" >&2 2>/dev/null
    die "${PORT_WAIT_SECONDS}초가 지나도 $SERVER_PORT 포트가 열리지 않았습니다. 처리부(PID $1)는 남아 있습니다. 위 로그를 확인하고 stop 후 다시 시작하세요."
}

do_start() {
    [ -n "$1" ] && set_time "$1"
    if pid=$(running_pid); then
        # 이미 떠 있는 처리부도 포트가 열려 있어야 성공이다(이전 start가 시간 초과로 남긴 처리부일 수 있음).
        say "이미 실행 중 (PID $pid)"
        wait_for_port "$pid"
        return 0
    fi
    setup_ip
    copy_files
    cd "$WORK_DIR" || die "$WORK_DIR로 이동 실패"
    say "처리부 시작 ($(head -n 1 COMMIT 2>/dev/null))"
    nohup "./$BIN" >> logs/console.out 2>&1 < /dev/null &
    pid=$!
    echo "$pid $(proc_field "$pid" 20) $(boot_id)" > "$PID_FILE"
    sleep 1
    running_pid >/dev/null || { fail "처리부가 바로 종료되었습니다. 최근 로그:"; tail -n 15 logs/console.out "$(latest_log)" >&2 2>/dev/null; exit 1; }
    wait_for_port "$pid"
}

do_stop() {
    if pid=$(running_pid); then
        say "종료 요청 (SIGTERM, PID $pid)"
        kill -TERM "$pid"
        i=0
        # 기다리는 동안에도 같은 프로세스인지(시작시각) 확인해, 끝난 뒤 PID를 받은 다른 프로세스를 죽이지 않는다.
        while [ $i -lt $STOP_WAIT_SECONDS ] && running_pid >/dev/null; do sleep 1; i=$((i + 1)); done
        if running_pid >/dev/null; then
            say "${STOP_WAIT_SECONDS}초 안에 끝나지 않아 강제 종료 (SIGKILL)"
            kill -KILL "$pid" 2>/dev/null
        fi
    elif [ -f "$PID_FILE" ]; then
        # 처리부가 먼저 끝났다(비정상 종료 등). 이 실행의 로그가 가장 필요하므로 그래도 복사한다.
        say "처리부가 이미 종료되어 있습니다 (PID 파일: $(saved_pid)). 로그는 복사합니다."
    else
        say "실행 중이 아닙니다."
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
    say "마지막 로그 저장: $(cat "$SAVE_STATE" 2>/dev/null || echo '(없음)')"
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
    # 로그 복사가 실패해도 끈다. 끄지 않으면 전원을 그냥 뽑게 되어 ext4가 더 위험하다. 로그는 /opt/aesa/logs에 남는다.
    do_stop
    status=$?
    sync
    say "보드를 끕니다. 10초쯤 기다린 뒤 전원을 빼세요."
    (sleep 1; poweroff) >/dev/null 2>&1 &
    [ $status -eq 0 ] || die "로그를 SD FAT로 복사하지 못한 채 보드를 끕니다. 다음 부팅 후 savelog로 다시 복사하세요."
}

do_restart() {
    do_stop
    status=$?
    do_start "$1"
    [ $status -eq 0 ] || die "처리부는 다시 시작했지만 이전 실행의 로그를 SD FAT로 복사하지 못했습니다. 보드: $WORK_DIR/logs"
}

acquire_lock() {
    # 처리부를 바꾸는 명령(start·stop·restart·poweroff·savelog·boot)은 한 번에 하나만 실행한다.
    # 모니터 창을 닫았다 다시 열거나 두 노트북에서 동시에 눌러도, 앞 명령이 끝나기 전에는 거부한다.
    # 잠금은 심볼릭 링크 하나다. 링크 내용("PID 명령")이 주인이고, ln -s는 링크와 내용을 한 번에(원자적으로)
    # 만든다. 그래서 '잠금은 있는데 주인이 아직 비어 있는' 순간이 없다.
    # 주인 프로세스가 이미 없으면(강제 종료 등) 그 잠금을 지우고 다시 시도한다. 지우기 직전에 내용이
    # 그대로인지 한 번 더 본다(그사이 다른 명령이 새로 잡은 잠금은 지우지 않는다).
    [ -d "$(dirname "$LOCK_LINK")" ] || LOCK_LINK=/tmp/aesactl.lock-owner
    LOCK_OWNER="$$ $*"
    tries=0
    while ! ln -s "$LOCK_OWNER" "$LOCK_LINK" 2>/dev/null; do
        holder=$(readlink "$LOCK_LINK" 2>/dev/null)
        holder_pid=${holder%% *}
        if [ -n "$holder" ] && kill -0 "$holder_pid" 2>/dev/null; then
            die "다른 aesactl 명령이 실행 중입니다 (${holder#* }, PID $holder_pid). 끝난 뒤 다시 하세요."
        fi
        if [ -n "$holder" ] && [ "$(readlink "$LOCK_LINK" 2>/dev/null)" = "$holder" ]; then
            rm -f "$LOCK_LINK"
        fi
        tries=$((tries + 1))
        [ $tries -lt 5 ] || die "잠금($LOCK_LINK)을 얻지 못했습니다."
    done
    trap release_lock EXIT
}

release_lock() {
    # 내 잠금일 때만 지운다.
    [ "$(readlink "$LOCK_LINK" 2>/dev/null)" = "$LOCK_OWNER" ] && rm -f "$LOCK_LINK"
    return 0
}

case "$1" in
    start|stop|restart|poweroff|savelog|boot) acquire_lock "$1" ;;
esac

# 로그를 복사하는 명령은 ssh가 먼저 끊겨도(모니터 제한 시간) 끝까지 복사하고 결과를 SAVE_STATE에 남긴다.
case "$1" in
    stop|restart|poweroff|savelog) trap '' HUP PIPE ;;
esac

case "$1" in
    start)    do_start "$2" ;;
    stop)     do_stop || exit 1 ;;
    restart)  do_restart "$2" ;;
    status)   do_status ;;
    log)      log=$(latest_log); [ -n "$log" ] || die "로그가 없습니다."; tail -f "$log" ;;
    savelog)  save_logs || exit 1 ;;
    settime)  [ -n "$2" ] || die "사용법: settime 'YYYY-MM-DD HH:MM:SS'"; set_time "$2" ;;
    net)      setup_ip ;;
    boot)     do_boot ;;
    poweroff) do_poweroff ;;
    *) echo "사용법: $0 {start [시각]|stop|restart [시각]|status|log|savelog|settime 시각|net|boot|poweroff}"; exit 1 ;;
esac
