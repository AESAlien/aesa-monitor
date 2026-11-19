# aesa-monitor

AESA 데이터 처리부(aesa-processor) 모니터·분석기. `aesa-processor/tools/monitor`(1단계 모니터)를 바탕으로
화면을 다시 구성하고 녹화·재생, 처리부 로그 가져오기, 통계 분석을 더한 버전이다.

- 실시간: 처리부·모의기·운용통제기 사이 TCP 패킷을 캡처(통신 캡처, 기본)해 메시지 흐름을 보여준다. 아래 "데이터 출처" 참고.
- 녹화·재생: 캡처한 메시지를 그대로 녹화하고, 원하는 시점부터 같은 화면으로 다시 재생한다(사후 분석).
- 분석: 녹화나 처리부 로그(adp_*.log, adp_detection_*.csv)로 통계 요약과 처리부 이벤트 타임라인을 만든다.

모니터는 처리부에 아무것도 보내지 않고, 처리부 코드도 바꾸지 않는다. 데이터 출처는 통신 캡처로 정했다(2026-11 팀 결정).
이전 방식인 복사본 수신(copy, 처리부가 모니터에 복사본을 보냄)은 처리부 main에 없는 코드(aesa-processor
`feature/129-monitor-feed`)가 있어야 해서 비교·호환용으로만 남겨 두었다.

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
sudo apt install tcpdump              # 통신 캡처 (이 PC에서 캡처할 때)
sudo setcap cap_net_raw,cap_net_admin=eip $(which tcpdump)   # 일반 사용자로 캡처
# 처리부 보드에서 캡처할 때는 보드에 tcpdump와 이 PC의 공개키가 있어야 한다(아래 "데이터 출처").
```

## 실행

```sh
./run.sh --env ../aesa-processor/.env             # 실시간 모니터, 통신 캡처 (python3 -m aesa_monitor 와 같음)
./run.sh --env ../aesa-processor/.env --record    # 시작하자마자 녹화
./run.sh --open ~/aesa-records/20261112_101500     # 녹화·로그 열기 (처리부 접속도 함께 함)
./run.sh --open ~/aesa-records/20261112_101500 --offline
./run.sh --stats ~/aesa-records/20261112_101500    # 화면 없이 통계 요약만 출력
```

| 옵션 | 설명 |
| --- | --- |
| `--env PATH` | 처리부와 같은 `.env`. 처리부 주소(`COMM_SERVER_*`), 운용통제기·모의기 주소, 추적 설정(`TARGET_*`)을 읽는다 |
| `--server`, `--local` | `ADDRESS:PORT`로 `.env` 값을 덮어쓴다. `--server`를 주면 `.env` 없이 실행된다. `--local`(모니터 주소)은 copy에만 필요 |
| `--source capture\|copy` | 데이터 출처. 기본값은 `monitor.env`의 `MONITOR_SOURCE`(없으면 capture) |
| `--capture-command CMD` | 통신 캡처 명령. 기본값은 `MONITOR_CAPTURE_COMMAND` |
| `--settings PATH` | 모니터 설정 파일. 기본값은 이 폴더의 `monitor.env` |
| `--record` | 시작하자마자 녹화. `--headless`와 함께 쓰면 화면 없이 녹화만 한다 |
| `--open PATH` | 세션 폴더, 녹화 파일(`*.aesarec`), 처리부 로그 파일, 캡처 파일(`*.pcap`, `*.pcapng`)을 연다 |
| `--offline` | 처리부에 접속하지 않는다 |
| `--stats PATH` | 통계 요약을 터미널에 출력하고 끝낸다 |
| `--headless`, `--duration` | 화면 없이 1초마다 수신 요약 출력 |
| `--font-size N`, `--tab NAME` | 글자 크기, 처음 탭(beam, track, comm, analysis) |

## 데이터 출처

기본은 **통신 캡처(capture)**다. 처리부 서버 포트(`COMM_SERVER_PORT`, 3200)를 지나는 양방향 TCP를 모두 잡는다.

| 연결 | 방향 | 메시지 |
| --- | --- | --- |
| 처리부 ↔ 모의기 | 처리부 → 모의기 | 빔 명령(0xAB01) — 빔 탭·추적 탭 예측 빔의 근거 |
| | 모의기 → 처리부 | 탐지 정보(0xBA01), 레이더 위치·자세(0xBA02) |
| 처리부 ↔ 운용통제기 | 처리부 → 운용통제기 | 추적 정보(0xAC01), 레이더 위치·자세(0xAC02), 트랙 삭제(0xAC03) |
| | 운용통제기 → 처리부 | 운용 명령(0xCA01) — 운용 ON/OFF 판단의 근거 |

캡처는 표준 출력으로 pcap을 내는 명령을 실행해 읽는다(`MONITOR_CAPTURE_COMMAND`, `--capture-command`).
`{port}`는 처리부 서버 포트로 바뀐다. 기본값은 이 PC에서 `tcpdump -i any --immediate-mode -U -s 0 -w - tcp port {port}`다.

**어디서 캡처하나.** 일반(단순) 스위치는 다른 장비끼리 주고받는 패킷을 모니터 PC로 보내지 않는다. 그래서 두 연결이
모두 지나가는 처리부 쪽에서 캡처한다.

```sh
# 처리부를 이 PC에서 실행할 때 (가짜 처리부 시험 포함): 기본 명령 그대로
./run.sh --env ../aesa-processor/.env
# 처리부를 보드(192.168.10.20)에서 실행할 때: 보드에서 tcpdump를 실행하고 ssh로 받는다 (보드에 tcpdump와 공개키 필요)
./run.sh --env ../aesa-processor/.env \
    --capture-command "ssh root@192.168.10.20 tcpdump -i eth0 --immediate-mode -U -s 0 -w - tcp port {port}"
```

보드 캡처는 아직 실제 보드에서 확인하지 않았다(보드에 tcpdump가 있는지, `--immediate-mode`를 지원하는지 미확인).
명령을 정하면 `monitor.env`의 `MONITOR_CAPTURE_COMMAND`에 넣어 두면 된다. 관리형 스위치의 포트 미러링을 쓸 수 있으면
처리부 포트를 이 PC로 복사해 기본 명령(`-i <그 인터페이스>`)으로 받아도 된다.

| | 통신 캡처 (capture, 기본) | 복사본 수신 (copy, 이전 방식) |
| --- | --- | --- |
| 받는 것 | 세 장비가 실제로 주고받은 모든 메시지(위 표) | 처리부가 모니터에 보내는 복사본 + 상태(0xAD01) |
| 처리부 코드 | 바꾸지 않음 | `feature/129-monitor-feed`의 모니터 지원 코드 필요(main에 없음) |
| 처리부 시각·연결·운용 상태 | 빔 시각, TCP 연결·종료, 운용 명령(0xCA01)으로 추정 | 0xAD01로 정확히 |
| 필요한 것 | 캡처 권한, 두 연결이 보이는 위치(처리부 쪽) | `.env`의 `COMM_MONITOR_*`(또는 `--local`), 모니터 PC에 그 주소 |

copy는 `--source copy`로 쓴다. 처리부는 접속한 상대를 원격 주소와 포트로 구분하므로 모니터가
`COMM_MONITOR_ADDRESS:COMM_MONITOR_PORT`에 바인딩한 뒤 접속한다. 끊기면 1초마다 다시 접속한다.

통신 캡처 참고:

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
- 캡처를 연결 도중에 시작하면 다음 메시지 경계부터 받는다. 그 전에 한 번만 보낸 메시지(레이더 위치·자세 0xBA02·0xAC02,
  운용 명령 0xCA01)는 보이지 않는다. 그러면 운용 상태가 '?'이고 추적 영역이 그려지지 않는다.
  **모니터(캡처)를 모의기·운용통제기 접속보다 먼저 켠다.**
- 캡처 녹화도 같은 `monitor.aesarec` 형식이다. 상대 연결 변화는 레코드 종류 4로 남는다.
- Wireshark·tcpdump로 저장한 `*.pcap`, `*.pcapng`도 `--open`이나 `메뉴 → 파일 열기`로 재생·분석할 수 있다.

## 화면

창 맨 위 **상단 바**: 왼쪽에 연결 상태(`● 처리부 ● 통제 ● 모의`, 초록 연결·빨강 끊김·회색 모름), 실시간/재생/캡처 표시,
처리부 시각(`시각 mm:ss.sss`, 옆에 배율·트랙 수), 오른쪽에 녹화 상태(녹화 중 경과·크기, 녹화 꺼짐, 저장됨), `● 녹화`, `메뉴`.
`메뉴`에는 폴더·파일 열기, 로그 가져오기, 글자 크기가 있다. 마우스를 올리면 자세히 나온다(끊긴 이유, 처리부 주소, 저장 경로 등).
재생 중 연결 상태와 시각은 녹화 당시 값이고, 녹화 상태는 지금(실시간) 녹화다.
창이 좁거나 글자가 크면 오른쪽 묶음이 둘째 줄로 내려가고, 그래도 모자라면 배율·트랙 수를 숨긴다(툴팁에는 남음).
재생 바도 좁으면 위치 막대를 둘째 줄로 내린다.

모든 탭 맨 위에 **판정**(정상·주의·이상·대기)이 있다. 숫자 옆의 `?`를 누르면 무엇을 뜻하는지, 기준이 무엇인지 나온다.
빔 탭은 판정 배지와 지표 카드를 한 줄로 합쳤다. 정상인 카드는 무채색이고, 주의(▲)·이상(■)인 카드만 색과 기호가 들어온다.
연결 끊김·처리부 정지처럼 탭 전체에 해당하는 상태는 카드 대신 그 문구와 마지막 값을 보여준다.
글자 크기는 `메뉴` 또는 `Ctrl` `+`/`-`/`0`. 바꾼 크기는 종료할 때 `monitor.env`에 저장된다.

| 탭 | 질문 | 내용 |
| --- | --- | --- |
| 빔 | 빔이 계획대로 나가고 있나? | 판정 배지 + 지표 카드(탐색 프레임 시간·기대값, 점유율, 탐색→확인 지연, 추적 주기 오차, 빠진 빔), 빔 방향 지도(탐색 칸, 확인·추적 빔, 트랙 위치, 추적 영역 ±60°/-10~60°), 트랙별 요청 빔 일정, 탐색 프레임별 빔 수(위 프레임 시간 띠: 마지막 프레임 시간을 탐색·확인·추적에 쓴 시간으로 나눔, 띠 안 % = 점유율, ▼ = 진행 중 프레임 위치 / 아래 막대: 끼어든 확인·추적 빔 수, 오른쪽 축 = 확인·추적 비율 %, 점선 = 기대 추적 빔 수 = 트랙 수 × 프레임 시간 ÷ 추적 주기), 빔 타임라인(창이 낮아도 잘리지 않게 아래 고정). 창이 낮으면 일정 표 4→3행 → 판정 카드 아랫줄(툴팁에 남음) → 그래프 축 제목 줄·요약 줄 순서로 줄인다 |
| 추적 | 표적을 잘 따라가고 있나? | 왼쪽:오른쪽 = 3:2. 왼쪽 전시기(부채꼴 PPI, 섹터 = 레이더 정면 위 ±70° / 360° = 북쪽 위): 거리 고리, 방위 눈금, 추적 영역 ±60° 점선, 탐색·확인·추적 빔 부채꼴(최근일수록 진함), 트랙 위치·꼬리·60초 속도 벡터, `#ID 거리·속력`, 누르면 트랙 선택(고각은 빔 탭에서). 오른쪽 위 트랙별 상태 줄(사실 값만: 최근 결과 ● 갱신 × 미탐 ■ 종료, 연속 미탐 n/한도, 빔 안 위치 막대(1 초과 빨강, 0.7 이상 주황은 임시 기준), 마지막 갱신 뒤 지난 시간. 4행 고정(낮은 창 3행), 나머지는 휠, 문제 트랙이 맨 위, 가려진 문제 트랙은 ⚠로 알림, 행을 누르면 선택). 오른쪽 아래 고른 트랙의 잔차(예측−측정, 참값 오차 아님) 추이(최근 60사이클, 축은 게이트 하한 ×3까지, 넘는 값은 ▲, 게이트 값은 오른쪽 여백). 사이클 상세(① 예측 → ② 관측·연관 → ③ 결과, 빔 단면)는 '사이클 상세 ▸' 버튼이나 잔차 그래프를 눌러 전시기 자리에 덮는 패널로 연다(✕·Esc로 닫음, 사후 분석용) |
| 통신 | 연결이 살아 있고 메시지가 제때 오나? | 링크 카드 3개(처리부·운용통제기·모의기, 운용통제기 카드에 레이더 자세), 메시지 표(속도와 기대값) · 연결 이벤트 기록(좌우) |
| 분석 | 무슨 일이 있었나? | 세션 한 줄 요약(자세히는 `?`)과 처리부 실행 선택, 하위 탭 `[문제 확인]`(이벤트 타임라인 + 문제·이벤트·북마크 목록, 누르면 재생 위치 이동, 실행이 여럿이면 고른 실행만 그림) · `[통계]`(묶음 + 주의·이상 항목, `전체 항목 보기`, 항목을 누르면 분포) · `[트랙]`(실행 구간별 트랙 요약). 탭 제목에 문제 수(`분석 ⚑ n`) |

판정 기준(모니터에서 정한 값):

| 항목 | 정상 | 주의 | 이상 |
| --- | --- | --- | --- |
| 탐색 프레임 시간 | 기대값(칸 수 × 10 ms ÷ 탐색 점유율) ±10% | ±30% | 그 밖 |
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
├─ processor_logs/      [로그 가져오기]로 가져온 처리부 로그
└─ bookmarks.json       분석 탭 북마크 (북마크를 달았을 때만)
```

녹화 파일 크기는 처리부 시각 10분에 약 30 MB다(가짜 처리부, 트랙 4개 기준 측정. 트랙이 많으면 커진다). 녹화 중인 파일도 열 수 있다(끝부분은 잘라 읽음).

재생할 때는 상단 바 아래에 재생 바(재생/정지, ±5초, 속도 ×0.1~×50, 위치 막대)가 나온다. 빔·추적·통신 탭이 녹화 시점의
화면으로 바뀐다. `실시간으로`를 누르면 돌아간다. 재생하는 동안에도 실시간 수신과 녹화는 계속된다. `Space`로 재생/정지.

## 문제 자동 기록

모니터가 판정한 문제 상황을 사람이 누르지 않아도 사건 단위로 기록한다(`core/problems.py`). 테스트나 실제 운용 중
문제가 생기면 나중에 분석 탭에서 그 위치로 바로 간다.

- 기준은 실시간 판정과 같다(새 기준을 만들지 않음). 모니터 추정이 섞인 값(예측-측정 잔차, 연관 탐지 선택과 그에 기댄
  빔 안 위치)은 기록하지 않는다. 빔 안 위치는 추적 탭에서 보는 값으로만 쓴다.

  | 분류 | 기록하는 문제 | 수준 |
  | --- | --- | --- |
  | 빔 | 빠진 빔(사건 합계 1~5칸 / 6칸 이상), 운용 중 빔 없음(1초) | 주의 / 이상, 주의 |
  | 빔 | 탐색 프레임 시간·추적 주기 오차·탐색→확인 지연이 판정 기준 밖 | 기준과 같음 |
  | 추적 | 트랙 미탐(연속 미탐이 한도−1 이상이면 이상), 트랙 종료(처리부 트랙 목록에서 사라짐), 확인 실패 | 주의 / 이상 |
  | 통신 | 처리부 연결 끊김(다시 연결까지, 짧은 끊김도 사건으로 기록), 운용통제기·모의기 연결 안 됨·끊김, 처리부 재시작(처리부 시각이 1초 넘게 되돌아감) | 이상, 이상, 주의 |
  | 처리부 로그 | 메시지 로그의 요청 거부·경고·오류(로그를 가져온 경우) | 주의 / 이상(오류) |

- 같은 종류(트랙 문제는 같은 트랙)가 2초(처리부 시각) 안에 다시 생기면 한 건으로 합치고 횟수를 센다.
- 녹화를 열 때 처음부터 다시 계산한다(40초 녹화 약 0.2초). 기준을 고치면 예전 녹화에도 적용된다.
- 실시간에서는 같은 로직으로 감지해 분석 탭 제목(`분석 ⚑ n`)과 `[문제]` 목록에 보인다. 녹화가 꺼져 있으면 녹화 버튼 옆에
  '녹화 꺼짐'을 띄운다. 녹화가 없으면 나중에 그 위치로 돌아갈 수 없다.
- 분석 탭 `[문제 확인]`의 오른쪽 `[문제]` 목록(분류·'이상만' 거르기)과 타임라인 '문제' 줄. 누르면 재생 위치를 1초 전으로 옮기고,
  트랙 문제면 추적 탭에서 그 트랙을 고른다. 오른쪽 클릭 → '북마크로 남기기'로 원인 메모를 단다.
- 위치는 처리부 시각과 녹화 위치(녹화 시작 후 초)를 함께 남긴다. 녹화 중 처리부가 다시 시작해 처리부 시각이 반복돼도
  녹화 위치로 정확히 간다. 목록에 `[실행 n]`으로 몇 번째 처리부 실행인지 표시한다.
- 한계: 처리부 로그 문제는 처리부 시각만 있다. 로그 실행 수와 녹화의 처리부 실행 구간 수가 같으면 순서대로 짝짓고(추정),
  다르면 그 시각에 처음 닿은 곳으로 간다. 이벤트 타임라인은 처리부 시각 축이라 실행 여러 개가 겹쳐 그려진다.

## 북마크 (보조)

자동 기록이 못 잡은 시점(또는 구간)이나 원인 메모를 직접 남긴다. 세션(녹화·로그·캡처)을 연 뒤에 쓴다.

- 추가: 분석 탭 `+ 북마크 (B)` 또는 어느 탭에서든 `B` 키(글자 입력 칸 밖에서). 시작 시각은 그 순간의 재생 위치로 채워지고
  재생은 멈춘다. 끝 시각도 넣으면 구간 북마크다. 시각은 처리부 시각이고 `01:10.250`, `70.25`(초) 형식으로 넣는다.
  `[문제]` 목록에서 오른쪽 클릭 → '북마크로 남기기'로도 단다.
- 보기: 이벤트 타임라인 '북마크' 줄(▼ 시점, 막대 구간, 분류별 색)과 오른쪽 `[북마크]` 목록. 목록 행이나 타임라인 표시를
  누르면 재생 위치를 그 시각으로 옮기고, 트랙을 넣었으면 추적 탭에서 그 트랙을 고른다. 고른 북마크의 메모는 목록 아래에 보인다.
- 편집·삭제: 목록 행 두 번 누르기(편집), 오른쪽 클릭 메뉴(편집·삭제), `Delete` 키(삭제).
- 저장: 바꿀 때마다 바로 쓴다. 세션 폴더를 열었으면 그 폴더의 `bookmarks.json`, 파일 하나를 열었으면 같은 폴더의
  `<파일 이름>.bookmarks.json`. 녹화 위치(`recording_s`)와 처리부 실행 구간(`segment`)도 함께 저장해 처리부 재시작이 있어도
  위치가 하나로 정해진다(이전 형식 파일도 읽음). 쓰지 못하면(읽기 전용 폴더 등) 창으로 알린다.
- 한글 입력 상태에서는 `B` 키가 'ㅠ'로 들어가 단축키가 동작하지 않을 수 있다. 그때는 버튼을 쓴다.
- 실시간 화면(세션을 열지 않은 상태)에서는 북마크를 달 수 없다. 녹화한 뒤 그 세션을 열어서 단다.

## 처리부 로그 가져오기

상단 `메뉴`의 `로그 가져오기`는 `aesa_monitor/io/log_fetch.py`를 백그라운드로 실행한다. 명령줄에서도 같다.

```sh
python3 -m aesa_monitor.io.log_fetch --dest ~/aesa-records/test/processor_logs                 # monitor.env 설정
python3 -m aesa_monitor.io.log_fetch --source local --local-dir ~/aesa/closedloop/logs --dest DIR
python3 -m aesa_monitor.io.log_fetch --source ssh --target petalinux@192.168.10.20 --dest DIR
```

| 방식 | 언제 | 동작 |
| --- | --- | --- |
| local | 처리부를 이 PC에서 실행 | `MONITOR_LOG_LOCAL_DIR`의 `adp_*.log`, `adp_detection_*.csv`를 복사. 크기가 같으면 건너뜀 |
| ssh | 처리부를 보드에서 실행 | `scp -O ... petalinux@192.168.10.20:/opt/aesa/logs/adp_* DIR/`. 보드 dropbear에 맞춘 옵션 포함 |

저장 위치: 녹화 중이면 그 세션 폴더, 세션을 열어 둔 상태면 그 폴더, 아니면 새 세션 폴더의 `processor_logs/`.

**ssh 주의**
- 버튼에서는 비밀번호를 입력할 수 없다(BatchMode). 보드에 공개키를 먼저 등록해야 한다(아래 `보드 준비`).
- 보드는 처리부를 `/opt/aesa`에서 실행한다(`aesactl`). 실행 중에 가져오면 쓰는 중인 마지막 줄은 건너뛴다.
- 예전 기본값(`root@192.168.10.20`, `/tmp/aesa/logs`, 키 옵션 없는 dropbear 옵션)이 monitor.env에 그대로 있으면 지금 기본값으로 읽는다.

## 처리부 제어 (보드)

상단 `메뉴`의 `처리부 제어`는 `aesa_monitor/io/board_control.py`로 보드의 `aesactl`을 ssh로 실행한다(전체 경로 `/usr/local/sbin/aesactl`).
실패(포트가 열리지 않음, 로그를 SD로 복사하지 못함 등)는 보드 스크립트의 종료 코드로 전달되어 창에 `실패`로 보인다.
접속 정보는 로그 가져오기와 같은 `MONITOR_LOG_SSH_*`를 쓴다.

| 버튼 | 보드에서 실행 | 비고 |
| --- | --- | --- |
| 상태 | `sudo -n aesactl status` | 창을 열 때 한 번 자동 실행 |
| 시작 / 재시작 | `sudo -n aesactl start '노트북 시각'` | 보드에 시계가 없어 노트북 시각을 먼저 넣는다(로그 파일 이름이 이 시각) |
| 중지 | `sudo -n aesactl stop` | 로그를 보드 SD FAT(`aesa/logs`)로도 복사 |
| 시각 맞추기 | `sudo -n aesactl settime '노트북 시각'` | |
| 보드 끄기 | `sudo -n aesactl poweroff` | 처리부 중지·로그 저장 후 poweroff |

```sh
python3 -m aesa_monitor.io.board_control status     # 명령줄
```

### 보드 준비 (한 번)

`board/`의 스크립트를 SD FAT(`aesa/`)에 넣고 보드에서 `install_board.sh`를 한 번 실행한다. 자세한 순서는 `board/README.txt`.

| 파일 | 하는 일 |
| --- | --- |
| `board/run_aesa.sh` | 처리부 실행·관리. 설치 후 `/usr/local/sbin/aesactl` |
| `board/install_board.sh` | 부팅 때 eth0 고정 IP, init 스크립트(부팅 `aesactl boot`, 종료 `aesactl stop`), petalinux sudo 규칙(aesactl만), ssh 공개키. 보드(시리얼) 또는 리눅스 PC(`--root`)에서 실행 |
| `board/board.conf` | `AESA_AUTOSTART=1`이면 부팅하자마자 처리부 시작(기본 0: 모니터 [시작]으로) |
| `windows/prepare_board_sd.bat` | 노트북에서: 위 파일을 SD에 복사, 노트북 키(`~/.ssh/aesa_board_ed25519`) 생성, 공개키를 SD `aesa/authorized_keys`에 넣음 |

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
| `MONITOR_SOURCE` | capture | 데이터 출처: capture(통신 캡처) 또는 copy(복사본 수신, 이전 방식) |
| `MONITOR_CAPTURE_COMMAND` | `tcpdump -i any --immediate-mode -U -s 0 -w - tcp port {port}` | capture: 표준 출력으로 pcap을 내는 명령 |
| `MONITOR_RECORD_DIR` | `~/aesa-records` | 세션 폴더를 만들 곳 |
| `MONITOR_FONT_SIZE` | 12 | 본문 글자 크기(8~32) |
| `MONITOR_AUTO_RECORD` | 0 | 1이면 시작하자마자 녹화 |
| `MONITOR_LOG_SOURCE` | local | local 또는 ssh |
| `MONITOR_LOG_LOCAL_DIR` | (빈 값) | local: 처리부 logs 폴더 |
| `MONITOR_LOG_SSH_TARGET` | petalinux@192.168.10.20 | ssh: 사용자@주소 (처리부 제어도 같이 씀) |
| `MONITOR_LOG_SSH_PORT` | 22 | ssh 포트 |
| `MONITOR_LOG_REMOTE_DIR` | /opt/aesa/logs | 보드의 로그 폴더 |
| `MONITOR_LOG_SSH_OPTIONS` | dropbear용 옵션 + `IdentityFile=~/.ssh/aesa_board_ed25519` | scp·ssh에 그대로 붙이는 옵션 |

## 구조

```
aesa_monitor/
├─ core/      화면과 무관한 계산 (tkinter 없음)
│   protocol(ICD 해석·인코딩) events config settings geometry
│   state(누적 상태) tracking(사이클 재구성) traffic(수신 속도) verdict(판정) stats(통계 요약)
│   problems(문제 자동 기록)
│   capture(패킷 → TCP 재조립 → 메시지)
├─ io/        connection(복사본 수신, 출처 만들기) capture(통신 캡처 출처, 캡처 파일) pcap(pcap·pcapng 읽기)
│             recording(녹화 파일) replay(재생) session(세션 열기) bookmarks(분석 북마크 파일)
│             processor_log(처리부 로그 해석) log_fetch(로그 가져오기)
└─ ui/        app(메인 창) style widgets beam_tab track_tab comm_tab analysis_tab fetch_dialog bookmark_dialog
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
