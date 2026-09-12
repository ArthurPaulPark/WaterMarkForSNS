#!/bin/bash
# 파인더에서 더블클릭하면 실행됩니다.
cd "$(dirname "$0")" || exit 1

URL="http://127.0.0.1:8765"
echo
echo "  ┌──────────────────────────────┐"
echo "  │   WaterMark                  │"
echo "  └──────────────────────────────┘"
echo

stop() { echo; echo "  종료되었습니다. 이 창은 닫아도 됩니다."; }
trap stop EXIT

# 이미 켜져 있으면 새로 띄우지 않고 브라우저만 연다
if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  이미 켜져 있습니다. 브라우저를 엽니다."
  open "$URL"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "  ✗ python3 이 설치돼 있지 않습니다."
  echo "    https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요."
  echo
  read -n 1 -s -r -p "  아무 키나 누르면 닫힙니다."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "  처음 실행이라 준비가 필요합니다 (1~2분, 인터넷 연결 필요)"
  echo "  ─ 이 컴퓨터 안에서만 쓰는 프로그램이고, 사진은 어디로도 전송되지 않습니다."
  echo
  LOG="$(mktemp -t watermark-setup)"
  # pip 의 잡다한 경고는 비전공자에게 불필요하게 겁을 준다. 로그로 보내고
  # 실패했을 때만 어디를 봐야 하는지 알려준다.
  if ! { python3 -m venv .venv &&
         .venv/bin/pip install --upgrade pip &&
         .venv/bin/pip install -r requirements.txt; } >"$LOG" 2>&1; then
    echo "  ✗ 준비에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해 보세요."
    echo "    자세한 내용: $LOG"
    echo
    read -n 1 -s -r -p "  아무 키나 누르면 닫힙니다."
    exit 1
  fi
  rm -f "$LOG"
  echo "  준비 끝. 다음부터는 바로 열립니다."
  echo
fi

echo "  브라우저에서 $URL 이 열립니다."
echo "  끄려면  ▸ 이 창을 닫거나 Control-C"
echo "         ▸ 10분간 쓰지 않으면 저절로 꺼집니다"
echo
( sleep 2; open "$URL" ) &
.venv/bin/python server.py
