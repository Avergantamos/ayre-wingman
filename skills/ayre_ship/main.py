"""Ayre's ship: the Game.log watcher, music ducking and her startup self-check.

(The spoken-confirmation lock for quantum, power, shields and thrusters off was removed on
2026-10-03 at the pilot's request; those are ordinary commands now.)
Game.log watcher: a background task that checks the file size once a second and reads only the
  new bytes. Patterns live in events.yaml next to this file (editable, reloaded on change). A match
  can make her say one short line and is fed into her prompt. A daily sample of masked line
  shapes is written under generated_files so we can learn what the pilot's log really contains.
Music ducking: while she speaks, music apps (Spotify by default) are faded down through the
  Windows per-app mixer (pycaw), and faded back when she stops. No-op off Windows.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import yaml

from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill, tool

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

try:  # Windows only; installed into dependencies/ from requirements.txt
    if sys.platform != "win32":
        raise ImportError("not Windows")
    import comtypes  # type: ignore
    from pycaw.pycaw import AudioUtilities  # type: ignore
except Exception:  # pragma: no cover - depends on the machine
    comtypes = None
    AudioUtilities = None

HERE = Path(__file__).parent


def _load_voice():
    """The shared speech gate, loaded from this folder (custom_skills loads main.py outside a package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ayre_voice_ship", HERE / "ayre_voice.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VOICE = _load_voice()


def screen_is_black(pixels: list[tuple[int, int, int]]) -> bool:
    """True when a capture is (nearly) all black: Star Citizen in exclusive fullscreen."""
    if not pixels:
        return True
    lum = [(r + g + b) / 3 for r, g, b in pixels]
    mean = sum(lum) / len(lum)
    spread = max(lum) - min(lum)
    return mean < 4 and spread < 12


def health_summary(checks: dict[str, tuple[bool, str]]) -> tuple[str | None, str]:
    """(line to speak on a problem or None, full report). Critical checks first."""
    order = ["brain", "screen", "keys", "profile", "game_log", "music_ducking"]
    critical = ["brain", "screen", "keys", "profile"]
    report = "; ".join(f"{k}: {'ok' if ok else 'PROBLEM'} ({why})" for k, (ok, why) in
                       sorted(checks.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))
    for k in critical:
        if k in checks and not checks[k][0]:
            return checks[k][1], report
    return None, report


PROBLEM_LINES = {
    "brain": "Raven, I can't reach my brain on the Mac. Is it awake and on the same network?",
    "screen": "Raven, I can't see the screen. Set Star Citizen to borderless window.",
    "keys": "Raven, my key map is missing. Rerun the installer.",
    "profile": "Raven, the game isn't using my key profile. Load the AYRE control profile.",
}


def profile_loaded(actionmaps: Path, actions: dict) -> tuple[bool, str]:
    """Is Ayre's AYRE profile the one Star Citizen has loaded? Her explicit power set on/off
    actions have no keyboard key in the pilot's own profile, so they are bound in the game's
    live actionmaps.xml only while her profile is loaded."""
    canaries = sorted(a for a in actions if a.startswith("v_power_set_"))
    if not canaries:
        return True, "no power actions to check"
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(actionmaps).getroot()
    except (OSError, ET.ParseError) as e:
        return False, f"can't read the game's bindings ({type(e).__name__})"
    bound = {act.get("name") for act in root.iter("action")
             if any(rb.get("input", "").startswith("kb1_") and rb.get("input")[4:].strip()
                    for rb in act.iter("rebind"))}
    missing = [a for a in canaries if a not in bound]
    if missing:
        return False, PROBLEM_LINES["profile"] + f" ({len(missing)} of {len(canaries)} power keys not bound)"
    return True, f"AYRE profile loaded ({len(canaries)} power keys bound)"
ACTIONS = json.loads((HERE / "ayre_actions.json").read_text())

# ---------------------------------------------------------------- Game.log watcher

MAX_READ = 1_000_000  # bytes per tick; if the log jumps further we skip ahead
SHAPE_LIMIT = 300
_MASKS = [
    (re.compile(r"^<[^>]*>\s*"), ""),  # leading timestamp
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b"), "<hex>"),
    (re.compile(r"\d+(?:\.\d+)?"), "#"),
]


def line_shape(line: str) -> str:
    for pattern, repl in _MASKS:
        line = pattern.sub(repl, line)
    return line.strip()[:200]


class ShapeSampler:
    """First SHAPE_LIMIT distinct masked line shapes per day, one file per day."""

    def __init__(self, folder: Path, limit: int = SHAPE_LIMIT):
        self.folder = folder
        self.limit = limit
        self.day = ""
        self.seen: set[str] = set()

    def add(self, line: str) -> None:
        day = time.strftime("%Y-%m-%d")
        if day != self.day:
            self.day = day
            f = self.folder / f"{day}.txt"
            self.seen = set(f.read_text(encoding="utf-8").splitlines()) if f.exists() else set()
        if len(self.seen) >= self.limit:
            return
        shape = line_shape(line)
        if shape and shape not in self.seen:
            self.seen.add(shape)
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.folder / f"{day}.txt", "a", encoding="utf-8") as f:
                f.write(shape + "\n")


def load_events(path: Path) -> list[dict]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    events = []
    for e in raw if isinstance(raw, list) else []:
        try:
            events.append({
                "name": str(e["name"]),
                "regex": re.compile(e["regex"]),
                "say": [s for s in (e.get("say") or []) if s],
                "music": e.get("music"),
                "cooldown_s": float(e.get("cooldown_s") or 0),
            })
        except Exception:
            continue  # bad entry: skip it, keep the rest
    return events


class LogWatcher:
    """Polled once a second by the skill; reads only new bytes, survives rotation and truncation."""

    def __init__(self, events_file: Path, shapes_dir: Path, clock: Callable[[], float] = time.monotonic,
                 speak_gap_s: float = 10.0):
        self.events_file = events_file
        self.sampler = ShapeSampler(shapes_dir)
        self.clock = clock
        self.speak_gap_s = speak_gap_s
        self.events: list[dict] = []
        self.events_mtime = None
        self.events_checked = -1e9
        self.path = None
        self.ident = None
        self.pos = 0
        self.buf = b""
        self.first = True  # first sight of a file at startup: skip what was already there
        self.last_fired: dict[str, float] = {}
        self.last_spoken = -1e9
        self.recent: deque = deque(maxlen=5)
        self.mood: str | None = None

    def _reload_events(self, now: float) -> None:
        if now - self.events_checked < 10:
            return
        self.events_checked = now
        try:
            mtime = self.events_file.stat().st_mtime
        except OSError:
            return
        if mtime != self.events_mtime:
            self.events_mtime = mtime
            self.events = load_events(self.events_file)

    def _read_new(self, path: str) -> list[str]:
        if path != self.path:
            self.path, self.ident, self.pos, self.buf, self.first = path, None, 0, b"", True
        try:
            st = os.stat(path)
        except OSError:
            self.ident, self.pos, self.buf, self.first = None, 0, b"", False  # missing: quiet
            return []
        ident = (st.st_ino, st.st_dev)
        if self.ident is None:
            self.ident = ident
            self.pos = st.st_size if self.first else 0
            self.first = False
        elif ident != self.ident or st.st_size < self.pos:  # new session: rotated or truncated
            self.ident, self.pos, self.buf = ident, 0, b""
        if st.st_size <= self.pos:
            return []
        if st.st_size - self.pos > MAX_READ:
            self.pos, self.buf = st.st_size - MAX_READ, b""
        try:
            with open(path, "rb") as f:
                f.seek(self.pos)
                data = f.read(st.st_size - self.pos)
        except OSError:
            return []
        self.pos += len(data)
        parts = (self.buf + data).split(b"\n")
        self.buf = parts.pop()
        return [p.decode("utf-8", "replace").rstrip("\r") for p in parts if p.strip()]

    def poll(self, path: str) -> list[str]:
        """Read new lines, record matching events. Returns lines Ayre should say (at most one)."""
        now = self.clock()
        self._reload_events(now)
        to_say: list[str] = []
        for line in self._read_new(path):
            self.sampler.add(line)
            for event in self.events:
                m = event["regex"].search(line)
                if not m:
                    continue
                if now - self.last_fired.get(event["name"], -1e9) < event["cooldown_s"]:
                    break
                self.last_fired[event["name"]] = now
                detail = next((v for v in m.groupdict().values() if v), "")[:60]
                self.recent.append({"name": event["name"], "at": time.strftime("%H:%M:%S"), "detail": detail})
                if event["music"] in ("combat", "calm"):
                    self.mood = event["music"]
                if event["say"] and not to_say and now - self.last_spoken >= self.speak_gap_s:
                    line_out = random.choice(event["say"])
                    try:
                        line_out = line_out.format(**{k: v or "" for k, v in m.groupdict().items()})
                    except (KeyError, IndexError, ValueError):
                        pass
                    self.last_spoken = now
                    to_say.append(line_out)
                break  # first matching event wins
        return to_say

    def summary(self) -> str:
        items = [f"{e['at']} {e['name']}" + (f" ({e['detail']})" if e["detail"] else "") for e in self.recent]
        return "; ".join(items)


# ---------------------------------------------------------------- music ducking

class PycawBackend:
    """Windows per-app volume. All calls run on one worker thread with COM initialized."""

    def __init__(self):
        self.available = AudioUtilities is not None
        self._volumes: dict = {}

    def init_thread(self) -> None:
        if comtypes is not None:
            comtypes.CoInitialize()

    def snapshot(self, names: list[str]) -> dict[str, float]:
        wanted = {n.lower() for n in names}
        self._volumes, out = {}, {}
        for i, s in enumerate(AudioUtilities.GetAllSessions()):
            try:
                if s.Process and s.Process.name().lower() in wanted:
                    key = f"{s.Process.name()}:{s.ProcessId}:{i}"
                    self._volumes[key] = s.SimpleAudioVolume
                    out[key] = float(s.SimpleAudioVolume.GetMasterVolume())
            except Exception:
                continue
        return out

    def set(self, key: str, level: float) -> None:
        vol = self._volumes.get(key)
        if vol is not None:
            try:
                vol.SetMasterVolume(max(0.0, min(1.0, level)), None)
            except Exception:
                pass  # app closed meanwhile


class Ducker:
    """Fade music apps down while Ayre speaks; always fade them back."""

    def __init__(self, backend, names: Callable[[], list[str]], level: Callable[[], float],
                 fade_s: float = 0.25, steps: int = 6, max_duck_s: float = 120.0):
        self.backend = backend
        self.names = names
        self.level = level
        self.fade_s = fade_s
        self.steps = steps
        self.max_duck_s = max_duck_s
        self.saved: dict[str, float] = {}
        self.ducked: dict[str, float] = {}
        self._lock = asyncio.Lock()  # create inside the running loop (prepare)
        self._executor = ThreadPoolExecutor(1, initializer=getattr(backend, "init_thread", None))
        self._watchdog = None

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _fade(self, plan: dict[str, tuple[float, float]], fade: bool) -> None:
        steps = self.steps if fade else 1
        for i in range(1, steps + 1):
            for key, (a, b) in plan.items():
                self.backend.set(key, a + (b - a) * i / steps)
            if i < steps:
                time.sleep(self.fade_s / steps)  # worker thread, not the event loop

    async def duck(self, _=None) -> None:
        if not self.backend.available:
            return
        async with self._lock:
            if self.saved:
                return
            try:
                levels = await self._run(self.backend.snapshot, self.names())
                if not levels:
                    return
                target = max(0.0, min(1.0, self.level()))
                self.saved = levels
                self.ducked = {k: min(v, target) for k, v in levels.items()}
                self._arm_watchdog()
                await self._run(self._fade, {k: (v, self.ducked[k]) for k, v in levels.items()}, True)
            except Exception:
                await self._restore_locked(fade=False)

    async def restore(self, _=None, fade: bool = True) -> None:
        async with self._lock:
            await self._restore_locked(fade)

    async def _restore_locked(self, fade: bool) -> None:
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        if not self.saved:
            return
        plan = {k: (self.ducked.get(k, v), v) for k, v in self.saved.items()}
        self.saved, self.ducked = {}, {}
        try:
            await self._run(self._fade, plan, fade)
        except Exception:
            for key, (_, b) in plan.items():  # last resort, straight from this thread
                try:
                    self.backend.set(key, b)
                except Exception:
                    pass

    def _arm_watchdog(self) -> None:
        # a lost "finished" event must never leave the music down
        loop = asyncio.get_running_loop()
        self._watchdog = loop.call_later(self.max_duck_s, lambda: asyncio.ensure_future(self.restore()))

    async def close(self) -> None:
        await self.restore(fade=False)
        self._executor.shutdown(wait=False)


# ---------------------------------------------------------------- the skill

class AyreShip(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.watcher: LogWatcher | None = None
        self.ducker: Ducker | None = None
        self._watch_task: asyncio.Task | None = None
        self._speech: set = set()
        self.poll_s = 1.0

    async def validate(self) -> list[WingmanInitializationError]:
        errors = await super().validate()
        for prop in ("game_log_path", "music_processes", "duck_level"):
            self.retrieve_custom_property_value(prop, errors)
        return errors

    # config, just in time
    def _log_path(self) -> str:
        return str(self.retrieve_custom_property_value("game_log_path", []) or "")

    def _music_names(self) -> list[str]:
        raw = self.retrieve_custom_property_value("music_processes", []) or ""
        return [n.strip() for n in str(raw).split(",") if n.strip()]

    def _duck_level(self) -> float:
        value = self.retrieve_custom_property_value("duck_level", [])
        value = 30 if value is None else float(value)
        return value / 100

    async def prepare(self) -> None:
        await super().prepare()
        self.gate = VOICE.SpeechGate.get(self.wingman, asyncio.get_running_loop())
        self._health: tuple[str | None, str] = (None, "not checked yet")
        self._startup_check = asyncio.create_task(self._self_check(startup=True))
        folder = Path(self.get_generated_files_dir())
        self.watcher = LogWatcher(HERE / "events.yaml", folder / "log_shapes")
        self.ducker = Ducker(PycawBackend(), self._music_names, self._duck_level)
        events = self.wingman.audio_player.playback_events
        events.subscribe("started", self._on_started)
        events.subscribe("finished", self._on_finished)
        self._watch_task = asyncio.create_task(self._watch_loop())

    async def unload(self) -> None:
        task = getattr(self, "_startup_check", None)
        if task and not task.done():
            task.cancel()
        await super().unload()
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None
        if self.ducker:
            events = self.wingman.audio_player.playback_events
            for name, fn in (("started", self._on_started), ("finished", self._on_finished)):
                try:
                    events.unsubscribe(name, fn)
                except ValueError:
                    pass
            await self.ducker.close()

    # duck only for Ayre's own voice, not other wingmen
    async def _on_started(self, wingman_name=None) -> None:
        if self.ducker and wingman_name in (None, self.wingman.name):
            await self.ducker.duck()

    async def _on_finished(self, wingman_name=None) -> None:
        if getattr(self, "gate", None) and wingman_name in (None, self.wingman.name):
            self.gate.playback_finished()
        if self.ducker and wingman_name in (None, self.wingman.name):
            await self.ducker.restore()

    async def _watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            try:
                for text in self.watcher.poll(self._log_path()):
                    self.gate.say(text, VOICE.CHATTER)  # dropped if she's busy, never queued
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never let one bad tick kill the watcher
                self.printr.print(f"AyreShip log watcher: {e}", server_only=True)

    async def _check_brain(self) -> tuple[bool, str]:
        try:
            reply = await asyncio.wait_for(self.llm_call([{"role": "user", "content": "Reply with: ok"}]), 45)
            ok = bool(reply and reply.choices and reply.choices[0].message.content)
            return ok, "model answered" if ok else PROBLEM_LINES["brain"]
        except Exception as e:
            return False, PROBLEM_LINES["brain"] + f" ({type(e).__name__})"

    def _check_screen(self) -> tuple[bool, str]:
        try:
            from mss import mss
            with mss() as sct:
                mon = sct.monitors[1]
                w, h = mon["width"], mon["height"]
                shot = sct.grab({"left": mon["left"] + w // 4, "top": mon["top"] + h // 4,
                                 "width": w // 2, "height": h // 2})
            px = [shot.pixel(x, y) for x in range(0, shot.width, 37) for y in range(0, shot.height, 37)]
            if screen_is_black(px):
                return False, PROBLEM_LINES["screen"]
            return True, f"capture {w}x{h} has picture"
        except Exception as e:
            return False, f"screen capture failed ({type(e).__name__})"

    async def _self_check(self, startup: bool = False) -> str:
        if startup:
            await asyncio.sleep(8)  # let Wingman finish starting and the model load
        checks = {
            "brain": await self._check_brain(),
            "screen": self._check_screen(),
            "keys": (len(ACTIONS) > 50 and "v_power_set_off" in ACTIONS,
                     f"{len(ACTIONS)} actions" if len(ACTIONS) > 50 else PROBLEM_LINES["keys"]),
            "profile": profile_loaded(Path(self._log_path()).parent / "user/client/0/Profiles/default/actionmaps.xml",
                                      ACTIONS),
            "game_log": (Path(self._log_path()).exists(), self._log_path()),
            "music_ducking": (bool(self.ducker and self.ducker.backend.available), "pycaw"),
        }
        self._health = health_summary(checks)
        problem, report = self._health
        self.printr.print(f"AyreShip self-check: {report}", server_only=True)
        if startup:
            if problem:
                self.gate.say(problem, VOICE.SAFETY)
            else:
                self.gate.say(random.choice(["Online, Raven.", "I'm here, Raven.", "Systems linked. Ready."]), VOICE.CHATTER)
        return report

    @tool(description="""Check Ayre's own systems: her brain on the Mac, whether she can see the
        screen, her keys, the game log and music ducking. Use when Raven asks for a status check
        or says something isn't working.""", wait_response=True)
    async def status_check(self) -> str:
        return await self._self_check()

    async def get_prompt(self) -> str | None:
        base = await super().get_prompt() or ""
        if not self.watcher or not self.watcher.recent:
            return base or None
        extra = "\n\nShip log events, latest last: " + self.watcher.summary() + "."
        if self.watcher.mood:
            extra += f" Music mood from the log: {self.watcher.mood}."
        return (base + extra).strip()
