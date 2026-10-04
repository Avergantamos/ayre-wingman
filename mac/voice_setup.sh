#!/bin/bash
# Run once after training: point the voice at Ayre's newest weights, install the voice service,
# pre-render her stock lines. Rerun after retraining.
set -e
SOVITS=~/Claude/Projects/GPT-SoVITS
REPO=~/Claude/Projects/ayre-wingman
GPT=$(ls -t "$SOVITS"/GPT_weights_v2Pro/ayre-e*.ckpt | head -1)
VITS=$(ls -t "$SOVITS"/SoVITS_weights_v2Pro/ayre_e*.pth | head -1)
echo "voice weights: $(basename "$GPT"), $(basename "$VITS")"
cat > "$SOVITS/GPT_SoVITS/configs/tts_ayre.yaml" <<YAML
custom:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: $GPT
  version: v2Pro
  vits_weights_path: $VITS
YAML
cat > "$REPO/voice_bridge/config.json" <<JSON
{
 "host": "0.0.0.0",
 "port": 9881,
 "sovits_url": "http://127.0.0.1:9880",
 "ref_audio": "$REPO/voice/clips/0601.wav",
 "ref_text": "Raven, those unidentified machines were using encrypted communications.",
 "gpt_weights": "$GPT",
 "sovits_weights": "$VITS",
 "speed": 1.0
}
JSON
cat > ~/Library/LaunchAgents/com.ayre.voice.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ayre.voice</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/mac/voice_server.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/ayre-voice.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ayre-voice.log</string>
</dict></plist>
PLIST
launchctl unload ~/Library/LaunchAgents/com.ayre.voice.plist 2>/dev/null || true
rm -rf "$REPO/voice/cache"   # lines rendered with older weights
launchctl load -w ~/Library/LaunchAgents/com.ayre.voice.plist
echo "waiting for the voice service..."
until curl -s -o /dev/null http://127.0.0.1:9881/v1/voices; do sleep 2; done
echo "pre-rendering her stock lines..."
/usr/bin/python3 "$REPO/voice_bridge/server.py" --prerender
echo "VOICE READY on http://$(scutil --get LocalHostName).local:9881/v1"
