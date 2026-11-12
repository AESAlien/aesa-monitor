# aesa-monitor

AESA 데이터 처리부(aesa-processor) 모니터·분석기. `aesa-processor/tools/monitor`(1단계 모니터)를 바탕으로
화면을 다시 구성하고 녹화·재생, 처리부 로그 가져오기, 통계 분석을 더한 버전이다.

- 실시간: 데이터 출처를 둘 중에서 고른다(`MONITOR_SOURCE` 또는 `--source`). 아래 "데이터 출처" 참고.
  - 복사본 수신(copy, 기본): 처리부가 보내는 복사본(0xAB01, 0xBA01, 0xAC01, 0xAC02)과 상태(0xAD01)를 받는다.
  - 통신 캡처(capture): 처리부·모의기·운용통제기 사이 TCP 패킷을 캡처해 같은 화면으로 보여준다.
- 녹화·재생: 받은 프레임을 그대로 녹화하고, 같은 화면으로 다시 재생한다.
- 분석: 녹화나 처리부 로그(adp_*.log, adp_detection_*.csv)로 통계 요약과 처리부 이벤트 타임라인을 만든다.

모니터는 처리부에 아무것도 보내지 않는다. 복사본 수신은 처리부의 모니터 지원 코드(aesa-processor feature/126)가
있어야 하고, 통신 캡처는 처리부 코드를 바꾸지 않아도 된다.

관련 저장소: [aesa-processor](https://github.com/AESAlien/aesa-processor) (데이터 처리부). 작업 기록은 `docs/작업로그.md`.

## 설치

Python 3.8 이상, 표준 라이브러리만 쓴다. 별도 패키지 설치는 없다.

```sh
# aesa-processor와 같은 폴더에 받는다. 예시 명령의 ../aesa-processor/.env와 C++ 헤더 대조 테스트가 이 배치를 가정한다.
cd ~/YGC/LIGproject
git clone https://github.com/AESAlien/aesa-monitor.git
cd aesa-monitor
cp monitor.env.example monitor.env    # 선택. 없으면 기본값

# Linux
sudo apt install python3-tk           # 화면(tkinter)
sudo apt install tcpdump              # 통신 캡처를 쓸 때만
sudo setcap cap_net_raw,cap_net_admin=eip $(which tcpdump)   # 일반 사용자로 캡처
```

## 실행

```sh
./run.sh --env ../aesa-processor/.env             # 실시간 모니터 (python3 -m aesa_monitor 와 같음)
./run.sh --env ../aesa-processor/.env --record    # 시작하자마자 녹화
./run.sh --open ~/aesa-records/20261112_101500     # 녹화·로그 열기 (처리부 접속도 함께 함)
./run.sh --open ~/aesa-records/20261112_101500 --offline
./run.sh --stats ~/aesa-records/20261112_101500    # 화면 없이 통계 요약만 출력
```

| 옵션 | 설명 |
| --- | --- |
| `--env PATH` | 처리부와 같은 `.env`. 서버 주소(`COMM_SERVER_*`), 모니터 주소(`COMM_MONITOR_*`), 추적 설정(`TARGET_*`)을 읽는다 |
| `--server`, `--local` | `ADDRESS:PORT`로 `.env` 값을 덮어쓴다. 둘 다 주면 `.env` 없이 실행된다 |
| `--source copy\|capture` | 데이터 출처. 기본값은 `monitor.env`의 `MONITOR_SOURCE`(없으면 copy) |
| `--capture-command CMD` | 통신 캡처 명령. 기본값은 `MONITOR_CAPTURE_COMMAND` |
| `--settings PATH` | 모니터 설정 파일. 기본값은 이 폴더의 `monitor.env` |
| `--record` | 시작하자마자 녹화. `--headless`와 함께 쓰면 화면 없이 녹화만 한다 |
| `--open PATH` | 세션 폴더, 녹화 파일(`*.aesarec`), 처리부 로그 파일, 캡처 파일(`*.pcap`, `*.pcapng`)을 연다 |
| `--offline` | 처리부에 접속하지 않는다 |
| `--stats PATH` | 통계 요약을 터미널에 출력하고 끝낸다 |
| `--headless`, `--duration` | 화면 없이 1초마다 수신 요약 출력 |
| `--font-size N`, `--tab NAME` | 글자 크기, 처음 탭(beam, track, comm, analysis) |

처리부는 접속한 상대를 원격 주소와 포트로 구분한다. 그래서 모니터는 `COMM_MONITOR_ADDRESS:COMM_MONITOR_PORT`에
바인딩한 뒤 접속한다. 노트북에 그 주소(예: 192.168.10.30)가 있어야 한다. 끊기면 1초마다 다시 접속한다.

## 데이터 출처

| | 복사본 수신 (copy) | 통신 캡처 (capture) |
| --- | --- | --- |
| 받는 것 | 처리부가 모니터에 보내는 복사본 + 상태(0xAD01) | 세 장비가 실제로 주고받은 모든 메시지(0xCA01, 0xBA02, 0xAC03 포함) |
| 처리부 코드 | 모니터 지원 코드 필요 | 바꾸지 않음 |
| 처리부 시각·속도·연결 상태 | 0xAD01로 정확히 | 빔 시각, TCP 연결·종료, 운용 명령(0xCA01)으로 추정 |
| 필요한 것 | 모니터 PC에 `COMM_MONITOR_ADDRESS` | 캡처 권한, 다른 장비 사이 패킷이 보이는 위치(아래) |

통신 캡처는 표준 출력으로 pcap을 내는 명령을 실행해 읽는다(`MONITOR_CAPTURE_COMMAND`, `--capture-command`).
`{port}`는 처리부 서버 포트로 바뀐다. 기본값은 이 PC에서 `tcpdump -i any --immediate-mode -U -s 0 -w - tcp port {port}`다.

```sh
# 이 PC에서 캡처 (처리부·모의기·전시기 중 하나가 이 PC이거나, 포트 미러링을 이 PC로 받을 때)
./run.sh --env ../aesa-processor/.env --source capture
# 처리부 보드에서 캡처해 ssh로 받기 (보드에 tcpdump와 공개키 필요)
./run.sh --env ../aesa-processor/.env --source capture \
    --capture-command "ssh root@192.168.10.20 tcpdump -i eth0 --immediate-mode -U -s 0 -w - tcp port {port}"
```

- `--immediate-mode`: libpcap이 패킷을 묶어서 넘기지 않게 한다. 빼면 캡처가 복사본보다 늦게(측정 40~120 ms,
  트래픽이 적으면 더 길 수 있음) 뭉쳐서 들어온다. 넣으면 복사본보다 20~30 ms 빠르다(복사본은 처리부 모니터 큐를
  한 번 더 거침). 오래된 tcpdump(예: 보드)가 이 옵션을 모르면 빼고 쓴다.
- 권한: tcpdump는 root나 `cap_net_raw` 권한이 필요하다. 예: `sudo setcap cap_net_raw,cap_net_admin=eip $(which tcpdump)`.
  권한이 없으면 통신 탭과 연결 상태에 `캡처 종료 (코드 1): ... Operation not permitted`가 보인다.
- 위치: 일반 스위치는 다른 장비끼리 주고받는 패킷을 모니터 PC로 보내지 않는다. 포트 미러링, 허브,
  처리부 보드 캡처(ssh) 중 하나가 필요하다.
- 연결의 상대(운용통제기, 모의기)는 `.env`의 `COMM_OPERATOR_CONSOLE_*`, `COMM_SCENARIO_SIMULATOR_*` 주소로 정하고,
  없으면 메시지 종류로 정한다. 모니터 복사본 연결(`COMM_MONITOR_*` 또는 0xAD01이 오가는 연결)은 같은 메시지가
  두 번 들어가지 않도록 뺀다. 그래서 복사본 수신 모니터와 캡처 모니터를 동시에 띄워도 된다.
- 캡처를 연결 도중에 시작하면 다음 메시지 경계부터 받는다. 그 전에 한 번만 보낸 메시지(레이더 자세, 운용 명령)는
  보이지 않을 수 있다.
- 캡처 녹화도 같은 `monitor.aesarec` 형식이다. 상대 연결 변화는 레코드 종류 4로 남는다.
- Wireshark·tcpdump로 저장한 `*.pcap`, `*.pcapng`도 `--open`이나 `열기`로 재생·분석할 수 있다.

## 화면

모든 탭 맨 위에 **판정 한 줄**(정상·주의·이상·대기)이 있다. 숫자 옆의 `?`를 누르면 무엇을 뜻하는지, 기준이 무엇인지 나온다.
글자 크기는 상단 `A+`/`A−` 또는 `Ctrl` `+`/`-`/`0`. 바꾼 크기는 종료할 때 `monitor.env`에 저장된다.

| 탭 | 질문 | 내용 |
| --- | --- | --- |
| 빔 | 빔이 계획대로 나가고 있나? | 지표 카드(탐색 한 바퀴·기대값, 점유율, 탐색→확인 지연, 추적 주기 오차, 빠진 빔), 빔 방향 지도(탐색 칸, 확인·추적 빔, 트랙 위치, 추적 영역 ±60°/-10~60°), 트랙별 요청 빔 일정, 빔 타임라인 |
| 추적 | 표적을 잘 따라가고 있나? | 트랙 목록, 위에서 본 트랙, 고른 트랙의 빔-탐지 차이 추이, 사이클 3단계(예측 → 관측·연관 → 결과), 빔 단면. 이전 '타겟' 탭과 '추적' 탭을 합쳤다 |
| 통신 | 연결이 살아 있고 메시지가 제때 오나? | 링크 카드 3개(처리부·운용통제기·모의기), 메시지 표(속도와 기대값), 연결 이벤트 기록, 레이더 정보 |
| 분석 | 무슨 일이 있었나? | 세션 정보, 통계 요약(항목을 누르면 분포), 트랙별 요약, 처리부 이벤트 타임라인(누르면 재생 위치 이동) |

판정 기준(모니터에서 정한 값):

| 항목 | 정상 | 주의 | 이상 |
| --- | --- | --- | --- |
| 탐색 한 바퀴 | 기대값(칸 수 × 10 ms ÷ 탐색 점유율) ±10% | ±30% | 그 밖 |
| 추적 주기 오차 | ≤ 20 ms | ≤ 100 ms | 그 밖 |
| 탐색→확인 지연 | 설정값 ±20 ms | ±100 ms | 그 밖 |
| 빠진 빔(최근 5초, 10 ms 칸) | 0 | 1~5 | 6 이상 |
| 트랙 연속 미탐 | 0 | 1회 | 한도−1 이상 |

**한계:** 처리부는 예측 거리, 공분산, NDS 점수를 보내지 않는다. 그래서 '예측'은 빔 방향뿐이고, '빔-탐지 차이'는
빔 축과 탐지 사이 수직 거리, 연관된 탐지는 빔 축에 가장 가까운 탐지로 **추정**한다. 처리부가 실제로 고른 탐지와 다를 수 있다.

## 녹화와 세션 폴더

상단 `● 녹화`를 누르면 `MONITOR_RECORD_DIR`(기본 `~/aesa-records`) 아래에 세션 폴더가 생긴다.

```
~/aesa-records/20261112_101500/
├─ monitor.aesarec      모니터가 받은 프레임 그대로 + 수신 시각
└─ processor_logs/      [로그 가져오기]로 가져온 처리부 로그
```

녹화 파일 크기는 처리부 시각 10분에 약 30 MB다(가짜 처리부, 트랙 4개 기준 측정. 트랙이 많으면 커진다). 녹화 중인 파일도 열 수 있다(끝부분은 잘라 읽음).

재생할 때는 상단 아래에 재생 바(재생/정지, ±5초, 속도 ×0.1~×50, 위치 막대)가 나온다. 빔·추적·통신 탭이 녹화 시점의
화면으로 바뀐다. `실시간으로`를 누르면 돌아간다. 재생하는 동안에도 실시간 수신과 녹화는 계속된다. `Space`로 재생/정지.

## 처리부 로그 가져오기

상단이나 분석 탭의 `로그 가져오기` 버튼은 `aesa_monitor/io/log_fetch.py`를 백그라운드로 실행한다. 명령줄에서도 같다.

```sh
python3 -m aesa_monitor.io.log_fetch --dest ~/aesa-records/test/processor_logs                 # monitor.env 설정
python3 -m aesa_monitor.io.log_fetch --source local --local-dir ~/aesa/closedloop/logs --dest DIR
python3 -m aesa_monitor.io.log_fetch --source ssh --target root@192.168.10.20 --dest DIR
```

| 방식 | 언제 | 동작 |
| --- | --- | --- |
| local | 처리부를 이 PC에서 실행 | `MONITOR_LOG_LOCAL_DIR`의 `adp_*.log`, `adp_detection_*.csv`를 복사. 크기가 같으면 건너뜀 |
| ssh | 처리부를 보드에서 실행 | `scp -O ... root@192.168.10.20:/tmp/aesa/logs/adp_* DIR/`. 보드 dropbear에 맞춘 옵션 포함 |

저장 위치: 녹화 중이면 그 세션 폴더, 세션을 열어 둔 상태면 그 폴더, 아니면 새 세션 폴더의 `processor_logs/`.

**ssh 주의**
- 버튼에서는 비밀번호를 입력할 수 없다(BatchMode). 보드에 공개키를 먼저 등록해야 한다. 보드는 initramfs라 재부팅하면 등록이 사라질 수 있다(추측).
- 보드의 `run_adp.sh`는 처리부를 `/tmp/aesa`에서 실행한다. 실행 중에 가져오면 쓰는 중인 마지막 줄은 건너뛴다.

## 처리부 로그와 시각 맞추기

- 탐지 CSV와 녹화는 처리부 시각(시작 후 ms)을 쓴다. 그대로 맞는다.
- 메시지 로그는 UTC 벽시계 시각이다. main 로거의 `Six worker threads started` 줄을 처리부 시각 0으로 보고 바꾼다.
  main은 이 줄을 쓴 직후 빔 시각의 기준을 잡으므로 차이는 수 ms 이내다(추정). 보드 시계가 틀려도 영향이 없다.
- 녹화 없이 처리부 로그만 있으면 빔 방향과 트랙 목록이 없다. 이때 통계는 탐지 CSV로 계산하고, 재생은 할 수 없다.

## 설정 파일 `monitor.env`

`monitor.env.example`을 복사해서 쓴다. 없으면 기본값을 쓴다. 로그 가져오기 창에서 `이 설정을 monitor.env에 저장`을 켜면 그 값이 저장된다.
화면을 닫을 때 글자 크기를 저장하면서 아래 키를 모두 적는다. `monitor.env`는 개인 설정이라 git에 올리지 않는다(.gitignore).

| 키 | 기본값 | 설명 |
| --- | --- | --- |
| `MONITOR_SOURCE` | copy | 데이터 출처: copy(복사본 수신) 또는 capture(통신 캡처) |
| `MONITOR_CAPTURE_COMMAND` | `tcpdump -i any --immediate-mode -U -s 0 -w - tcp port {port}` | capture: 표준 출력으로 pcap을 내는 명령 |
| `MONITOR_RECORD_DIR` | `~/aesa-records` | 세션 폴더를 만들 곳 |
| `MONITOR_FONT_SIZE` | 12 | 본문 글자 크기(8~32) |
| `MONITOR_AUTO_RECORD` | 0 | 1이면 시작하자마자 녹화 |
| `MONITOR_LOG_SOURCE` | local | local 또는 ssh |
| `MONITOR_LOG_LOCAL_DIR` | (빈 값) | local: 처리부 logs 폴더 |
| `MONITOR_LOG_SSH_TARGET` | root@192.168.10.20 | ssh: 사용자@주소 |
| `MONITOR_LOG_SSH_PORT` | 22 | ssh 포트 |
| `MONITOR_LOG_REMOTE_DIR` | /tmp/aesa/logs | 보드의 로그 폴더 |
| `MONITOR_LOG_SSH_OPTIONS` | dropbear용 옵션 | scp에 그대로 붙이는 옵션 |

## 구조

```
aesa_monitor/
├─ core/      화면과 무관한 계산 (tkinter 없음)
│   protocol(ICD 해석·인코딩) events config settings geometry
│   state(누적 상태) tracking(사이클 재구성) traffic(수신 속도) verdict(판정) stats(통계 요약)
│   capture(패킷 → TCP 재조립 → 메시지)
├─ io/        connection(복사본 수신, 출처 만들기) capture(통신 캡처 출처, 캡처 파일) pcap(pcap·pcapng 읽기)
│             recording(녹화 파일) replay(재생) session(세션 열기)
│             processor_log(처리부 로그 해석) log_fetch(로그 가져오기)
└─ ui/        app(메인 창) style widgets beam_tab track_tab comm_tab analysis_tab fetch_dialog
tools/fake_processor.py   시험·시연용 가짜 처리부 (아래)
tests/                    단위 테스트
```

## 시험

```sh
python3 -m unittest discover -s tests -t .
```

- `test_protocol.py`의 C++ 헤더 대조는 `../aesa-processor`(또는 환경변수 `AESA_PROCESSOR_DIR`)가 있을 때만 한다.
- 화면 시험은 가짜 처리부로 한다. 처리부·모의기를 아주 단순하게 흉내 낸다(칼만 필터가 아님). 값의 정확도를 볼 용도가 아니다.

```sh
python3 tools/fake_processor.py --listen 127.0.0.1:43200 --log-dir /tmp/fake-logs --speed 2
./run.sh --server 127.0.0.1:43200 --local 127.0.0.1:43203
```

통신 캡처 시험: `--peers`를 주면 가짜 모의기·운용통제기가 처리부에 접속해 실제 방향으로 메시지를 주고받는다.

```sh
python3 tools/fake_processor.py --peers --speed 2
./run.sh --server 127.0.0.1:43200 --local 127.0.0.1:43203 --source capture \
    --capture-command "tcpdump -i lo --immediate-mode -U -s 0 -w - tcp port {port}"
```

tcpdump에 `setcap` 권한을 준 상태를 가정한다(설치 참고). 모니터 전체를 `sudo`로 띄우면 root로 화면을 열지 못하거나
`monitor.env`가 root 소유로 저장될 수 있다.

## 라이선스

All rights reserved. 저작권자(AESAlien)의 사전 서면 허락 없이 사용·복제·수정·배포할 수 없다. [LICENSE](LICENSE) 참고.
