"""Tests for skills/ayre_flight. Run: <venv python> -m pytest -q test_flight.py  (or python test_flight.py)"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
SKILL = Path(__file__).resolve().parents[2] / "skills" / "ayre_flight"
sys.path.insert(0, str(HERE / "stubs"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


main = load("ayre_flight_main_under_test", SKILL / "main.py")
core = main.core
readers = main.readers


# ------------------------------------------------------------------ fakes

class SimClock:
    def __init__(self):
        self.t = 0.0


class Keys:
    def __init__(self):
        self.down = set()
        self.log = []

    def press(self, a):
        assert a in main.ALLOWED, a
        self.down.add(a)
        self.log.append(("press", a))

    def release(self, a):
        self.down.discard(a)
        self.log.append(("release", a))


def still_sticks(names=("VKB Gladiator", "VKB Throttle")):
    return lambda: [{"name": n, "axes": [0.0, 0.0, -1.0], "buttons": [0] * 8, "hats": [(0, 0)]} for n in names]


class VerticalSim:
    """IFCS-like: holding strafe-down accelerates downward to vmax; release decelerates to 0 at b."""

    def __init__(self, alt, a, b, vmax=15.0, noise=0.05, lag=0.05, seed=1):
        self.alt, self.v, self.a, self.b, self.vmax = alt, 0.0, a, b, vmax
        self.noise, self.lag = noise, lag
        self.clock, self.keys = SimClock(), Keys()
        self.hist = [(0.0, alt)]
        self.touch_speed = None
        self.max_up = 0.0
        self.rng = random.Random(seed)

    def advance(self, dt):
        steps = max(1, int(round(dt / 0.002)))
        h = dt / steps
        for _ in range(steps):
            if "v_strafe_down" in self.keys.down:
                self.v = min(self.vmax, self.v + self.a * h)
            else:
                self.v = max(0.0, self.v - self.b * h)
            prev = self.alt
            self.alt -= self.v * h
            self.max_up = max(self.max_up, self.alt - prev)
            if self.alt <= 0:
                if self.touch_speed is None:
                    self.touch_speed = self.v
                self.alt, self.v = 0.0, 0.0
            self.clock.t += h
        self.hist.append((self.clock.t, self.alt))

    def read(self):
        t = self.clock.t
        past = [a for (tt, a) in self.hist if tt <= t - self.lag] or [self.hist[0][1]]
        value = round(max(0.0, past[-1] + self.rng.uniform(-self.noise, self.noise)), 1)
        return t, value, None


class RangeSim:
    def __init__(self, d, a, b, vmax=20.0, noise=0.08, lag=0.05, seed=2):
        self.d, self.c, self.a, self.b, self.vmax = d, 0.0, a, b, vmax
        self.noise, self.lag = noise, lag
        self.clock, self.keys = SimClock(), Keys()
        self.hist = [(0.0, d)]
        self.min_d = d
        self.rng = random.Random(seed)

    def advance(self, dt):
        steps = max(1, int(round(dt / 0.002)))
        h = dt / steps
        for _ in range(steps):
            if "v_strafe_forward" in self.keys.down:
                self.c = min(self.vmax, self.c + self.a * h)
            elif "v_strafe_back" in self.keys.down:
                self.c = max(-self.vmax, self.c - self.a * h)
            elif self.c > 0:
                self.c = max(0.0, self.c - self.b * h)
            else:
                self.c = min(0.0, self.c + self.b * h)
            self.d -= self.c * h
            self.min_d = min(self.min_d, self.d)
            self.clock.t += h
        self.hist.append((self.clock.t, self.d))

    def read(self):
        t = self.clock.t
        past = [x for (tt, x) in self.hist if tt <= t - self.lag] or [self.hist[0][1]]
        return t, round(past[-1] + self.rng.uniform(-self.noise, self.noise), 1), None


def runner_for(sim, assist, sticks=None, stopped=lambda: False, read=None):
    joystick = core.JoystickGuard(sticks or still_sticks())
    joystick.arm()
    said = []
    r = core.AssistRunner(assist, read or sim.read, sim.keys.press, sim.keys.release,
                          now=lambda: sim.clock.t, sleep=sim.advance, stopped=stopped,
                          joystick=joystick, say=lambda line, p=False: said.append((round(sim.clock.t, 1), line)))
    return r, said


# ------------------------------------------------------------------ assists

def test_descend_lands_softly():
    for a, b in ((3.0, 4.0), (8.0, 10.0), (2.0, 2.5)):
        sim = VerticalSim(80.0, a, b)
        runner, said = runner_for(sim, core.DescendAssist())
        outcome, why = runner.run()
        assert (outcome, why) == ("done", "contact"), (a, b, outcome, why, sim.alt)
        assert not sim.keys.down, "keys left held"
        for _ in range(500):  # IFCS brakes after release; let it settle or touch
            sim.advance(0.01)
        touch = sim.touch_speed or 0.0
        print(f"  descend a={a} b={b}: {sim.clock.t:.1f}s, touchdown {touch:.2f} m/s, final alt {sim.alt:.2f} m, "
              f"callouts {[l for _, l in said]}")
        assert touch < 1.0, touch
        assert sim.max_up <= 1e-9, "went up"
        assert sim.alt < 0.6


def test_descend_contact_when_altitude_bottoms_above_zero():
    """Radar altitude reads 1.2 m on the ground (gear offset): contact comes from 'pushing, not moving'."""
    sim = VerticalSim(40.0, 3.0, 4.0)
    base = sim.read
    runner, _ = runner_for(sim, core.DescendAssist(), read=lambda: (lambda t, v, x: (t, round(v + 1.2, 1), x))(*base()))
    assert runner.run() == ("done", "contact")
    assert sim.alt == 0.0 and (sim.touch_speed or 0) < 1.0 and not sim.keys.down
    print(f"  offset ground: contact at {sim.clock.t:.1f}s, touchdown {sim.touch_speed:.2f} m/s")


def test_close_to_range_no_overshoot():
    for a, b in ((3.0, 4.0), (8.0, 10.0), (2.0, 2.5)):
        sim = RangeSim(120.0, a, b)
        runner, said = runner_for(sim, core.RangeAssist(25.0))
        outcome, why = runner.run()
        assert (outcome, why) == ("done", "in_range"), (a, b, outcome, why, sim.d)
        assert not sim.keys.down
        for _ in range(300):
            sim.advance(0.01)
        print(f"  close a={a} b={b}: {sim.clock.t:.1f}s, min {sim.min_d:.2f} m, final {sim.d:.2f} m")
        assert sim.min_d >= 23.5, sim.min_d
        assert abs(sim.d - 25.0) <= 0.5, sim.d


def test_close_to_range_backs_up_when_too_close():
    sim = RangeSim(20.0, 3.0, 4.0)
    runner, _ = runner_for(sim, core.RangeAssist(25.0))
    outcome, why = runner.run()
    assert (outcome, why) == ("done", "in_range") and abs(sim.d - 25) <= 0.5, (outcome, why, sim.d)
    assert ("press", "v_strafe_back") in sim.keys.log


def test_assists_with_noisier_readings():
    """0.15 m reading noise, 0.1 s capture lag, four ship profiles, three seeds each."""
    worst_touch, worst_min, worst_err, longest = 0.0, 99.0, 0.0, 0.0
    for a, b in ((2.0, 2.5), (3.0, 4.0), (8.0, 10.0), (12.0, 12.0)):
        for seed in range(3):
            sim = VerticalSim(80.0, a, b, noise=0.15, lag=0.1, seed=seed)
            runner, _ = runner_for(sim, core.DescendAssist())
            assert runner.run() == ("done", "contact"), (a, b, seed)
            longest = max(longest, sim.clock.t)
            for _ in range(500):
                sim.advance(0.01)
            worst_touch = max(worst_touch, sim.touch_speed or 0.0)
            assert sim.max_up <= 1e-9
            sim = RangeSim(120.0, a, b, noise=0.15, lag=0.1, seed=seed)
            runner, _ = runner_for(sim, core.RangeAssist(25.0))
            assert runner.run() == ("done", "in_range"), (a, b, seed, sim.d)
            longest = max(longest, sim.clock.t)
            for _ in range(300):
                sim.advance(0.01)
            worst_min, worst_err = min(worst_min, sim.min_d), max(worst_err, abs(sim.d - 25.0))
    print(f"  noisy: worst touchdown {worst_touch:.2f} m/s, closest {worst_min:.2f} m, "
          f"worst final error {worst_err:.2f} m, longest assist {longest:.0f}s")
    assert worst_touch < 1.0 and worst_min >= 23.5 and worst_err <= 0.5


# ------------------------------------------------------------------ guards

def test_stale_reading_aborts_and_releases():
    sim = VerticalSim(80.0, 3.0, 4.0)
    read = lambda: sim.read() if sim.clock.t < 3.0 else None
    runner, _ = runner_for(sim, core.DescendAssist(), read=read)
    outcome, why = runner.run()
    assert (outcome, why) == ("abort", "lost the reading"), (outcome, why)
    assert 3.0 < sim.clock.t < 3.9, sim.clock.t
    assert not sim.keys.down and ("press", "v_strafe_down") in sim.keys.log


def test_jump_aborts():
    sim = VerticalSim(80.0, 3.0, 4.0)
    read = lambda: sim.read() if sim.clock.t < 3.0 else (sim.clock.t, 400.0, None)
    runner, _ = runner_for(sim, core.DescendAssist(), read=read)
    assert runner.run() == ("abort", "reading jumped")
    assert not sim.keys.down


def test_stick_move_aborts_but_vjoy_does_not():
    sim = VerticalSim(80.0, 3.0, 4.0)

    def sticks():
        devs = still_sticks(("VKB Gladiator", "vJoy Device"))()
        if sim.clock.t > 1.0:
            devs[1]["axes"][0] = 0.9           # Ayre-side virtual device: ignored
        if sim.clock.t > 2.5:
            devs[0]["axes"][1] = 0.2           # pilot nudges the stick
        return devs

    runner, _ = runner_for(sim, core.DescendAssist(), sticks=sticks)
    outcome, why = runner.run()
    assert (outcome, why) == ("abort", "stick moved"), (outcome, why)
    assert 2.5 < sim.clock.t < 2.56, sim.clock.t          # caught mid-pulse, within one 20 ms slice
    assert not sim.keys.down


def test_button_stop_and_timeout():
    sim = VerticalSim(80.0, 3.0, 4.0)

    def sticks():
        d = still_sticks(("VKB Gladiator",))()
        d[0]["buttons"][3] = 1 if sim.clock.t > 1.0 else 0
        return d

    runner, _ = runner_for(sim, core.DescendAssist(), sticks=sticks)
    assert runner.run() == ("abort", "stick button") and not sim.keys.down
    sim = VerticalSim(80.0, 3.0, 4.0)
    runner, _ = runner_for(sim, core.DescendAssist(), stopped=lambda: sim.clock.t > 1.5)
    assert runner.run() == ("abort", "stopped") and not sim.keys.down
    sim = VerticalSim(800.0, 3.0, 4.0)
    runner, _ = runner_for(sim, core.DescendAssist())
    runner.max_s = 5.0
    assert runner.run() == ("abort", "time limit") and not sim.keys.down


def test_release_failure_still_releases_others():
    released = []
    r = core.AssistRunner(None, None, None, lambda a: (released.append(a), 1 / 0), lambda: 0.0, None,
                          lambda: True, core.JoystickGuard(lambda: []))
    r.held = {"v_strafe_down", "v_strafe_forward"}
    r.read = lambda: None
    assert r.run() == ("abort", "stopped")
    assert set(released) == {"v_strafe_down", "v_strafe_forward"} and not r.held


# ------------------------------------------------------------------ readers

FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def render(text, size=22, seed=0, bg=(10, 20, 30), fg=(80, 230, 240), offset=(6, 4)):
    rng = np.random.default_rng(seed)
    font = ImageFont.truetype(FONT, size)
    w = int(font.getlength(text)) + 14
    img = Image.new("RGB", (w, size + 12), bg)
    ImageDraw.Draw(img).text(offset, text, font=font, fill=fg)
    arr = np.asarray(img).astype(np.int16) + rng.integers(-12, 13, (img.height, img.width, 1))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def test_template_reader_learns_from_one_sample():
    tr = readers.TemplateReader()
    assert tr.learn(render("-12345.67890m"), "-12345.67890 m")
    cases = {"907.5m": 907.5, "-3.2m": -3.2, "48.06m": 48.06, "1250m": 1250.0, "0.4m": 0.4, "86m": 86.0}
    for i, (text, want) in enumerate(cases.items()):
        got = tr.read_number(render(text, seed=i + 1, offset=(6 + i % 3, 4 + i % 2)))
        assert got == want, (text, got, tr.read_text(render(text, seed=i + 1)))
    # unseen glyph inside the number is refused, not guessed
    assert readers.TemplateReader({}, None).read_number(render("12.5m")) is None
    # round trip through JSON storage
    tr2 = readers.TemplateReader(readers.templates_from_json(json.loads(json.dumps(readers.templates_to_json(tr.templates)))))
    assert tr2.read_number(render("63.9m", seed=9)) == 63.9


def test_parse_number():
    cases = {"23.5 m": 23.5, "-4.2 m/s": -4.2, "−4.2m/s": -4.2, "1,234 m": 1234.0, "1.2km": 1200.0,
             "1,5 m": 1.5, "2 3.5m": 23.5, "ALT 12m": 12.0, "1O5 m": 105.0, "": None, "m/s": None,
             "1,234.5 m": 1234.5, "- 7 m/s": -7.0}
    for text, want in cases.items():
        assert core.parse_number(text) == want, (text, core.parse_number(text))


# ------------------------------------------------------------------ callouts

def test_landing_callout_sequence_and_rate_limit():
    clock = SimClock()
    said = []
    speaker = core.Speaker(said.append, lambda: clock.t, gap=1.5)
    lc = core.LandingCallouts()
    alt = 120.0
    while clock.t < 60:
        rate = 3.0 if alt > 10 else 0.8
        for line, prio in lc.feed(clock.t, alt, rate):
            speaker.say(line, prio)
        speaker.tick()
        alt = max(0.0, alt - rate * 0.2)
        clock.t += 0.2
    assert said == ["One hundred.", "Fifty.", "Thirty.", "Twenty.", "Ten.", "Five.", "Contact."], said

    # thresholds crossed together: only the lowest; too soon: dropped; priority waits for the gap
    clock.t, said[:] = 100.0, []
    speaker.last = -1e9
    lc = core.LandingCallouts()
    for line, prio in lc.feed(clock.t, 45.0, 2.0):        # crosses 100 and 50 at once
        speaker.say(line, prio)
    clock.t += 0.5
    for line, prio in lc.feed(clock.t, 29.0, 2.0):
        speaker.say(line, prio)
    clock.t += 0.3
    for line, prio in lc.feed(clock.t, 28.0, 20.0):       # far too fast: priority warning
        speaker.say(line, prio)
    speaker.tick()
    assert said == ["Fifty."], said
    clock.t += 0.8
    speaker.tick()
    assert said == ["Fifty.", "Too fast."], said
    # no repeats while hovering at the same altitude
    for _ in range(20):
        clock.t += 0.2
        for line, prio in lc.feed(clock.t, 28.0, 0.0):
            speaker.say(line, prio)
    assert said == ["Fifty.", "Too fast."], said


def test_approach_callouts():
    ac = core.ApproachCallouts(25.0)
    out = []
    d = 70.0
    while d > 24.8:
        out += [l for l, _ in ac.feed(d)]
        d -= 0.3
    assert out == ["50 meters.", "40 meters.", "30 meters.", "Hold."], out
    ac = core.ApproachCallouts()
    out = []
    for d in np.arange(60, 0.5, -0.25):
        out += [l for l, _ in ac.feed(float(d))]
    assert out[0] == "50 meters." and out[-1] == "1 meters." and len(out) == len(set(out)), out


# ------------------------------------------------------------------ skill wiring

class FakeWingman:
    def __init__(self):
        self.actions = []
        self.spoken = []

    async def execute_action(self, command):
        self.actions.append(command)

    async def play_to_user(self, text, no_interrupt=False):
        self.spoken.append(text)


def make_skill(tmp):
    skill = main.AyreFlight(config=None, settings=None, wingman=FakeWingman())
    skill.props = {"display": 1, "max_assist_seconds": 90, "stick_deadzone": 0.08}
    skill.gen_dir = tmp
    return skill


def test_key_press_release_and_allowlist():
    with tempfile.TemporaryDirectory() as tmp:
        skill = make_skill(tmp)
        assert asyncio.run(skill._key("v_strafe_down", True))
        assert asyncio.run(skill._key("v_strafe_down", False))
        assert not asyncio.run(skill._key("v_weapon_preset_fire", True))   # never anything outside the allowlist
        hold, rel = skill.wingman.actions
        assert hold.actions[0]["keyboard"] == {"hotkey": "left ctrl", "press": True, "release": False}
        assert rel.actions[0]["keyboard"] == {"hotkey": "left ctrl", "press": False, "release": True}
        assert "release" not in main.ACTIONS["v_strafe_down"][0]["keyboard"], "shared map mutated"


def test_calibration_end_to_end_with_template_backend():
    """Synthetic 3440x1440 HUD frame, fake vision answer with sloppy boxes; template backend."""
    W, H = 3440, 1440
    frame = Image.new("RGB", (W, H), (20, 26, 34))
    draw = ImageDraw.Draw(frame)
    font = ImageFont.truetype(FONT, 22)
    spots = {"altitude": ((1500, 800), "-12345.67890 m"), "target_distance": ((2600, 1100), "1,234.5 m")}
    boxes = {}
    for name, ((x, y), text) in spots.items():
        draw.text((x, y), text.replace(" ", ""), font=font, fill=(80, 230, 240))
        w = font.getlength(text.replace(" ", ""))
        boxes[name] = {"box": [(x + 2) / W, (y + 3) / H, (x + w - 1) / W, (y + 24) / H], "text": text}
    draw.text((1300, 800), "ALT", font=font, fill=(80, 230, 240))  # a label nearby

    class FakeMss:
        monitors = [{"left": 0, "top": 0, "width": W, "height": H}] * 2

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def grab(self, mon):
            img = frame.crop((mon["left"], mon["top"], mon["left"] + mon["width"], mon["top"] + mon["height"]))
            bgra = np.dstack([np.asarray(img)[..., ::-1], np.full((img.height, img.width), 255, np.uint8)])
            return SimpleNamespace(size=img.size, bgra=bgra.tobytes())

        def close(self):
            pass

    main.mss = FakeMss
    readers.HAVE_WINOCR = False
    with tempfile.TemporaryDirectory() as tmp:
        skill = make_skill(tmp)

        async def llm_call(messages, tools=None):
            content = json.dumps({**boxes, "vertical_speed": None, "speed": None})
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        skill.llm_call = llm_call
        result = asyncio.run(skill.calibrate_hud())
        print("  calibrate_hud ->", result)
        regions = json.loads((Path(tmp) / "regions.json").read_text())["3440x1440"]
        assert set(regions) == {"altitude", "target_distance"}, regions
        assert list((Path(tmp) / "frames" / "calibration").glob("*.png"))
        # change the numbers on screen; the live reader reads them from small region grabs
        draw.rectangle((1490, 790, 1800, 840), fill=(20, 26, 34))
        draw.text((1500, 800), "-847.5m", font=font, fill=(80, 230, 240))
        read = skill._make_reader("altitude")
        assert read()[1] == -847.5, read()
        assert skill._read_once("target_distance") == 1234.5
        out = asyncio.run(skill.flight_assist("mark_range", "Claw"))
        assert "1234.5" in out and json.loads((Path(tmp) / "ranges.json").read_text()) == {"claw": 1234.5}, out
        assert asyncio.run(skill.flight_assist("close_to_range", "salvage")).startswith("No range named")
        assert asyncio.run(skill.flight_assist("stop")) == "Nothing running."


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception as e:
                failed += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {e!r}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
