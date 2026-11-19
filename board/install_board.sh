#!/bin/sh
# AESA 보드 한 번 설정 - Zybo Z7 PetaLinux 2022 (SD 부팅)
#
# 하는 일 (모두 SD의 ext4 루트 파일 시스템에 남는다)
#   1. /usr/local/sbin/aesactl   SD FAT의 run_aesa.sh 사본 (start/stop/status/settime/poweroff ...)
#   2. 부팅 때 eth0 고정 IP       /etc/network/interfaces의 eth0를 static으로 (원본은 .aesa-orig로 보관)
#                                 + init 스크립트가 부팅 끝에 aesactl boot 실행
#                                   (board.conf의 AESA_AUTOSTART=1이면 처리부도 시작)
#   3. 종료 때 처리부 정지·로그 저장 (poweroff/reboot 때 aesactl stop)
#   4. sudo 규칙                  petalinux 계정이 비밀번호 없이 aesactl만 root로 실행
#   5. ssh 공개키                 SD FAT의 aesa/authorized_keys를 petalinux의 ~/.ssh/authorized_keys에 추가
#   6. 점검                       sudo, dropbear, 계정 잠금·비밀번호 상태를 알려 준다
#
# 실행 방법 (둘 중 하나)
#   보드(시리얼 콘솔)에서:   sudo sh /run/media/mmcblk0p1/aesa/install_board.sh
#                            (root로 로그인했다면 sudo 없이. SD FAT 위치는 mount | grep mmcblk0p1)
#   리눅스 PC에서 SD를 꽂고: sudo sh <FAT 마운트>/aesa/install_board.sh --root <ext4 마운트>
#                            예) sudo sh /media/$USER/BOOT/aesa/install_board.sh --root /media/$USER/rootfs
#
# 여러 번 실행해도 된다(같은 결과). 되돌리기: --uninstall

set -u
umask 022

ACCOUNT=${AESA_ACCOUNT:-petalinux}
ROOT=""
UNINSTALL=0
SRC_DIR=$(cd "$(dirname "$0")" && pwd)

say() { echo "[install] $*"; }
warn() { echo "[install] 주의: $*"; WARNINGS=$((WARNINGS + 1)); }
die() { echo "[install] 오류: $*" >&2; exit 1; }
WARNINGS=0

while [ $# -gt 0 ]; do
    case "$1" in
        --root) [ $# -ge 2 ] || die "--root 다음에 ext4 루트 마운트 위치를 주세요."; ROOT=${2%/}; shift 2 ;;
        --account) [ $# -ge 2 ] || die "--account 다음에 계정 이름을 주세요."; ACCOUNT=$2; shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) die "알 수 없는 인자: $1 (--help)" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "root 권한이 필요합니다. sudo sh $0 ... 로 실행하세요."
[ -f "$ROOT/etc/passwd" ] || die "$ROOT/etc/passwd가 없습니다. --root에 SD의 ext4(두 번째) 파티션 마운트 위치를 주세요."
if [ -z "$ROOT" ]; then
    case "$(uname -m)" in
        arm*) ;;
        *) die "이 PC는 보드가 아닙니다 ($(uname -m)). 리눅스 PC에서는 --root <SD ext4 마운트>를 주세요." ;;
    esac
fi

if [ -n "$ROOT" ]; then MODE="오프라인(--root $ROOT)"; else MODE="보드"; fi
say "모드: $MODE, 계정: $ACCOUNT, SD FAT: $SRC_DIR"

# --- 공통 ---------------------------------------------------------------------

env_value() {
    sed -n "s/^$1=//p" "$SRC_DIR/aesa.env" 2>/dev/null | tr -d '\r' | head -n 1
}

passwd_field() {
    # passwd_field 계정 번호 : /etc/passwd의 n번째 칸
    awk -F: -v user="$1" -v n="$2" '$1 == user { print $n; exit }' "$ROOT/etc/passwd"
}

uses_systemd() {
    target=$(readlink "$ROOT/sbin/init" 2>/dev/null || true)
    case "$target" in *systemd*) return 0 ;; esac
    [ -z "$ROOT" ] && [ -d /run/systemd/system ] && return 0
    return 1
}

link() {
    # link 대상 링크이름 : 이미 같으면 그대로
    [ "$(readlink "$2" 2>/dev/null || true)" = "$1" ] && return 0
    rm -f "$2" && ln -s "$1" "$2"
}

INIT_SCRIPT="$ROOT/etc/init.d/aesa"
RC_LINKS="rc2.d/S99aesa rc3.d/S99aesa rc4.d/S99aesa rc5.d/S99aesa rc0.d/K01aesa rc6.d/K01aesa"
UNIT="$ROOT/etc/systemd/system/aesa.service"
UNIT_LINK="$ROOT/etc/systemd/system/multi-user.target.wants/aesa.service"
SUDOERS="$ROOT/etc/sudoers.d/aesa"
INTERFACES="$ROOT/etc/network/interfaces"
NETWORKD="$ROOT/etc/systemd/network/10-aesa-eth0.network"

# --- 되돌리기 -----------------------------------------------------------------

if [ "$UNINSTALL" -eq 1 ]; then
    rm -f "$ROOT/usr/local/sbin/aesactl" "$INIT_SCRIPT" "$UNIT" "$UNIT_LINK" "$SUDOERS" "$NETWORKD"
    for l in $RC_LINKS; do rm -f "$ROOT/etc/$l"; done
    if [ -f "$INTERFACES.aesa-orig" ]; then
        mv "$INTERFACES.aesa-orig" "$INTERFACES" && say "원래 $INTERFACES 복원"
    fi
    sync
    say "제거 완료. ssh 공개키(~$ACCOUNT/.ssh/authorized_keys)와 /opt/aesa는 그대로 두었습니다."
    exit 0
fi

# --- 0. 확인 ------------------------------------------------------------------

[ -f "$SRC_DIR/run_aesa.sh" ] || die "$SRC_DIR/run_aesa.sh가 없습니다. SD FAT의 aesa 폴더에서 실행하세요."
SERVER_IP=$(env_value COMM_SERVER_ADDRESS)
[ -n "$SERVER_IP" ] || { SERVER_IP=192.168.10.20; warn "aesa.env에 COMM_SERVER_ADDRESS가 없어 $SERVER_IP를 씁니다."; }

# --- 1. aesactl ---------------------------------------------------------------

mkdir -p "$ROOT/usr/local/sbin" "$ROOT/opt/aesa/logs"
chmod 755 "$ROOT/opt/aesa" "$ROOT/opt/aesa/logs"
tr -d '\r' < "$SRC_DIR/run_aesa.sh" > "$ROOT/usr/local/sbin/aesactl"
chown 0:0 "$ROOT/usr/local/sbin/aesactl"
chmod 755 "$ROOT/usr/local/sbin/aesactl"
say "1. /usr/local/sbin/aesactl 설치"

# --- 2. 부팅 때 고정 IP ---------------------------------------------------------

if uses_systemd; then
    mkdir -p "$(dirname "$NETWORKD")"
    cat > "$NETWORKD" <<EOF
# install_board.sh가 만듦: 처리부 고정 IP
[Match]
Name=eth0

[Network]
Address=$SERVER_IP/24
EOF
    say "2. systemd-networkd: eth0 = $SERVER_IP/24 ($NETWORKD)"
elif [ -f "$INTERFACES" ]; then
    [ -f "$INTERFACES.aesa-orig" ] || cp -p "$INTERFACES" "$INTERFACES.aesa-orig"
    # eth0 블록(iface eth0 줄과 뒤따르는 들여쓴 줄)을 static으로 바꾸고, auto eth0가 없으면 넣는다.
    awk -v ip="$SERVER_IP" '
        /^[ \t]*iface[ \t]+eth0[ \t]/ {
            print "iface eth0 inet static"
            print "\taddress " ip
            print "\tnetmask 255.255.255.0"
            skip = 1; found = 1; next
        }
        skip && /^[ \t]+[^ \t]/ { next }
        { skip = 0; print }
        /^[ \t]*auto[ \t].*[ \t]eth0([ \t]|$)/ || /^[ \t]*auto[ \t]+eth0([ \t]|$)/ { auto = 1 }
        END {
            if (!found) {
                print ""
                if (!auto) print "auto eth0"
                print "iface eth0 inet static"
                print "\taddress " ip
                print "\tnetmask 255.255.255.0"
            }
        }' "$INTERFACES.aesa-orig" > "$INTERFACES.aesa-new"
    if ! grep -Eq '^[[:space:]]*(auto|allow-hotplug)[[:space:]].*eth0' "$INTERFACES.aesa-new"; then
        { echo "auto eth0"; cat "$INTERFACES.aesa-new"; } > "$INTERFACES.aesa-tmp" && mv "$INTERFACES.aesa-tmp" "$INTERFACES.aesa-new"
    fi
    mv "$INTERFACES.aesa-new" "$INTERFACES"
    say "2. $INTERFACES: eth0 = $SERVER_IP/24 static (원본: interfaces.aesa-orig)"
else
    warn "$INTERFACES도 systemd-networkd도 없어 네트워크 설정 파일은 바꾸지 않았습니다. 부팅 스크립트(aesactl boot)가 IP를 넣습니다."
fi

# --- 3. 부팅·종료 스크립트 -----------------------------------------------------

if uses_systemd; then
    mkdir -p "$(dirname "$UNIT_LINK")"
    cat > "$UNIT" <<'EOF'
[Unit]
Description=AESA data processor (aesactl)
After=network.target local-fs.target
Wants=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
# 모니터 명령이 잠금을 잡고 있으면 끝날 때까지 기다린다(바로 거부하면 종료 때 처리부·로그 정리를 못 한다)
Environment=AESA_LOCK_WAIT=60
ExecStart=/usr/local/sbin/aesactl boot
ExecStop=/usr/local/sbin/aesactl stop
# 종료 때 로그를 SD로 복사한다(ssh 제어의 stop 제한 시간 150초와 맞춤). 넘기면 systemd가 강제로 끝낸다.
TimeoutStopSec=180

[Install]
WantedBy=multi-user.target
EOF
    link /etc/systemd/system/aesa.service "$UNIT_LINK"
    say "3. systemd aesa.service 등록 (부팅: aesactl boot, 종료: aesactl stop)"
else
    mkdir -p "$ROOT/etc/init.d"
    cat > "$INIT_SCRIPT" <<'EOF'
#!/bin/sh
### BEGIN INIT INFO
# Provides:          aesa
# Required-Start:    $network $local_fs
# Required-Stop:     $local_fs
# Default-Start:     2 3 4 5
# Default-Stop:      0 6
# Short-Description: AESA data processor (aesactl)
### END INIT INFO
# install_board.sh가 만듦. 부팅: 고정 IP(+자동 실행 설정 시 처리부 시작), 종료: 처리부 정지·로그 저장
# 모니터 명령이 잠금을 잡고 있으면 끝날 때까지 기다린다(바로 거부하면 종료 때 처리부·로그 정리를 못 한다).
AESA_LOCK_WAIT=60
export AESA_LOCK_WAIT
case "$1" in
    start)   /usr/local/sbin/aesactl boot ;;
    stop)    /usr/local/sbin/aesactl stop ;;
    restart) /usr/local/sbin/aesactl restart ;;
    status)  /usr/local/sbin/aesactl status ;;
    *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
exit 0
EOF
    chmod 755 "$INIT_SCRIPT"
    for l in $RC_LINKS; do
        mkdir -p "$ROOT/etc/$(dirname "$l")"
        link ../init.d/aesa "$ROOT/etc/$l"
    done
    say "3. /etc/init.d/aesa 등록 (rc2~5 S99 부팅, rc0·rc6 K01 종료)"
fi

# --- 4. sudo 규칙 ---------------------------------------------------------------

if [ -z "$(passwd_field "$ACCOUNT" 1)" ]; then
    warn "$ACCOUNT 계정이 /etc/passwd에 없습니다. sudo 규칙과 ssh 키를 넣지 않았습니다."
    HOME_DIR=""
else
    HOME_DIR=$(passwd_field "$ACCOUNT" 6)
    if [ ! -x "$ROOT/usr/bin/sudo" ] && [ ! -x "$ROOT/bin/sudo" ]; then
        warn "sudo가 설치되어 있지 않습니다. 모니터 버튼(sudo -n /usr/local/sbin/aesactl)이 동작하지 않습니다. 이미지에 sudo를 넣거나 root 계정을 써야 합니다."
    fi
    mkdir -p "$ROOT/etc/sudoers.d"
    chmod 755 "$ROOT/etc/sudoers.d"
    printf '# install_board.sh가 만듦: 모니터가 ssh로 처리부를 시작·중지할 수 있게\n%s ALL=(root) NOPASSWD: /usr/local/sbin/aesactl\n' \
        "$ACCOUNT" > "$SUDOERS"
    chown 0:0 "$SUDOERS"
    chmod 440 "$SUDOERS"
    if [ -f "$ROOT/etc/sudoers" ] && ! grep -Eq '^[#@]includedir[[:space:]]+/etc/sudoers.d' "$ROOT/etc/sudoers"; then
        warn "/etc/sudoers에 '#includedir /etc/sudoers.d' 줄이 없어 규칙이 적용되지 않습니다. visudo로 넣어 주세요."
    fi
    if [ -z "$ROOT" ] && command -v visudo >/dev/null 2>&1 && ! visudo -cf "$SUDOERS" >/dev/null 2>&1; then
        rm -f "$SUDOERS"
        warn "sudo 규칙 문법 확인(visudo -c)에 실패해 지웠습니다."
    else
        say "4. sudo 규칙: $ACCOUNT → /usr/local/sbin/aesactl (비밀번호 없음)"
    fi
fi

# --- 5. ssh 공개키 --------------------------------------------------------------

KEYS="$SRC_DIR/authorized_keys"
if [ -n "$HOME_DIR" ] && [ -f "$KEYS" ]; then
    uid=$(passwd_field "$ACCOUNT" 3)
    gid=$(passwd_field "$ACCOUNT" 4)
    ssh_dir="$ROOT$HOME_DIR/.ssh"
    mkdir -p "$ssh_dir"
    touch "$ssh_dir/authorized_keys"
    added=0
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(printf '%s' "$line" | tr -d '\r')
        case "$line" in ''|'#'*) continue ;; esac
        if ! grep -qxF "$line" "$ssh_dir/authorized_keys"; then
            printf '%s\n' "$line" >> "$ssh_dir/authorized_keys"
            added=$((added + 1))
        fi
    done < "$KEYS"
    chown "$uid:$gid" "$ROOT$HOME_DIR" "$ssh_dir" "$ssh_dir/authorized_keys"
    chmod go-w "$ROOT$HOME_DIR"
    chmod 700 "$ssh_dir"
    chmod 600 "$ssh_dir/authorized_keys"
    say "5. ssh 공개키 ${added}개 추가 → $HOME_DIR/.ssh/authorized_keys (총 $(grep -c . "$ssh_dir/authorized_keys")개)"
elif [ -n "$HOME_DIR" ]; then
    warn "$KEYS가 없어 ssh 공개키를 넣지 않았습니다. 노트북에서 prepare_board_sd.bat을 실행해 SD에 넣은 뒤 다시 실행하세요."
fi

# --- 6. 점검 --------------------------------------------------------------------

if [ ! -x "$ROOT/usr/sbin/dropbear" ] && [ ! -x "$ROOT/usr/sbin/sshd" ]; then
    warn "ssh 서버(dropbear/sshd)가 없습니다. 모니터가 보드에 접속할 수 없습니다."
fi
if [ -n "$HOME_DIR" ] && [ -r "$ROOT/etc/shadow" ]; then
    hash=$(awk -F: -v user="$ACCOUNT" '$1 == user { print $2; exit }' "$ROOT/etc/shadow")
    changed=$(awk -F: -v user="$ACCOUNT" '$1 == user { print $3; exit }' "$ROOT/etc/shadow")
    case "$hash" in
        '!'*|'*'*) warn "$ACCOUNT 계정이 잠겨 있습니다(비밀번호 칸 '${hash%"${hash#?}"}'). dropbear는 잠긴 계정의 키 로그인도 막을 수 있습니다. 시리얼에서 $ACCOUNT로 로그인해 비밀번호를 정하세요." ;;
        '') warn "$ACCOUNT 계정의 비밀번호가 비어 있습니다. 시리얼에서 passwd $ACCOUNT로 정하세요." ;;
    esac
    if [ "$changed" = "0" ]; then
        warn "$ACCOUNT 계정은 첫 로그인 때 비밀번호를 바꿔야 하는 상태입니다. 시리얼에서 한 번 로그인해 바꿔 두세요(ssh 키 로그인 전에)."
    fi
fi

sync

# 보드에서 실행했다면 지금 바로 IP를 넣는다 (재부팅 없이 ssh 시험 가능).
if [ -z "$ROOT" ]; then
    /usr/local/sbin/aesactl net || warn "지금 IP를 넣지 못했습니다. 재부팅 후 확인하세요."
fi

say "끝 (주의 ${WARNINGS}개)"
say "확인: 노트북에서 ssh -o HostKeyAlgorithms=+ssh-rsa $ACCOUNT@$SERVER_IP 'sudo -n /usr/local/sbin/aesactl status'"
[ -n "$ROOT" ] && say "SD를 안전하게 꺼낸 뒤(umount) 보드에 꽂으세요."
exit 0
