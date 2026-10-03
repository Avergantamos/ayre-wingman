"""Keep complete sentences from voice/clips/lines.list and fix AC6 names Whisper mishears.

Writes voice/clips/train.list (the training set) and prints how many minutes it holds.
"""
import re
import wave
from pathlib import Path

CLIPS = Path("voice/clips")
FIXES = {r"\bKor+a\b": "Coral", r"\bCorral\b": "Coral", r"\bRubikon\b": "Rubicon",
         r"\b6-21\b": "621", r"\bAllmine?d?\b": "ALLMIND", r"\bAll Mind\b": "ALLMIND"}

kept, seconds = [], 0.0
for line in (CLIPS / "lines.list").read_text().splitlines():
    name, spk, lang, text = line.split("|", 3)
    for pat, rep in FIXES.items():
        text = re.sub(pat, rep, text, flags=re.I)
    # complete sentences only: capital start, real ending, not a two-word scrap
    if not (text[:1].isupper() and text[-1:] in ".!?" and len(text.split()) >= 3):
        continue
    with wave.open(str(CLIPS / name)) as w:
        seconds += w.getnframes() / w.getframerate()
    kept.append(f"{name}|{spk}|{lang}|{text}")

(CLIPS / "train.list").write_text("\n".join(kept) + "\n")
print(f"{len(kept)} lines, {seconds / 60:.1f} min")
