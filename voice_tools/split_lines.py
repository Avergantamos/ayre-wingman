"""Split a long voice-lines recording into one wav per line, then transcribe each.

Usage: voice/.venv/bin/python voice_tools/split_lines.py voice/raw/<file>.wav
Writes voice/clips/NNNN.wav (mono 32 kHz) and voice/clips/lines.list in
GPT-SoVITS format: path|speaker|lang|text
"""
import re
import subprocess
import sys
from pathlib import Path

NOISE_DB = -45      # quieter than this counts as a gap between lines
MIN_GAP = 0.35      # seconds of quiet that ends a line
MIN_LEN, MAX_LEN = 1.2, 14.0  # training wants short, complete lines
PAD = 0.12          # keep a little air around each line

src = Path(sys.argv[1])
out = Path("voice/clips")
out.mkdir(parents=True, exist_ok=True)

log = subprocess.run(
    ["ffmpeg", "-hide_banner", "-i", str(src), "-af",
     f"silencedetect=noise={NOISE_DB}dB:d={MIN_GAP}", "-f", "null", "-"],
    capture_output=True, text=True).stderr
starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", log).groups()
total = int(h) * 3600 + int(m) * 60 + float(s)

# speech runs from the end of one gap to the start of the next
bounds = list(zip([0.0] + ends, starts + [total]))
segments = [(max(0, a - PAD), b + PAD) for a, b in bounds if MIN_LEN <= b - a <= MAX_LEN]
print(f"{len(bounds)} spans, {len(segments)} kept ({sum(b - a for a, b in segments) / 60:.1f} min)")

for i, (a, b) in enumerate(segments):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
                    "-ac", "1", "-ar", "32000", str(out / f"{i:04d}.wav")], check=True)

from faster_whisper import WhisperModel  # noqa: E402

model = WhisperModel("small.en", device="cpu", compute_type="int8")
with open(out / "lines.list", "w") as f:
    for i in range(len(segments)):
        wav = out / f"{i:04d}.wav"
        parts, _ = model.transcribe(str(wav), language="en", beam_size=5, vad_filter=False)
        text = " ".join(p.text.strip() for p in parts).strip()
        if text:
            f.write(f"{wav.name}|ayre|en|{text}\n")
        if i % 100 == 0:
            print(f"transcribed {i}/{len(segments)}", flush=True)
print("done")
