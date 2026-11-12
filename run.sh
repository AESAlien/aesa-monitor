#!/bin/sh
# aesa-monitor 실행. 인자는 python3.12 -m aesa_monitor에 그대로 넘긴다.
# 어느 폴더에서 실행해도 되도록 이 폴더를 PYTHONPATH에 넣는다(상대 경로 인자는 현재 폴더 기준).
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m aesa_monitor "$@"
