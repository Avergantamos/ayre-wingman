"""Tests for skills/ayre_ship with stubbed Wingman modules. Run with the voice venv python."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import time
import types
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2] / "skills" / "ayre_ship"

# ---- stubs
api = types.ModuleType("api")
interface = types.ModuleType("api.interface")


class CommandConfig:
    @staticmethod
    def model_validate(d):
        return types.SimpleNamespace(**d)


interface.CommandConfig = CommandConfig
interface.SettingsConfig = interface.SkillConfig = interface.WingmanInitializationError = object
skills_pkg = types.ModuleType("skills")
skill_base = types.ModuleType("skills.skill_base")


def tool(**kw):
    return lambda f: f


class Skill:
    def __init__(self, config, settings, wingman):
        self.config, self.settings, self.wingman = config, settings, wingman
        self.printr = types.SimpleNamespace(print=lambda *a, **k: print("PRINTR", *a))

    async def validate(self):
        return []

    async def prepare(self):
        pass

    async def unload(self):
        pass

    async def get_prompt(self):
        return None

    def retrieve_custom_property_value(self, pid, errors):
        return self.props.get(pid)

    def get_generated_files_dir(self):
        return self.gen_dir


skill_base.tool, skill_base.Skill = tool, Skill
sys.modules.update({"api": api, "api.interface": interface, "skills": skills_pkg, "skills.skill_base": skill_base})
spec = importlib.util.spec_from_file_location("ayre_ship_main", SKILL / "main.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PubSub:
    def __init__(self):
        self.subs = {}

    def subscribe(self, e, fn):
        self.subs.setdefault(e, []).append(fn)

    def unsubscribe(self, e, fn):
        self.subs[e].remove(fn)

    async def publish(self, e, data=None):
        for fn in list(self.subs.get(e, [])):
            await fn(data)


class FakeWingman:
    def __init__(self):
        self.name = "Ayre"
        self.presses, self.spoken = [], []
        self.audio_player = types.SimpleNamespace(playback_events=PubSub())

    async def execute_action(self, cmd):
        self.presses.append((cmd.name, cmd.actions))

    async def play_to_user(self, text, no_interrupt=False):
        self.spoken.append(text)


class FakeVolume:
    available = True

    def __init__(self):
        self.levels = {"Spotify.exe:1:0": 0.8}
        self.calls = 0

    def init_thread(self):
        pass

    def snapshot(self, names):
        return {k: v for k, v in self.levels.items() if k.split(":")[0] in names}

    def set(self, key, level):
        self.calls += 1
        self.levels[key] = level


results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def make_skill(tmp):
    s = m.AyreShip(None, None, FakeWingman())
    s.props = {"game_log_path": str(Path(tmp, "Game.log")), "music_processes": "Spotify.exe", "duck_level": 30}
    s.gen_dir = str(Path(tmp, "gen"))
    return s


# ---- log watcher
async def test_watcher():
    tmp = tempfile.mkdtemp()
    log = Path(tmp, "Game.log")
    clock = [1000.0]
    w = m.LogWatcher(SKILL / "events.yaml", Path(tmp, "shapes"), clock=lambda: clock[0])
    p = str(log)
    check("watcher: missing file is quiet", w.poll(p) == [])
    log.write_text("<2026-10-03T10:00:00.000Z> Log started on Sat Oct 3\n")
    out = w.poll(p)
    check("watcher: new file read from start, session_start recorded", w.recent and w.recent[-1]["name"] == "session_start" and out == [])

    with open(log, "a") as f:
        f.write("<2026-10-03T10:00:01.000Z> [Notice] QuantumDrive spool started id 12345 0xDEADBEEF\n<2026-10-03T10:00:01.0")
    out = w.poll(p)
    check("watcher: quantum_start fires and speaks", len(out) == 1 and w.recent[-1]["name"] == "quantum_start")
    with open(log, "a") as f:
        f.write("00Z> partial line completed\n<x> QuantumDrive spool started again\n")
    clock[0] += 5
    out = w.poll(p)
    check("watcher: partial line joined, cooldown blocks repeat", out == [] and w.recent[-1]["name"] == "quantum_start" and len(w.recent) == 2)
    with open(log, "a") as f:
        f.write("<x> QuantumDrive arrived at destination\n")
    clock[0] += 2
    out = w.poll(p)
    check("watcher: second event within 10 s recorded but silent", out == [] and w.recent[-1]["name"] == "quantum_end")
    with open(log, "a") as f:
        f.write("<x> <RequestLocationInventory> Player[a] Location[Stanton1_Lorville]\n")
    clock[0] += 61
    w.poll(p)
    check("watcher: named group detail", w.recent[-1]["detail"] == "Stanton1_Lorville")

    # truncation: new session written shorter than old position
    log.write_text("Log started on Sun\n")
    clock[0] += 61
    w.poll(p)
    check("watcher: truncation re-reads new session", w.recent[-1]["name"] == "session_start")
    # rotation: file replaced (new inode) and bigger than old pos
    os.remove(log)
    log.write_text("x" * 500 + "\n" + "<y> QuantumDrive spool started\n")
    clock[0] += 61
    out = w.poll(p)
    check("watcher: rotation re-reads and speaks", w.recent[-1]["name"] == "quantum_start" and len(out) == 1)

    shapes = Path(tmp, "shapes", time.strftime("%Y-%m-%d") + ".txt").read_text().splitlines()
    check("watcher: masked shape sample written", "[Notice] QuantumDrive spool started id # <hex>" in shapes
          and all(not any(c.isdigit() for c in s) for s in shapes))
    w2 = m.LogWatcher(SKILL / "events.yaml", Path(tmp, "shapes2"))
    log.write_text("".join(f"line kind {chr(65 + i % 26)}{chr(65 + i // 26 % 26)} {i}\n" for i in range(1000)))
    w2.path, w2.first = p, False
    w2.poll(p)
    check("watcher: sample capped at 300", len(Path(tmp, "shapes2", time.strftime("%Y-%m-%d") + ".txt").read_text().splitlines()) == 300)

    # startup with an existing log: old content is skipped
    w3 = m.LogWatcher(SKILL / "events.yaml", Path(tmp, "shapes3"))
    log.write_text("<y> QuantumDrive spool started\n")
    check("watcher: existing log skipped at startup", w3.poll(p) == [] and not w3.recent)

    # skill loop: speaks via play_to_user, idle CPU while sleeping ~1 s per tick
    s = make_skill(tmp)
    s.props["duck_level"] = 30
    await s.prepare()
    log.write_text("")
    await asyncio.sleep(1.2)
    with open(log, "a") as f:
        f.write("<z> QuantumDrive spool started\n")
    t0, c0 = time.monotonic(), time.process_time()
    await asyncio.sleep(3.1)
    cpu = time.process_time() - c0
    check("skill: watcher loop spoke the line", s.wingman.spoken and s.wingman.spoken[0] in ("Quantum.", "Spooling up, Raven.", "Going quantum."))
    check(f"skill: idle CPU over 3 s = {cpu * 1000:.1f} ms (< 50 ms)", cpu < 0.05)
    prompt = await s.get_prompt()
    check("skill: get_prompt lists events", prompt and "quantum_start" in prompt)
    await s.unload()
    check("skill: watcher task cancelled on unload", s._watch_task is None)


# ---- ducking
async def test_ducking():
    vol = FakeVolume()
    d = m.Ducker(vol, lambda: ["Spotify.exe"], lambda: 0.3, fade_s=0.05)
    await d.duck("Ayre")
    check("duck: started lowers to 30% of app volume", abs(vol.levels["Spotify.exe:1:0"] - 0.3) < 1e-9 and vol.calls == 6)
    await d.duck("Ayre")
    check("duck: second started does not re-save the ducked level", d.saved == {"Spotify.exe:1:0": 0.8})
    await d.restore("Ayre")
    check("duck: finished restores", abs(vol.levels["Spotify.exe:1:0"] - 0.8) < 1e-9 and not d.saved)

    tmp = tempfile.mkdtemp()
    s = make_skill(tmp)
    await s.prepare()
    s.ducker.backend = vol
    s.ducker.fade_s = 0.05
    pub = s.wingman.audio_player.playback_events
    await pub.publish("started", "Ayre")
    check("duck: via playback event", abs(vol.levels["Spotify.exe:1:0"] - 0.3) < 1e-9)
    await s.unload()
    check("duck: unload restores and unsubscribes", abs(vol.levels["Spotify.exe:1:0"] - 0.8) < 1e-9
          and not pub.subs["started"] and not pub.subs["finished"])

    d = m.Ducker(vol, lambda: ["Spotify.exe"], lambda: 0.3, fade_s=0.01, max_duck_s=0.2)
    await d.duck()
    await asyncio.sleep(0.4)
    check("duck: watchdog restores a lost finished event", abs(vol.levels["Spotify.exe:1:0"] - 0.8) < 1e-9)

    class Broken(FakeVolume):
        def set(self, key, level):
            if level < 0.5:
                raise RuntimeError("COM error")
            super().set(key, level)
    b = Broken()
    d = m.Ducker(b, lambda: ["Spotify.exe"], lambda: 0.3, fade_s=0.01)
    await d.duck()
    check("duck: error mid-fade restores", abs(b.levels["Spotify.exe:1:0"] - 0.8) < 1e-9 and not d.saved)
    d2 = m.Ducker(m.PycawBackend(), lambda: ["Spotify.exe"], lambda: 0.3)
    await d2.duck()
    await d2.close()
    check("duck: no-op without pycaw (this Mac)", not d2.saved)


async def main():
    await test_watcher()
    await test_ducking()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


asyncio.run(main())
