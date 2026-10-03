"""Ayre's ship: the confirmation lock, the Game.log watcher and music ducking.

risky_action: the only way to press Engage Quantum Drive, Main Power Off, Shields Off and
  Thrusters Off. The lock is in code: the second call presses only when Raven's latest message,
  spoken after the first call and within 20 s, is a plain yes.
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

from api.interface import CommandConfig, SettingsConfig, SkillConfig, WingmanInitializationError
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
ACTIONS = json.loads((HERE / "ayre_actions.json").read_text())

# ---------------------------------------------------------------- confirmation lock

RISKY = {
    "v_toggle_qdrive_engagement": ("Engage Quantum Drive", [
        "engage quantum drive", "engage quantum", "quantum drive", "quantum", "quantum jump",
        "quantum travel", "engage qt", "qt", "jump"]),
    "v_power_set_off": ("Main Power Off", [
        "main power off", "power off", "power down", "main power", "shut down power"]),
    "v_power_set_shields_off": ("Shields Off", ["shields off", "shields", "drop shields", "cut shields"]),
    "v_power_set_thrusters_off": ("Thrusters Off", ["thrusters off", "thrusters", "cut thrusters", "kill thrusters"]),
}
CONFIRM_WINDOW_S = 20.0
YES = re.compile(r"\b(yes|yeah|yep|confirm|confirmed|do it|affirmative|engage|cut them|kill it)\b")
GO = {"go", "go ahead", "go go"}  # "go" only on its own, so "let's go" never confirms
NO = re.compile(r"\b(no|not|cancel|wait|stop|don t|dont|abort|negative|hold)\b")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9_]+", " ", (text or "").lower()).split())


def is_affirmative(text: str) -> bool:
    t = _norm(text)
    return (bool(YES.search(t)) or t in GO) and not NO.search(t)


def resolve_risky(name: str) -> str | None:
    n = _norm(name)
    for action_id, (label, aliases) in RISKY.items():
        if n in (action_id, action_id[2:], _norm(label)) or n in aliases:
            return action_id
    return None


class ConfirmLock:
    """One pending request; a press needs a fresh affirmative user message after it."""

    def __init__(self, window_s: float = CONFIRM_WINDOW_S, clock: Callable[[], float] = time.monotonic):
        self.window_s = window_s
        self.clock = clock
        self.pending: tuple[str, int, float] | None = None  # action, user message seq, time
        self.seq = 0  # counter, not timestamps: Windows clocks can tick coarser than two events
        self.last_text = ""

    def user_said(self, text: str) -> None:
        self.seq += 1
        self.last_text = text or ""

    def request(self, action: str) -> str:
        """Returns ask, press, stale, no_answer or refused."""
        now = self.clock()
        pending = self.pending
        if pending is None or pending[0] != action:
            self.pending = (action, self.seq, now)
            return "ask"
        self.pending = None  # one shot, whatever happens next
        if now - pending[2] > self.window_s:
            return "stale"
        if self.seq <= pending[1]:
            return "no_answer"
        if not is_affirmative(self.last_text):
            return "refused"
        return "press"


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
        self.lock = ConfirmLock()
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
        folder = Path(self.get_generated_files_dir())
        self.watcher = LogWatcher(HERE / "events.yaml", folder / "log_shapes")
        self.ducker = Ducker(PycawBackend(), self._music_names, self._duck_level)
        events = self.wingman.audio_player.playback_events
        events.subscribe("started", self._on_started)
        events.subscribe("finished", self._on_finished)
        self._watch_task = asyncio.create_task(self._watch_loop())

    async def unload(self) -> None:
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
        if self.ducker and wingman_name in (None, self.wingman.name):
            await self.ducker.restore()

    async def _watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            try:
                for text in self.watcher.poll(self._log_path()):
                    task = asyncio.create_task(self.wingman.play_to_user(text, True))
                    self._speech.add(task)
                    task.add_done_callback(self._speech.discard)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never let one bad tick kill the watcher
                self.printr.print(f"AyreShip log watcher: {e}", server_only=True)

    async def on_add_user_message(self, message: str) -> None:
        self.lock.user_said(message)

    async def _press(self, action: str) -> bool:
        if action not in ACTIONS:
            return False
        await self.wingman.execute_action(CommandConfig.model_validate({"name": action, "actions": ACTIONS[action]}))
        return True

    @tool(description="""The only way to Engage Quantum Drive, Main Power Off, Shields Off or
        Thrusters Off. First call returns a confirmation question for Raven. Call again with the
        same name only after Raven answers yes.""")
    async def risky_action(self, name: str) -> str:
        """
        Args:
            name: Engage Quantum Drive, Main Power Off, Shields Off or Thrusters Off.
        """
        action = resolve_risky(name)
        if not action:
            return "Unknown risky action. Options: " + ", ".join(label for label, _ in RISKY.values()) + "."
        label = RISKY[action][0]
        result = self.lock.request(action)
        if result == "ask":
            return (f"{label} not pressed yet. Ask Raven exactly 'Confirm, Raven?' and wait. "
                    f"Call risky_action again only after he says yes.")
        if result == "press":
            if await self._press(action):
                return f"{label}: pressed."
            return f"{label} has no key bound. Tell Raven."
        reason = {"stale": "his yes did not come within 20 seconds",
                  "no_answer": "Raven has not answered yet",
                  "refused": "Raven did not say yes"}[result]
        return f"{label} not pressed: {reason}. Request cleared; tell Raven in a few words."

    async def get_prompt(self) -> str | None:
        base = await super().get_prompt() or ""
        if not self.watcher or not self.watcher.recent:
            return base or None
        extra = "\n\nShip log events, latest last: " + self.watcher.summary() + "."
        if self.watcher.mood:
            extra += f" Music mood from the log: {self.watcher.mood}."
        return (base + extra).strip()
