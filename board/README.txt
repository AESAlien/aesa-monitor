AESA 데이터 처리부 - 시연용 묶음 (Zybo Z7, PetaLinux 2022, SD 부팅)

[구성] SD FAT(첫 번째 파티션)의 aesa 폴더
  adp               처리부 실행 파일 (ARM 32-bit 정적 링크, Release)
  aesa.env          처리부 설정 (실행할 때 작업 폴더의 .env로 복사됨)
  run_aesa.sh       실행·관리 스크립트 (설치 후에는 보드의 /usr/local/sbin/aesactl)
  install_board.sh  보드 한 번 설정 (고정 IP, 부팅 스크립트, sudo 규칙, ssh 키)
  board.conf        AESA_AUTOSTART=0/1 (부팅하면 처리부를 바로 시작할지)
  authorized_keys   노트북 ssh 공개키 (노트북의 prepare_board_sd.bat이 넣는다)
  SHA256SUMS        adp 검증값 (start가 자동 확인)
  COMMIT            빌드한 커밋
  logs/             stop·poweroff·savelog 때 보드 로그를 여기로 복사 (노트북에서 바로 읽을 수 있음)

[주소] (aesa.env)
  처리부(이 보드)  192.168.10.20:3200
  전시기           192.168.10.10:3100
  모의기           192.168.10.100:3300
  모니터(분석기)   192.168.10.30        (캡처 방식, 처리부 설정 불필요)

[처음 한 번]
  1) 노트북: SD를 꽂고 모니터 폴더의 prepare_board_sd.bat 실행
       -> 이 폴더의 스크립트를 SD에 복사하고, 노트북 ssh 키를 만들어(없을 때만) 공개키를 aesa\authorized_keys에 넣는다
  2) U-Boot를 SD 부팅으로 (이미 되어 있으면 생략)
       시리얼 콘솔(115200)에서 부팅 카운트다운 3초 안에 키 입력 후
         setenv bootcmd 'run sdboot'
         saveenv
         boot
  3) 보드(시리얼)에서 petalinux로 로그인 (처음이면 비밀번호를 새로 정하라고 나온다)
       mount | grep mmcblk0p1                       SD FAT 위치 확인 (보통 /run/media/mmcblk0p1)
       sudo sh /run/media/mmcblk0p1/aesa/install_board.sh
     -> "끝 (주의 0개)"가 나오면 성공. 주의가 있으면 내용대로 고친 뒤 다시 실행해도 된다.
     리눅스 PC에서 SD를 직접 고칠 수도 있다(보드 없이):
       sudo sh <FAT 마운트>/aesa/install_board.sh --root <ext4 마운트>
  4) 노트북에서 확인 (모니터 [메뉴] -> [처리부 제어] -> [상태])
       또는 명령 프롬프트: ssh -i %USERPROFILE%\.ssh\aesa_board_ed25519 -o HostKeyAlgorithms=+ssh-rsa petalinux@192.168.10.20 "sudo -n /usr/local/sbin/aesactl status"

[시연 순서]
  보드 전원 -> (부팅이 끝나면 IP 192.168.10.20이 자동으로 들어간다)
  -> 모니터 실행 -> [메뉴] -> [처리부 제어] -> [시작] (노트북 시각으로 보드 시각을 맞춘 뒤 시작)
  -> 전시기 운용 ON -> 모의기 시나리오 로드
  끝: [처리부 제어] -> [중지] -> [로그 가져오기] -> [보드 끄기] -> 10초 뒤 전원 빼기
      (ext4라 전원을 바로 뽑지 않는다. 그냥 끄더라도 poweroff/reboot 때는 처리부를 멈추고 로그를 SD로 복사한다)

[보드에서 직접] (시리얼 또는 ssh)
  sudo /usr/local/sbin/aesactl start ['YYYY-MM-DD HH:MM:SS']   시각을 주면 먼저 보드 시각을 맞춘다
  sudo /usr/local/sbin/aesactl stop | restart | status | log | savelog | settime '시각' | poweroff
  (ssh로 접속하면 PATH에 /usr/local/sbin이 없을 수 있어 전체 경로로 쓴다. 보드 호스트 키가 RSA라 노트북 ssh에는 -o HostKeyAlgorithms=+ssh-rsa가 필요)
  start는 포트가 15초 안에 열리지 않으면, stop·poweroff·savelog는 로그를 SD로 복사하지 못하면 실패로 끝난다.
  로그 복사는 SD에 같은 크기로 이미 있는 파일을 건너뛰고, ssh가 끊겨도 끝까지 한다. 결과는 status의 "마지막 로그 저장".
  설치 전에는 sudo sh /run/media/mmcblk0p1/aesa/run_aesa.sh <명령> 으로 같은 일을 한다.

[참고]
  - 실행은 ext4의 /opt/aesa에서 한다 (FAT는 실행 권한이 없음). 로그: /opt/aesa/logs (petalinux도 읽을 수 있음)
  - 로그 이름: adp_<날짜>_<시각>.log, adp_detection_<날짜>_<시각>.csv, console.out(표준 출력)
  - 보드에 RTC가 없어 부팅하면 예전 시각이다. 모니터 [시작]이 노트북 시각을 넣어 준다.
    자동 실행(AESA_AUTOSTART=1)이면 시각을 맞추기 전에 시작하므로 로그 이름이 예전 날짜가 된다.
  - eth0의 DHCP 클라이언트(udhcpc)가 돌고 있으면 aesactl이 멈추고 고정 IP를 넣는다.
  - 설정을 바꾸려면 노트북에서 aesa.env를 고친 뒤 restart. adp를 바꾸면 SHA256SUMS도 갱신해야 한다
      (노트북에서: cd aesa && sha256sum adp > SHA256SUMS)
  - 설치 되돌리기: sudo sh .../aesa/install_board.sh --uninstall
