"""Ayre's flight skill: a fast HUD reader, landing and approach callouts, and two short assists.

calibrate_hud: one screen grab and one vision call find the readouts (radar altitude, vertical
  speed, speed, target distance on the Target Status MFD). Each box is verified by reading it
  locally before it is kept, per screen resolution.
flight_assist: callout modes (landing, approach) and pilot-started single-axis assists (descend,
  close to a marked range), plus mark_range and stop.

Performance: nothing runs unless a mode is active. When active, one thread grabs only the
calibrated regions with mss (a few hundred pixels each), reads them on the CPU at 5 Hz, and
never calls a model. The joystick guard reads pygame's cached stick state.

Safety: assists press only strafe forward/back/down and gear; every held key is released in a
finally block; any stick movement or button, a lost or jumping reading, the time limit or
"stop" ends the assist at once.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from mss import mss
from PIL import Image

from api.interface import CommandConfig, SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill, tool

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

HERE = Path(__file__).parent
DEPS = HERE / "dependencies"
if DEPS.is_dir() and str(DEPS) not in sys.path:
    sys.path.append(str(DEPS))  # winrt OCR packages, imported lazily by flight_readers


def _sibling(name: str):
    """Load a module next to this file. custom_skills loads main.py outside any package."""
    key = f"ayre_flight_{name}"
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


core = _sibling("flight_core")
voice = _sibling("ayre_voice")
readers = _sibling("flight_readers")

ACTIONS = json.loads((HERE / "ayre_actions.json").read_text())
# the only keys this skill may ever press: never weapons, missiles or targeting
ALLOWED = frozenset({"v_strafe_forward", "v_strafe_back", "v_strafe_down",
                     "v_deploy_landing_system", "v_mfd_select_view_target_status_short"})
READOUTS = ("altitude", "vertical_speed", "speed", "target_distance")
PADS = (0.25, 0.15, 0.4)
PERIOD = 0.2  # 5 Hz

CALIBRATE = """Find these Star Citizen HUD readouts on the screen. Return JSON only:
{"altitude": {"box": [x0, y0, x1, y1], "text": "<exactly as shown, with unit>"} or null,
 "vertical_speed": ..., "speed": ..., "target_distance": ...}
box: a tight box around the number and its unit only (no labels, icons or bars), as fractions
0..1 of the image width and height. altitude: the radar altitude shown in landing mode.
vertical_speed: the vertical speed shown in landing mode. speed: the ship's current speed.
target_distance: the selected target's distance as shown on the Target Status MFD screen, not
the floating target box. Use null for any readout that is not visible."""

FINISH = {"contact": "Contact. We're down, Raven.", "in_range": "In range. Holding."}


def close_enough(a: Optional[float], b: float) -> bool:
    return a is not None and abs(a - b) <= max(0.05, 0.01 * abs(b))


def pad_box(box: list, pad: float, width: int, height: int) -> tuple:
    """Normalized [x0, y0, x1, y1] -> padded pixel (left, top, w, h), clamped to the screen."""
    x0, y0, x1, y1 = [float(v) for v in box]
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    w, h = (x1 - x0) * width, (y1 - y0) * height
    px, py = max(pad * w, 4), max(pad * h, 3)
    left, top = max(0, int(x0 * width - px)), max(0, int(y0 * height - py))
    right, bottom = min(width, int(x1 * width + px + 1)), min(height, int(y1 * height + py + 1))
    return left, top, max(1, right - left), max(1, bottom - top)


def crop(image: Image.Image, region: tuple) -> Image.Image:
    left, top, w, h = region
    return image.crop((left, top, left + w, top + h))


class AyreFlight(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mode: Optional[str] = None
        self._latest: dict = {}
        self._ocr = None
        self._ocr_checked = False
        self._sticks: list = []
        self._own_pygame = False

    async def validate(self) -> list[WingmanInitializationError]:
        errors = await super().validate()
        for prop in ("display", "max_assist_seconds", "stick_deadzone"):
            self.retrieve_custom_property_value(prop, errors)
        return errors

    async def prepare(self) -> None:
        await super().prepare()
        self._gate = voice.SpeechGate.get(self.wingman, asyncio.get_running_loop())
        self.wingman.audio_player.playback_events.subscribe("finished", self._spoken)

    async def _spoken(self, _wingman_name=None) -> None:
        self._gate.playback_finished()

    async def unload(self) -> None:
        self._halt()
        try:
            self.wingman.audio_player.playback_events.unsubscribe("finished", self._spoken)
        except (ValueError, AttributeError):
            pass
        await super().unload()

    # ------------------------------------------------------------ config and storage

    def _prop(self, name: str, default):
        value = self.retrieve_custom_property_value(name, [])
        return default if value in (None, "") else value

    def _dir(self, *parts) -> Path:
        d = Path(self.get_generated_files_dir(), *parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load(self, name: str) -> dict:
        f = self._dir() / name
        try:
            return json.loads(f.read_text()) if f.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, name: str, data: dict) -> None:
        (self._dir() / name).write_text(json.dumps(data, indent=1))

    def _monitor(self) -> dict:
        display = int(self._prop("display", 1))
        with mss() as sct:
            mons = sct.monitors
            return dict(mons[display] if display < len(mons) else mons[1])

    @staticmethod
    def _res(mon: dict) -> str:
        return f"{mon['width']}x{mon['height']}"

    def _ocr_reader(self):
        if not self._ocr_checked:
            self._ocr_checked = True
            self._ocr = readers.best_backend()
        return self._ocr

    def _regions(self, res: str) -> dict:
        return self._load("regions.json").get(res, {})

    def _region_reader(self, res: str, name: str, info: dict):
        if info.get("backend") == "winocr" and self._ocr_reader():
            return self._ocr_reader()
        all_t = self._load("templates.json").get(res, {})
        pool: dict = {}
        for other, tpl in all_t.items():
            if other != name:
                for c, items in readers.templates_from_json(tpl).items():
                    pool.setdefault(c, []).extend(items)
        return readers.TemplateReader(readers.templates_from_json(all_t.get(name, {})), pool)

    # ------------------------------------------------------------ screen

    def _grab_full(self, mon: dict) -> Image.Image:
        with mss() as sct:
            shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def _make_reader(self, primary: str, extra: Optional[str] = None):
        """read() -> (t, value, extra_value) from small region grabs. Call from the worker thread."""
        mon = self._monitor()
        res = self._res(mon)
        regions = self._regions(res)
        parts = []
        for name in (primary, extra):
            if name and name in regions:
                box = regions[name]["box"]
                grab = {"left": mon["left"] + box[0], "top": mon["top"] + box[1], "width": box[2], "height": box[3]}
                parts.append((name, grab, self._region_reader(res, name, regions[name])))
        sct = mss()

        def read():
            t = time.monotonic()
            values = []
            for name, grab, reader in parts:
                try:
                    shot = sct.grab(grab)
                    value = reader.read_number(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
                except Exception:
                    value = None
                if value is not None:
                    self._latest[name] = (t, value)
                values.append(value)
            values += [None] * (2 - len(values))
            return t, values[0], values[1]

        read.close = sct.close
        return read

    def _read_once(self, name: str) -> Optional[float]:
        read = self._make_reader(name)  # mss must be used on the thread that created it
        try:
            return read()[1]
        finally:
            read.close()

    # ------------------------------------------------------------ keys and voice

    async def _key(self, action: str, down: Optional[bool] = None) -> bool:
        """Tap (down=None), hold (True) or release (False) one allowed action."""
        if action not in ALLOWED or action not in ACTIONS:
            return False
        steps = []
        for step in ACTIONS[action]:
            step = json.loads(json.dumps(step))
            if down is not None and step.get("keyboard"):
                step["keyboard"]["press"], step["keyboard"]["release"] = down, not down
            steps.append(step)
        await self.wingman.execute_action(CommandConfig.model_validate({"name": action, "actions": steps}))
        return True

    def _say(self, line: str, priority: int = voice.CALLOUT) -> None:
        """Everything goes through the shared gate: no backlog, no talking over herself."""
        gate = getattr(self, "_gate", None)
        if gate:
            gate.say(line, priority)
        else:
            self.threaded_execution(self.wingman.play_to_user, line, True)

    # ------------------------------------------------------------ sticks

    def _arm_sticks(self) -> int:
        """Open the already-initialized sticks (or init pygame if the core has not). Returns the
        number of physical sticks."""
        try:
            os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
            import pygame
            if not pygame.get_init():
                pygame.init()
                self._own_pygame = True
            if not pygame.joystick.get_init():
                pygame.joystick.init()
                self._own_pygame = True
            self._sticks = []
            for i in range(pygame.joystick.get_count()):
                j = pygame.joystick.Joystick(i)
                if not j.get_init():
                    j.init()
                self._sticks.append(j)
            return core.JoystickGuard(self._stick_snapshot).arm()
        except Exception:
            self._sticks = []
            return 0

    def _stick_snapshot(self) -> list:
        import pygame
        if self._own_pygame:
            pygame.event.pump()  # the core pumps events itself when it owns pygame
        return [{"name": j.get_name(),
                 "axes": [j.get_axis(a) for a in range(j.get_numaxes())],
                 "buttons": [j.get_button(b) for b in range(j.get_numbuttons())],
                 "hats": [j.get_hat(h) for h in range(j.get_numhats())]} for j in self._sticks]

    # ------------------------------------------------------------ modes

    def _halt(self, wait: float = 1.5) -> bool:
        running = bool(self._thread and self._thread.is_alive())
        self._stop.set()
        if running:
            self._thread.join(wait)
        return running

    def _start(self, mode: str, target, *args) -> None:
        self._halt()
        self._stop.clear()
        self._mode = mode

        def run():
            try:
                target(*args)
            except Exception as e:  # never die silently with a mode marked active
                self.printr.print(f"[AyreFlight] {mode} failed: {e}", server_only=True)
                self._say("Flight assist error. Off.", voice.SAFETY)
            finally:
                self._mode = None

        self._thread = threading.Thread(target=run, name=f"ayre_flight_{mode}", daemon=True)
        self._thread.start()

    def _run_callouts(self, kind: str, mark: Optional[float]) -> None:
        landing = kind == "landing_callouts"
        primary = "altitude" if landing else "target_distance"
        read = self._make_reader(primary, "vertical_speed" if landing else None)
        speaker = core.Speaker(self._say, time.monotonic)
        callouts = core.LandingCallouts() if landing else core.ApproachCallouts(mark)
        rate = core.RateEstimator()
        start = last_ok = time.monotonic()
        contact_at = None
        try:
            while not self._stop.is_set():
                tick = time.monotonic()
                t, value, vs = read()
                if value is not None:
                    last_ok = t
                    if landing:
                        rate.add(t, value)
                        r = rate.rate()
                        down = abs(vs) if vs is not None else (None if r is None else -r)
                        lines = callouts.feed(t, value, down)
                        if callouts.contacted and contact_at is None:
                            contact_at = t
                    else:
                        lines = callouts.feed(value)
                    for line, prio in lines:
                        speaker.say(line, prio)
                speaker.tick()
                now = time.monotonic()
                if now - last_ok > 20:
                    self._say("Lost the readout. Callouts off.", voice.SAFETY)
                    break
                if contact_at and now - contact_at > 3:
                    break
                if now - start > 900:
                    break
                self._stop.wait(max(0.0, tick + PERIOD - time.monotonic()))
        finally:
            read.close()

    def _run_assist(self, kind: str, mark: Optional[float]) -> None:
        aloop = asyncio.new_event_loop()
        run = aloop.run_until_complete
        descend = kind == "descend"
        read = self._make_reader("altitude" if descend else "target_distance",
                                 "vertical_speed" if descend else None)
        joystick = core.JoystickGuard(self._stick_snapshot, float(self._prop("stick_deadzone", 0.08)))
        try:
            joystick.arm()
            if descend:
                run(self._key("v_deploy_landing_system"))
                if self._stop.wait(1.5):
                    self._say("Assist off: stopped.", voice.SAFETY)
                    return
            speaker = core.Speaker(self._say, time.monotonic)
            assist = core.DescendAssist() if descend else core.RangeAssist(mark)
            runner = core.AssistRunner(
                assist, read,
                press=lambda a: run(self._key(a, True)),
                release=lambda a: run(self._key(a, False)),
                now=time.monotonic, sleep=time.sleep, stopped=self._stop.is_set, joystick=joystick,
                say=speaker.say, period=PERIOD, max_s=float(self._prop("max_assist_seconds", 90)))
            outcome, why = runner.run()
            self._say(FINISH.get(why, "Assist done.") if outcome == "done" else f"Assist off: {why}.", voice.SAFETY)
        finally:
            read.close()
            aloop.close()

    # ------------------------------------------------------------ calibration

    async def _ask_boxes(self, image: Image.Image) -> tuple:
        w = min(2048, image.width)
        small = image.resize((w, int(image.height * w / image.width)))
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode()
        completion = await self.llm_call([
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": [
                {"type": "text", "text": CALIBRATE},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
            ]},
        ])
        answer = completion.choices[0].message.content if completion and completion.choices else ""
        match = re.search(r"\{.*\}", answer or "", re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            data = {}
        return data, answer

    def _verify(self, image: Image.Image, data: dict) -> tuple:
        """Keep only boxes the local reader reads back as the model's value. Runs off the event loop."""
        found, templates = {}, {}
        ocr = self._ocr_reader()
        for name in READOUTS:
            item = data.get(name) if isinstance(data.get(name), dict) else None
            if not item or not item.get("box") or len(item["box"]) != 4:
                continue
            text = str(item.get("text") or "")
            value = core.parse_number(text)
            if value is None:
                continue
            try:
                crops = [(pad, pad_box(item["box"], pad, image.width, image.height)) for pad in PADS]
            except (TypeError, ValueError):
                continue
            for pad, region in crops:
                if ocr and close_enough(ocr.read_number(crop(image, region)), value):
                    found[name] = {"box": list(region), "backend": "winocr", "text": text}
                    break
                tr = readers.TemplateReader()
                if tr.learn(crop(image, region), text):
                    checks = [close_enough(tr.read_number(crop(image, r)), value) for p, r in crops if p != pad]
                    if all(checks):
                        found[name] = {"box": list(region), "backend": "template", "text": text}
                        templates[name] = tr.templates
                        break
        return found, templates

    # ------------------------------------------------------------ tools

    @tool(
        description="""Calibrate Ayre's HUD reader: finds radar altitude, vertical speed, speed and
        target distance on screen and checks she can read them. Run once per resolution, hovering in
        landing mode with a target selected.""",
        wait_response=True,
    )
    async def calibrate_hud(self) -> str:
        if self._mode:
            return f"Busy with {self._mode}. Stop it first."
        await self._key("v_mfd_select_view_target_status_short")
        await asyncio.sleep(0.8)
        mon = self._monitor()
        image = self._grab_full(mon)
        data, answer = await self._ask_boxes(image)
        found, templates = await asyncio.to_thread(self._verify, image, data)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = self._dir("frames", "calibration")
        image.save(folder / f"{stamp}.png")
        (folder / f"{stamp}.json").write_text(json.dumps(
            {"question": CALIBRATE, "answer": answer, "verified": found}, indent=1))

        res = self._res(mon)
        regions = self._load("regions.json")
        regions.setdefault(res, {}).update(found)
        self._save("regions.json", regions)
        if templates:
            store = self._load("templates.json")
            for name, tpl in templates.items():
                store.setdefault(res, {})[name] = readers.templates_to_json(tpl)
            self._save("templates.json", store)

        have = sorted(regions[res])
        missing = [n for n in READOUTS if n not in regions[res]]
        return (f"Calibrated at {res}: {', '.join(found) or 'none this time'}. "
                f"Ready: {', '.join(have) or 'none'}."
                + (f" Not readable yet: {', '.join(missing)}." if missing else ""))

    @tool(
        description="""Ayre's flight callouts and short piloting assists. Modes: landing_callouts,
        approach_callouts (distance to target), descend (assisted landing, she only strafes down),
        close_to_range (she moves fore/aft only to a marked range), mark_range (store current target
        distance under range_name), stop. Assists only when Raven asks; touching the stick ends them.""",
        wait_response=False,
    )
    async def flight_assist(self, mode: str, range_name: str = "") -> str:
        """
        Args:
            mode: landing_callouts, approach_callouts, descend, close_to_range, mark_range or stop.
            range_name: Range name for mark_range, close_to_range and approach_callouts, e.g. claw.
        """
        mode = (mode or "").strip().lower()
        name = re.sub(r"[^a-z0-9_]+", "_", (range_name or "").strip().lower()).strip("_")
        ranges = self._load("ranges.json")

        if mode == "stop":
            return "Stopped." if self._halt() else "Nothing running."

        regions = self._regions(self._res(self._monitor()))
        need = "altitude" if mode in ("landing_callouts", "descend") else "target_distance"
        if mode not in ("landing_callouts", "approach_callouts", "descend", "close_to_range", "mark_range"):
            return "Unknown mode."
        if need not in regions:
            return f"Can't read {need.replace('_', ' ')} yet. Run calibrate_hud first."

        if mode == "mark_range":
            name = name or "claw"
            t, value = self._latest.get(need, (0.0, None))
            if value is None or time.monotonic() - t > 0.5:
                value = await asyncio.to_thread(self._read_once, need)
            if value is None:
                return "Could not read the target distance. Is a target selected?"
            ranges[name] = round(value, 2)
            self._save("ranges.json", ranges)
            return f"Marked {name.replace('_', ' ')} range at {value:.1f} meters."

        mark = ranges.get(name) if name else None
        if name and mark is None:
            return f"No range named {name.replace('_', ' ')}. Mark it first."

        if mode in ("landing_callouts", "approach_callouts"):
            if mode == "approach_callouts":
                await self._key("v_mfd_select_view_target_status_short")
            self._start(mode, self._run_callouts, mode, mark)
            return f"{'Landing' if mode == 'landing_callouts' else 'Approach'} callouts on."

        # assists: pilot-started, single axis, guarded by the sticks
        if mode == "close_to_range" and mark is None:
            return "Which range? Mark one first, like claw."
        self._halt()
        if await asyncio.to_thread(self._arm_sticks) == 0:
            return "No physical stick detected, so I can't guard the assist. Not starting."
        if mode == "close_to_range":
            await self._key("v_mfd_select_view_target_status_short")
            await asyncio.sleep(0.5)
        self._start(mode, self._run_assist, mode, mark)
        return ("Descending. Touch the stick to take over." if mode == "descend"
                else f"Closing to {name.replace('_', ' ')} range, fore and aft only. Touch the stick to take over.")
