"""Carry the pilot's own Wingman settings for Ayre (push-to-talk key, mouse or stick button, voice
activation, skill settings such as the Spotify client id) from the installed Ayre.yaml into the
new one, so reinstalling never resets them.
Usage: py -3.11 install/keep_pilot_settings.py <old Ayre.yaml> <new Ayre.yaml>"""
import sys
from pathlib import Path

import yaml

KEEP = ["record_key", "record_key_codes", "record_mouse_button", "record_joystick_button",
        "is_voice_activation_default"]

old_path, new_path = Path(sys.argv[1]), Path(sys.argv[2])
if not old_path.exists():
    sys.exit(0)
old, new = yaml.safe_load(old_path.read_text()) or {}, yaml.safe_load(new_path.read_text())
kept = {k: old[k] for k in KEEP if k in old and old[k] is not None}
# skill settings made in the Wingman app (Spotify client id, ...) live in a `skills` list the
# template doesn't have; keep each one the template doesn't define itself
mine = {s.get("module") for s in new.get("skills") or []}
own_skills = [s for s in old.get("skills") or [] if s.get("module") not in mine]
if own_skills:
    kept["skills"] = (new.get("skills") or []) + own_skills
if kept:
    new.update(kept)


    class D(yaml.SafeDumper):
        pass
    D.add_representer(str, lambda d, s: d.represent_scalar(
        "tag:yaml.org,2002:str", s, style="|" if "\n" in s else None))
    new_path.write_text(yaml.dump(new, Dumper=D, sort_keys=False, allow_unicode=True, width=1000))
shown = {k: ([s.get("module") for s in v] if k == "skills" else v) for k, v in kept.items()}  # no secrets in the log
print("kept your settings: " + (", ".join(f"{k}={shown[k]}" for k in shown) if shown else "none set"))
