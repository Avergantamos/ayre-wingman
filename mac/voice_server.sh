#!/bin/bash
# Ayre's voice on the Mac: GPT-SoVITS engine (local only, port 9880) + the bridge the PC uses (9881).
SOVITS=~/Claude/Projects/GPT-SoVITS
REPO=~/Claude/Projects/ayre-wingman
cd "$SOVITS" || exit 1
.venv/bin/python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_ayre.yaml &
ENGINE=$!
trap 'kill $ENGINE 2>/dev/null' EXIT TERM INT
until curl -s -o /dev/null http://127.0.0.1:9880/docs; do
  kill -0 $ENGINE 2>/dev/null || exit 1   # engine died while starting
  sleep 1
done
/usr/bin/python3 "$REPO/voice_bridge/server.py"
