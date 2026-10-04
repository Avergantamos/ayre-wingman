"""Ayre's voice bridge: an OpenAI-shaped speech endpoint in front of GPT-SoVITS.

Wingman AI's "OpenAI compatible" TTS provider posts here; we ask the local GPT-SoVITS
api_v2 server for Ayre's voice and hand back WAV. Every line is cached on disk, so a
line she has said before (confirmations, ready lines, callouts) plays instantly.

Run:   python voice_bridge/server.py           (listens on 127.0.0.1:9881)
Needs: GPT-SoVITS api_v2 running (default 127.0.0.1:9880) with Ayre's trained weights,
       and voice_bridge/config.json (copy config.example.json).
Wingman Ayre config: tts provider openai_compatible, base_url http://127.0.0.1:9881/v1,
       api_key anything, output_streaming false.
"""
import hashlib
import json
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text()) if (HERE / "config.json").exists() else \
    json.loads((HERE / "config.example.json").read_text())
CACHE = HERE.parent / "voice" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)


def sovits(path, payload=None):
    req = urllib.request.Request(CONFIG["sovits_url"] + path, method="POST" if payload else "GET",
                                 data=json.dumps(payload).encode() if payload else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def speak(text):
    """WAV bytes for text in Ayre's voice, from cache when she has said it before."""
    split, gap = CONFIG.get("split", "cut4"), CONFIG.get("fragment_interval", 0.12)
    key = hashlib.sha1(f"{CONFIG['ref_audio']}|{CONFIG.get('speed', 1.0)}|{split}|{gap}|{text}".encode()).hexdigest()
    cached = CACHE / f"{key}.wav"
    if cached.exists():
        return cached.read_bytes()
    wav = sovits("/tts", {
        "text": text, "text_lang": "en",
        "ref_audio_path": CONFIG["ref_audio"], "prompt_text": CONFIG["ref_text"], "prompt_lang": "en",
        "speed_factor": CONFIG.get("speed", 1.0),
        # cut4 splits at sentence ends only (cut5 split at every comma, each joined by a pause)
        "text_split_method": split, "fragment_interval": gap,
        "media_type": "wav", "streaming_mode": False,
    })
    cached.write_bytes(wav)
    return wav


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.rstrip("/").endswith("/audio/speech"):
            return self.send_error(404)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            wav = speak(body["input"])
        except Exception as e:  # GPT-SoVITS down or failed: tell Wingman, don't hang
            return self.send_error(502, f"GPT-SoVITS: {e}")
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.end_headers()
        self.wfile.write(wav)

    def do_GET(self):  # Wingman may list voices
        out = json.dumps({"voices": [{"voice_id": "ayre", "name": "Ayre"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if "--load-weights" in sys.argv:  # point GPT-SoVITS at Ayre's trained models
        sovits(f"/set_gpt_weights?weights_path={CONFIG['gpt_weights']}")
        sovits(f"/set_sovits_weights?weights_path={CONFIG['sovits_weights']}")
    if "--prerender" in sys.argv:  # render her stock lines ahead of time
        lines = [l.strip() for l in (HERE / "stock_lines.txt").read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
        for i, line in enumerate(lines, 1):
            speak(line)
            print(f"{i}/{len(lines)} {line}")
        sys.exit()
    host, port = CONFIG.get("host", "127.0.0.1"), CONFIG.get("port", 9881)
    print(f"Ayre voice bridge on http://{host}:{port}/v1", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
