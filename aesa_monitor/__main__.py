"""모니터 실행 진입점.

    python3 -m aesa_monitor --env ../aesa-processor/.env             실시간 모니터 (복사본 수신)
    python3 -m aesa_monitor --env ../aesa-processor/.env --source capture   실시간 모니터 (통신 캡처)
    python3 -m aesa_monitor --open capture.pcapng                     캡처 파일 열기(재생, 분석)
    python3 -m aesa_monitor --open ~/aesa-records/20261112_101500    녹화·로그 열기(재생, 분석)
    python3 -m aesa_monitor --stats ~/aesa-records/20261112_101500   화면 없이 통계 요약만 출력
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path
from typing import List, Optional

from aesa_monitor.core.config import ConfigError, MonitorConfig, TrackingParameters, load_config
from aesa_monitor.core.events import PEER_CONSOLE, PEER_SIMULATOR, Event
from aesa_monitor.core.protocol import BeamType, MessageId
from aesa_monitor.core.settings import DATA_SOURCES, DEFAULT_SETTINGS_PATH, load_settings
from aesa_monitor.core.state import MonitorState


def parse_arguments(arguments: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aesa_monitor", description="AESA 데이터 처리부 모니터·분석기")
    parser.add_argument("--env", type=Path, default=Path(".env"),
                        help="처리부와 같은 .env 파일 경로 (기본값: 현재 디렉터리의 .env)")
    parser.add_argument("--server", help="처리부 서버 주소 ADDRESS:PORT. .env 값을 덮어쓴다")
    parser.add_argument("--local", help="모니터가 바인딩할 주소 ADDRESS:PORT. .env 값을 덮어쓴다")
    parser.add_argument("--source", choices=DATA_SOURCES,
                        help="데이터 출처: copy(처리부 복사본 수신) 또는 capture(통신 캡처). 기본값은 설정 파일")
    parser.add_argument("--capture-command",
                        help="캡처 명령. 표준 출력으로 pcap을 내야 한다. {port}는 처리부 포트로 바뀐다")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH,
                        help="모니터 설정 파일 (기본값: 프로젝트 폴더의 monitor.env)")
    parser.add_argument("--record", action="store_true", help="시작하자마자 녹화한다")
    parser.add_argument("--open", type=Path, help="세션 폴더나 녹화 파일을 열어 재생·분석한다")
    parser.add_argument("--offline", action="store_true", help="처리부에 접속하지 않는다 (--open과 함께)")
    parser.add_argument("--stats", type=Path, help="세션 폴더의 통계 요약을 화면 없이 출력한다")
    parser.add_argument("--headless", action="store_true", help="화면 없이 1초마다 수신 요약을 출력한다")
    parser.add_argument("--duration", type=float, default=None,
                        help="--headless에서 지정한 초만큼 실행한 뒤 종료한다")
    parser.add_argument("--font-size", type=int, help="글자 크기 (기본값은 설정 파일, 없으면 12)")
    parser.add_argument("--tab", choices=("beam", "track", "comm", "analysis"), help="처음에 보여줄 탭")
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--quit-after", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--geometry", help=argparse.SUPPRESS)
    return parser.parse_args(arguments)


def resolve_config(arguments: argparse.Namespace) -> MonitorConfig:
    env_path: Optional[Path] = arguments.env
    # 서버와 로컬 주소를 모두 지정하면 .env 파일이 없어도 실행할 수 있다.
    if not env_path.is_file() and arguments.server and arguments.local:
        env_path = None
    return load_config(env_path, arguments.server, arguments.local)


def run_headless(events: "queue.Queue[Event]", duration: Optional[float]) -> None:
    state = MonitorState()
    started = time.monotonic()
    next_report = started + 1.0
    while duration is None or time.monotonic() - started < duration:
        try:
            state.apply(events.get(timeout=0.1))
        except queue.Empty:
            pass
        if time.monotonic() >= next_report:
            next_report += 1.0
            print(summarize(state), flush=True)


def summarize(state: MonitorState) -> str:
    occupancy = state.occupancy(5000)
    status = state.status

    def flag(value: Optional[bool]) -> str:
        return "?" if value is None else str(int(value))

    peers = (f"source={state.source} console={flag(state.peer_connected(PEER_CONSOLE))} "
             f"simulator={flag(state.peer_connected(PEER_SIMULATOR))} paused={flag(state.paused())}")
    if status is not None:
        peers += f" scale={status.time_scale:g}"
    sweep = state.search_sweep_ms()
    reason = "" if state.connected else f" reason={state.connection_detail!r}"
    return (
        f"connected={int(state.connected)}{reason} time_ms={state.now_ms()} {peers} "
        f"beams={len(state.beams)} cells={len(state.search_cells)} "
        f"search={occupancy[BeamType.SEARCH]:.2f} "
        f"confirm={occupancy[BeamType.CONFIRMATION]:.2f} "
        f"track={occupancy[BeamType.TRACKING]:.2f} "
        f"sweep_ms={sweep} tracks={len(state.tracks)} "
        f"detections={state.traffic.counts[MessageId.PLOT_INFORMATION]}"
    )


def print_stats(path: Path, parameters: TrackingParameters) -> int:
    from aesa_monitor.io.session import load_session

    session = load_session(path, parameters)
    for line in session.describe():
        print(line)
    report = session.report
    if report is None:
        print("통계를 만들 데이터가 없습니다")
        return 1
    group = None
    for item in report.items:
        if item.group != group:
            group = item.group
            print(f"\n[{group}]")
        reference = f"  (기준 {item.reference})" if item.reference else ""
        mark = {"ok": "정상", "warn": "주의", "bad": "이상"}.get(item.level, "")
        print(f"  {item.name}: {item.value}{reference} {mark}".rstrip())
        if item.note:
            print(f"      - {item.note}")
    if report.tracks:
        print("\n[트랙별]")
        print("  ID  시작(s)  수명(s)  추적빔  미탐  최대연속  RMS(m)  상태")
        for track in report.tracks:
            rms = "-" if track.rms_angle_error_m is None else f"{track.rms_angle_error_m:.1f}"
            state = "종료" if track.ended else "진행"
            if not track.confirmed:
                state = "확인 실패"
            print(f"  {track.track_id:<3} {track.first_ms / 1000:7.1f}  {track.lifetime_s:7.1f}  "
                  f"{track.tracking_beams:6d}  {track.misses:4d}  {track.max_consecutive_misses:8d}  "
                  f"{rms:>6}  {state}")
    return 0


def main(arguments: Optional[List[str]] = None) -> int:
    options = parse_arguments(arguments)
    try:
        settings = load_settings(options.settings)
    except ConfigError as error:
        print(f"monitor: {options.settings}: {error}", file=sys.stderr)
        return 2

    offline = options.offline or options.stats is not None
    config: Optional[MonitorConfig] = None
    try:
        config = resolve_config(options)
    except ConfigError as error:
        if not offline and options.open is None:
            print(f"monitor: {error}", file=sys.stderr)
            return 2
        offline = True

    if options.stats is not None:
        return print_stats(options.stats, config.tracking if config else TrackingParameters())

    from aesa_monitor.io.connection import create_source

    events: "queue.Queue[Event]" = queue.Queue()
    connection = None
    if config is not None and not offline:
        connection = create_source(options.source or settings.data_source, config, events,
                                   options.capture_command or settings.capture_command)
        connection.start()
    try:
        if options.headless:
            if connection is not None:
                print(f"monitor: {connection.description}", flush=True)
            recorder = None
            if options.record and connection is not None:
                from aesa_monitor.io.recording import RECORDING_NAME, Recorder, new_session_directory

                directory = new_session_directory(settings.record_path)
                recorder = Recorder(directory / RECORDING_NAME, connection.recording_meta())
                connection.attach_recorder(recorder)
                print(f"monitor: 녹화 {directory}", flush=True)
            try:
                run_headless(events, options.duration)
            finally:
                if recorder is not None:
                    connection.attach_recorder(None)
                    recorder.close()
        else:
            import tkinter as tk

            from aesa_monitor.ui.app import MonitorApp

            root = tk.Tk()
            app = MonitorApp(root, events, config, settings, options.settings, connection)
            if options.font_size:
                app.set_font_size(options.font_size)
            if options.geometry:
                root.geometry(options.geometry)
            if options.record:
                app.start_recording()
            if options.open is not None:
                app.open_session(options.open)
            if options.tab:
                app.select_tab(options.tab)
            if options.screenshot is not None or options.quit_after is not None:
                app.schedule_exit(options.quit_after or 3.0, options.screenshot)
            root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if connection is not None:
            connection.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
