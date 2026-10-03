"""Pure logic for Ayre's flight skill: number parsing, guards, callouts, controllers, assist loop.

No screen, keyboard or Wingman imports here, so everything can be unit tested with a fake clock.
The I/O (screen grabs, OCR, key presses, speech) is injected by main.py.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Callable, Optional

# ---------------------------------------------------------------- numbers

_MINUS = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2014": "-"})  # minus sign and dashes
_ZERO, _ONE = "OoDQ", "lI|i"


def _fix_confusions(text: str) -> str:
    """OCR letter/digit mixups, only when the letter touches a digit (keeps units like km, m/s)."""
    chars = list(text)
    for i, c in enumerate(chars):
        if c in _ZERO or c in _ONE:
            left = chars[i - 1] if i > 0 else ""
            right = chars[i + 1] if i + 1 < len(chars) else ""
            if left.isdigit() or right.isdigit():
                chars[i] = "0" if c in _ZERO else "1"
    return "".join(chars)


def parse_number(text: Optional[str]) -> Optional[float]:
    """First number in a HUD string, in meters or m/s. Handles '-12.5 m/s', '1,234 m', '1.2km', '1,5'."""
    if not text:
        return None
    s = _fix_confusions(text.translate(_MINUS))
    s = re.sub(r"(?<=\d)\s+(?=\d|[.,]\d)", "", s)     # '2 3.5' -> '23.5' (OCR spaces inside numbers)
    s = re.sub(r"(?<=[.,])\s+(?=\d)", "", s)
    s = re.sub(r"-\s+(?=\d)", "-", s)
    m = re.search(r"-?\d+(?:[.,]\d+)*", s)
    if not m:
        return None
    num = m.group(0)
    neg = num.startswith("-")
    num = num.lstrip("-")
    if "," in num and "." in num:
        num = num.replace(",", "")
    elif "," in num:
        groups = num.split(",")
        if all(len(g) == 3 for g in groups[1:]):
            num = "".join(groups)                      # thousands separators
        elif len(groups) == 2:
            num = ".".join(groups)                     # decimal comma
        else:
            return None
    if num.count(".") > 1:
        return None
    try:
        value = float(num)
    except ValueError:
        return None
    unit = s[m.end():].lstrip().lower()
    if unit.startswith("km"):
        value *= 1000.0
    return -value if neg else value


# ---------------------------------------------------------------- estimators and guards

def _slope(samples) -> Optional[float]:
    n = len(samples)
    if n < 2 or samples[-1][0] - samples[0][0] < 0.15:
        return None
    mt = sum(t for t, _ in samples) / n
    mv = sum(v for _, v in samples) / n
    den = sum((t - mt) ** 2 for t, _ in samples)
    return sum((t - mt) * (v - mv) for t, v in samples) / den if den else None


class RateEstimator:
    """Least-squares slope of the last `window` seconds of readings (units per second).

    rate(): smooth but lags about window/2. fast(): slope of the last `fast_window` seconds,
    noisy but quick. Controllers use whichever says faster, so a speed build-up is seen early."""

    def __init__(self, window: float = 0.8, fast_window: float = 0.45):
        self.window, self.fast_window = window, fast_window
        self.samples: deque = deque()

    def add(self, t: float, v: float) -> None:
        self.samples.append((t, v))
        while self.samples and t - self.samples[0][0] > self.window:
            self.samples.popleft()

    def rate(self) -> Optional[float]:
        return _slope(list(self.samples))

    def fast(self) -> Optional[float]:
        if not self.samples:
            return None
        t_end = self.samples[-1][0]
        return _slope([s for s in self.samples if t_end - s[0] <= self.fast_window + 1e-9])

    def conservative(self, direction: float) -> Optional[float]:
        """The estimate furthest along `direction` (+1 or -1): never underestimates a build-up."""
        slow, fast = self.rate(), self.fast()
        if slow is None or fast is None:
            return slow
        return max(slow, fast) if direction >= 0 else min(slow, fast)


class ReadingGuard:
    """Stale and implausible-jump detection for one readout.

    A single implausible reading is dropped (OCR glitch); `max_bad` in a row aborts.
    No good reading for `stale_s` aborts (the first reading gets `grace_s`).
    """

    def __init__(self, start: float, stale_s: float = 0.6, max_rate: float = 40.0,
                 min_jump: float = 2.0, max_bad: int = 2, grace_s: float = 1.0):
        self.stale_s, self.max_rate, self.min_jump, self.max_bad = stale_s, max_rate, min_jump, max_bad
        self.last_t: Optional[float] = None
        self.last_v: Optional[float] = None
        self.deadline = start + grace_s
        self.bad = 0
        self.reason: Optional[str] = None

    def update(self, t: float, value: Optional[float]) -> bool:
        """True when the value is usable."""
        if value is None:
            return False
        if self.last_t is not None:
            dt = max(t - self.last_t, 1e-3)
            jump = abs(value - self.last_v)
            if jump > self.min_jump and jump / dt > self.max_rate:
                self.bad += 1
                if self.bad >= self.max_bad:
                    self.reason = "reading jumped"
                return False
        self.bad = 0
        self.last_t, self.last_v = t, value
        self.deadline = t + self.stale_s
        return True

    def check(self, t: float) -> Optional[str]:
        if self.reason:
            return self.reason
        if t > self.deadline:
            return "lost the reading"
        return None


class JoystickGuard:
    """Any physical stick axis off its starting position, or any new button or hat press, aborts.

    `snapshot()` returns [{"name", "axes": [...], "buttons": [...], "hats": [...]}]. Virtual
    devices (vJoy and friends) are ignored by name.
    """

    VIRTUAL = ("vjoy", "virtual", "vigem")

    def __init__(self, snapshot: Callable[[], list], deadzone: float = 0.08):
        self.snapshot, self.deadzone = snapshot, deadzone
        self.base: list = []

    def _physical(self) -> list:
        return [d for d in self.snapshot() if not any(v in d.get("name", "").lower() for v in self.VIRTUAL)]

    def arm(self) -> int:
        self.base = self._physical()
        return len(self.base)

    def check(self) -> Optional[str]:
        now = self._physical()
        if len(now) != len(self.base):
            return "stick connection changed"
        for b, d in zip(self.base, now):
            if any(abs(x - y) > self.deadzone for x, y in zip(b["axes"], d["axes"])):
                return "stick moved"
            if any(y and not x for x, y in zip(b["buttons"], d["buttons"])):
                return "stick button"
            if any(tuple(x) != tuple(y) for x, y in zip(b.get("hats", []), d.get("hats", []))):
                return "stick hat"
        return None


# ---------------------------------------------------------------- speech

class Speaker:
    """At most one line per `gap` seconds. Normal lines are dropped when too soon (a late
    callout is useless); priority lines wait in a one-line slot and go out on the next tick."""

    def __init__(self, say: Callable[[str], None], clock: Callable[[], float], gap: float = 1.5):
        self._say, self.clock, self.gap = say, clock, gap
        self.last = -1e9
        self.pending: Optional[str] = None

    def say(self, line: Optional[str], priority: bool = False) -> bool:
        if not line:
            return False
        if self.clock() - self.last >= self.gap:
            self.last = self.clock()
            self.pending = None if self.pending == line else self.pending
            self._say(line)
            return True
        if priority:
            self.pending = line
        return False

    def tick(self) -> None:
        if self.pending and self.clock() - self.last >= self.gap:
            line, self.pending = self.pending, None
            self.say(line, True)


class ThresholdCallouts:
    """Fires each threshold once as the value falls through it (only the lowest when several
    are crossed in one step). Re-arms when the value climbs back above threshold + margin."""

    def __init__(self, thresholds: list, fmt: Callable[[float], str], margin: float = 0.2):
        self.thresholds = sorted(thresholds, reverse=True)
        self.fmt, self.margin = fmt, margin
        self.fired: set = set()

    def feed(self, value: float) -> Optional[str]:
        for th in self.thresholds:
            if th in self.fired and value > th * (1 + self.margin) + 1:
                self.fired.discard(th)
        crossed = [th for th in self.thresholds if value <= th and th not in self.fired]
        if not crossed:
            return None
        self.fired.update(crossed)
        return self.fmt(min(crossed))


def safe_descent_rate(alt: float) -> float:
    return max(1.5, 0.4 * alt + 1.0)


class ContactDetector:
    """Contact when altitude is ~0, or stays near 0 without decreasing for a second."""

    def __init__(self, zero: float = 0.1, near: float = 2.0, still_s: float = 1.0, still_m: float = 0.15):
        self.zero, self.near, self.still_s, self.still_m = zero, near, still_s, still_m
        self.hist: deque = deque()

    def feed(self, t: float, alt: float) -> Optional[str]:
        """'zero' (reads ~0), 'still' (near 0 and not moving for a second) or None."""
        self.hist.append((t, alt))
        while self.hist and t - self.hist[0][0] > self.still_s:
            self.hist.popleft()
        if alt <= self.zero:
            return "zero"
        if alt < self.near and t - self.hist[0][0] >= self.still_s * 0.9:
            vals = [v for _, v in self.hist]
            if max(vals) - min(vals) < self.still_m:
                return "still"
        return None


class LandingCallouts:
    LINES = {100: "One hundred.", 50: "Fifty.", 30: "Thirty.", 20: "Twenty.", 10: "Ten.", 5: "Five."}

    def __init__(self, too_fast_gap: float = 3.0):
        self.steps = ThresholdCallouts(list(self.LINES), lambda th: self.LINES[th])
        self.contact = ContactDetector()
        self.contacted = False
        self.too_fast_gap = too_fast_gap
        self.last_warn = -1e9

    def feed(self, t: float, alt: float, descent_rate: Optional[float]) -> list:
        """Returns [(line, priority)]."""
        out = []
        if self.contacted:
            if alt > 5:
                self.contacted = False                 # took off again, re-arm
                self.contact = ContactDetector()
            return out
        if self.contact.feed(t, alt):
            self.contacted = True
            return [("Contact.", True)]
        if descent_rate is not None and alt < 100 and descent_rate > safe_descent_rate(alt) \
                and t - self.last_warn >= self.too_fast_gap:
            self.last_warn = t
            out.append(("Too fast.", True))
        line = self.steps.feed(alt)
        if line:
            out.append((line, False))
        return out


class ApproachCallouts:
    """Target distance every few meters inside 50 m, then 'Hold.' at the marked range."""

    STEPS = [50, 40, 30, 25, 20, 15, 10, 8, 6, 5, 4, 3, 2, 1]

    def __init__(self, mark: Optional[float] = None):
        self.mark = mark
        steps = [s for s in self.STEPS if mark is None or s > mark + 1]
        self.steps = ThresholdCallouts(steps, lambda th: f"{th} meters.", margin=0.1)
        self.held = False

    def feed(self, d: float) -> list:
        if self.mark is not None:
            if abs(d - self.mark) <= 0.5 and not self.held:
                self.held = True
                return [("Hold.", True)]
            if abs(d - self.mark) > 2:
                self.held = False
        line = self.steps.feed(d)
        return [(line, False)] if line else []


# ---------------------------------------------------------------- controllers

def descent_target(alt: float) -> float:
    return min(6.0, max(0.5, 0.25 * alt))


def closure_target(d: float, d_target: float, floor: float = 0.3) -> float:
    """Closing speed (m/s, negative = backing off): 0.4 per meter of error, at most 5 forward and
    1 back, and never below `floor` so the last half meter does not stall."""
    v = max(-1.0, min(5.0, 0.4 * (d - d_target)))
    return v if abs(v) >= floor else (floor if v > 0 else -floor)


def kick_cap(target: float) -> float:
    """Limit on the proportional part of the duty: slow targets get small kicks, so a noisy rate
    estimate can't push hard near the ground or the mark. The integral (low-pass) still learns
    whatever hold duty the ship needs."""
    return 0.1 + 0.1 * abs(target)


class RatePI:
    """PI on a rate, output is a key duty cycle. Release means IFCS brakes toward zero, so the
    integral learns the duty that holds a rate; it is clamped and bled off when braking."""

    def __init__(self, kp: float = 0.35, ki: float = 0.3, i_max: float = 0.7, signed: bool = False):
        self.kp, self.ki, self.i_max, self.signed = kp, ki, i_max, signed
        self.i = 0.0

    def update(self, target: float, measured: float, dt: float) -> float:
        err = target - measured
        lo = -1.0 if self.signed else 0.0
        if not self.signed and err < -0.5:              # clearly too fast: brake, forget the hold duty
            self.i = max(0.0, self.i - self.ki * 2 * dt)
            return 0.0
        sign = -1.0 if target < 0 else 1.0
        slowing = err * sign < 0                         # going faster than wanted
        self.i = max(-self.i_max if self.signed else 0.0,
                     min(self.i_max, self.i + self.ki * err * dt * (3.0 if slowing else 1.0)))
        if self.signed and target != 0 and (self.i > 0) != (target > 0):
            self.i *= 0.5                               # direction change: drop the old hold duty fast
        p = self.kp * err
        if not slowing:
            p = sign * min(abs(p), kick_cap(target))      # pushes are small; easing off is not limited
        return max(lo, min(1.0, self.i + p))


class DescendAssist:
    """Holds a descent rate that shrinks with altitude; stops at contact. Duty in [0, 1] on strafe down."""

    def __init__(self):
        self.rate = RateEstimator(1.0)
        self.pi = RatePI(kp=0.35, ki=0.3)
        self.contact = ContactDetector()
        self.last_t: Optional[float] = None
        self.callouts = LandingCallouts()

    def key_for(self, duty: float) -> str:
        return "v_strafe_down"

    def descent_rate(self, vs: Optional[float]) -> Optional[float]:
        if vs is not None:
            return abs(vs)
        r = self.rate.conservative(-1.0)
        return None if r is None else -r

    def step(self, t: float, alt: float, vs: Optional[float] = None) -> tuple:
        """Returns (duty, event, lines)."""
        self.rate.add(t, alt)
        dt = 0.2 if self.last_t is None else max(1e-3, t - self.last_t)
        self.last_t = t
        rate = self.descent_rate(vs)
        lines = [ln for ln in self.callouts.feed(t, alt, rate) if ln[0] != "Contact."]
        touch = self.contact.feed(t, alt)
        # "still" counts only with the hold duty near its limit: pushing and not moving is the ground, a hover stall is not
        if touch == "zero" or (touch == "still" and self.pi.i >= 0.65):
            return 0.0, "contact", lines
        if rate is None:
            return 0.0, None, lines
        return self.pi.update(descent_target(alt), rate, dt), None, lines


class RangeAssist:
    """Fore/aft only to a stored range. Positive duty = strafe forward (closing), negative = back."""

    def __init__(self, d_target: float, band: float = 0.5, aim: float = 0.2, settle_s: float = 1.0):
        # drive until within `aim`, then let IFCS brake; done after settle_s inside `band`
        self.d_target, self.band, self.aim, self.settle_s = d_target, band, aim, settle_s
        self.braking = False
        self.rate = RateEstimator(1.0)
        self.pi = RatePI(kp=0.35, ki=0.3, signed=True)
        self.in_band_since: Optional[float] = None
        self.last_t: Optional[float] = None
        self.callouts = ApproachCallouts(d_target)

    def key_for(self, duty: float) -> str:
        return "v_strafe_forward" if duty >= 0 else "v_strafe_back"

    def step(self, t: float, d: float, _unused=None) -> tuple:
        self.rate.add(t, d)
        dt = 0.2 if self.last_t is None else max(1e-3, t - self.last_t)
        self.last_t = t
        err = d - self.d_target
        r = self.rate.conservative(-1.0 if err > 0 else 1.0)  # closing when ahead of the mark
        closure = None if r is None else -r
        lines = [ln for ln in self.callouts.feed(d) if ln[0] != "Hold."]
        if abs(err) <= self.aim:
            self.braking = True
        elif abs(err) > self.band:
            self.braking = False
        if self.braking:
            self.pi.i = 0.0
            slow = self.rate.rate()                      # settle on the smooth estimate
            if slow is not None and abs(slow) < 0.4:
                self.in_band_since = t if self.in_band_since is None else self.in_band_since
                if t - self.in_band_since >= self.settle_s:
                    return 0.0, "in_range", lines
            else:
                self.in_band_since = None
            return 0.0, None, lines                     # inside the band: let IFCS brake
        self.in_band_since = None
        if closure is None:
            return 0.0, None, lines
        return self.pi.update(closure_target(d, self.d_target), closure, dt), None, lines


# ---------------------------------------------------------------- assist loop

class Abort(Exception):
    pass


class AssistRunner:
    """Runs one assist: read, guard, step, pulse the key; releases every held key on exit.

    Injected: read() -> (t, value, extra) or None; press(action); release(action); now(); sleep(s);
    stopped() -> bool; joystick: JoystickGuard (armed); say(line, priority).
    """

    def __init__(self, assist, read, press, release, now, sleep, stopped, joystick: JoystickGuard,
                 say: Callable = lambda line, priority=False: None, period: float = 0.2,
                 max_s: float = 90.0, min_pulse: float = 0.03, slice_s: float = 0.02):
        self.assist, self.read, self._press, self._release = assist, read, press, release
        self.now, self._sleep, self.stopped, self.joystick, self.say = now, sleep, stopped, joystick, say
        self.period, self.max_s, self.min_pulse, self.slice_s = period, max_s, min_pulse, slice_s
        self.held: set = set()

    def _check(self, start: float) -> None:
        if self.stopped():
            raise Abort("stopped")
        if self.now() - start > self.max_s:
            raise Abort("time limit")
        reason = self.joystick.check()
        if reason:
            raise Abort(reason)

    def _wait(self, seconds: float, start: float) -> None:
        end = self.now() + seconds
        while True:
            left = end - self.now()
            if left <= 0:
                return
            self._sleep(min(self.slice_s, left))
            self._check(start)

    def _down(self, key: str) -> None:
        for other in list(self.held):
            if other != key:
                self._up(other)
        if key not in self.held:
            self._press(key)
            self.held.add(key)

    def _up(self, key: str) -> None:
        if key in self.held:
            self._release(key)
            self.held.discard(key)

    def run(self) -> tuple:
        """Returns ("done", event) or ("abort", reason)."""
        start = self.now()
        guard = ReadingGuard(start)
        try:
            while True:
                tick = self.now()
                self._check(start)
                got = self.read()
                t, value, extra = got if got else (self.now(), None, None)
                duty = 0.0
                if guard.update(t, value):
                    duty, event, lines = self.assist.step(t, value, extra)
                    for line, prio in lines:
                        self.say(line, prio)
                    if event:
                        return "done", event
                reason = guard.check(self.now())
                if reason:
                    raise Abort(reason)
                on = abs(duty) * self.period
                if on >= self.min_pulse:
                    key = self.assist.key_for(duty)
                    self._down(key)
                    if on < self.period - self.min_pulse:
                        self._wait(on, start)
                        self._up(key)
                else:
                    for key in list(self.held):
                        self._up(key)
                self._wait(tick + self.period - self.now(), start)
        except Abort as e:
            return "abort", str(e)
        finally:
            for key in list(self.held):
                try:
                    self._release(key)
                except Exception:
                    pass                                # keep releasing the rest
                self.held.discard(key)
