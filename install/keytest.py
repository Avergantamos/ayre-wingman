"""Checks that every one of Ayre's keys leaves Windows correctly, before trying them in game.

Run on the PC with Star Citizen CLOSED, from C:\\ayre-wingman, with Notepad focused:
    py -3.11 install\\keytest.py
It sends each key through the same `keyboard` package Wingman uses (vendored in this repo),
listens with a global hook, and reports keys that never arrived, arrived wrong, or got stuck.
It does not check that Star Citizen accepts them; the hangar test does that.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import keyboard.keyboard as keyboard  # noqa: E402  the same import Wingman uses

if sys.platform != "win32":
    sys.exit("Run this on the Windows PC.")
running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq StarCitizen.exe"], capture_output=True, text=True).stdout
if "StarCitizen.exe" in running:
    sys.exit("Close Star Citizen first: this presses every Ayre key for real.")
if input("Open Notepad first. Type YES, press Enter, then click into Notepad within 5 seconds: ") != "YES":
    sys.exit("Cancelled.")
for n in range(5, 0, -1):
    print(f"Starting in {n}... click into Notepad", flush=True)
    time.sleep(1)

ACTIONS = json.loads((ROOT / "skills/ayre_eyes/ayre_actions.json").read_text())
seen: list = []
hook = keyboard.hook(lambda e: seen.append((e.event_type, e.name, e.scan_code, getattr(e, "is_keypad", None))))


def send(step: dict) -> None:
    """Same logic as Wingman's execute_action for keyboard steps."""
    k = step["keyboard"]
    codes = k.get("hotkey_codes")
    numpad = any(t.strip().startswith("num ") for t in k["hotkey"].lower().split("+"))
    if k.get("press") == k.get("release"):
        if codes and len(codes) == 1 and not numpad:
            keyboard.direct_event(codes[0], 0 + (1 if k.get("hotkey_extended") else 0))
            time.sleep(k.get("hold") or 0.1)
            keyboard.direct_event(codes[0], 2 + (1 if k.get("hotkey_extended") else 0))
        else:
            keyboard.press(k["hotkey"])
            time.sleep(k.get("hold") or 0.1)
            keyboard.release(k["hotkey"])
    else:
        keyboard.send(k["hotkey"], k.get("press"), k.get("release"))


problems = []
for action, steps in sorted(ACTIONS.items()):
    keys = [s for s in steps if "keyboard" in s]
    if not keys or any("mouse" in s for s in steps):
        continue  # mouse clicks would click whatever is under the pointer
    seen.clear()
    for step in steps:
        if "wait" in step:
            time.sleep(step["wait"])
        elif "keyboard" in step:
            send(step)
    time.sleep(0.15)
    downs = [s for s in seen if s[0] == "down"]
    ups = [s for s in seen if s[0] == "up"]
    want = keys[-1]["keyboard"]["hotkey"].split("+")[-1].strip().lower()
    names = {(s[1] or "").lower() for s in downs}
    if not downs:
        problems.append(f"{action}: nothing arrived")
    elif want not in names and not any(want.replace("num ", "") == n for n in names):
        problems.append(f"{action}: expected '{want}', Windows saw {sorted(names)}")
    elif len(ups) < len(downs):
        problems.append(f"{action}: a key stayed held down")
    if action == "v_open_all_doors" and not any(s[2] == 28 and s[3] for s in downs):
        problems.append("v_open_all_doors: arrived as main Enter, not numpad Enter (would open chat)")

keyboard.unhook(hook)
stuck = [k for k in ("ctrl", "alt", "right ctrl", "right alt", "shift") if keyboard.is_pressed(k)]
print(f"\nChecked {len(ACTIONS)} actions.")
for p in problems:
    print("PROBLEM", p)
if stuck:
    print("PROBLEM modifiers still held after the test:", stuck)
print("All keys arrived correctly." if not problems and not stuck else "Send these lines to the Mac session.")
