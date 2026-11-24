"""Ayre's telemetry brain: no network, no Wingman, so it is tested on the Mac.

Star Citizen's local telemetry stream (5.0, and display-only on 4.10.3 LIVE) sends partial frames
about 17 times a second. Ship keeps the latest value of every field, so a frame that only carries
the actor still leaves the ship state in place.

Units are not documented by CIG. Fuel, shields and hull arrive as floats; pct() reads anything up
to 1.0 as a fraction and anything above as a percent. Gauges (oxygen, hunger, thirst, health,
body temperature) are current over max. The first frames of each session are saved raw so the
units can be checked on the PC (see main.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# Never sent through the game API for Ayre (CLAUDE.md safety rules): self destruct and eject are
# not hers, and emergency exit bails the pilot out of the seat.
BLOCKED = {"v_self_destruct", "v_eject", "v_emergency_exit"}

MASTER = {1: "NAV", 2: "SCM", 3: "stealth"}
DEPLOY = {1: "up", 2: "deploying", 3: "down", 4: "retracting"}
LOCOMOTION = {1: "on foot", 2: "EVA", 3: "seated"}
ROLE = {1: "pilot", 2: "gunner", 3: "crew"}
OPENED, CLOSED, UNLOCKED, LOCKED = 1, 2, 1, 2
MAJOR = {5, 6}  # annunciator MAJOR_FLASHING, MAJOR_ON
ENTER_SEAT, DOWNED, RESPAWN = 3, 6, 5


def pct(v) -> float | None:
    if v is None:
        return None
    v = float(v)
    return max(0.0, min(100.0, v * 100 if v <= 1.0001 else v))


def gauge_pct(g) -> float | None:
    if g is None or not getattr(g, "max", 0):
        return None
    return max(0.0, min(100.0, g.current / g.max * 100))


@dataclass
class Ship:
    """The latest known state, merged from partial frames. Fields stay None until first seen."""
    name: str | None = None
    manufacturer: str | None = None
    hull: float | None = None
    shields: float | None = None
    fuel: float | None = None
    quantum: float | None = None
    landed: bool | None = None
    gear: str | None = None
    lights: bool | None = None
    doors_open: bool | None = None
    doors_locked: bool | None = None
    mode: str | None = None
    power: dict = field(default_factory=dict)
    alarms: dict = field(default_factory=dict)  # annunciator name -> True when MAJOR
    decoys: int | None = None
    noise: int | None = None
    # the pilot
    health: float | None = None
    oxygen: float | None = None
    hunger: float | None = None
    thirst: float | None = None
    body_temp: float | None = None
    breathable: bool | None = None
    locomotion: str | None = None
    role: str | None = None
    seat: str | None = None
    uec: int | None = None
    in_vehicle: bool = False
    events: list = field(default_factory=list)  # event types in the last frame
    updated: float = 0.0

    def merge(self, frame) -> None:
        """Fold one TelemetryStreamResponse into the state (presence-checked, proto3 omits zeros)."""
        self.events = []
        if frame.HasField("vehicle") and frame.vehicle.HasField("state"):
            v = frame.vehicle.state
            self.in_vehicle = bool(v.name)
            if v.name:
                self.name, self.manufacturer = v.name, v.manufacturer or self.manufacturer
            self.hull = pct(v.health)
            self.lights = v.lights_enabled
            if v.HasField("shields"):
                self.shields = pct(v.shields.level)
            if v.HasField("master_mode"):
                self.mode = MASTER.get(v.master_mode.mode, self.mode)
            if v.HasField("doors"):
                self.doors_open = v.doors.open_state == OPENED if v.doors.open_state else self.doors_open
                self.doors_locked = v.doors.lock_state == LOCKED if v.doors.lock_state else self.doors_locked
            if v.HasField("flight"):
                f = v.flight
                if f.HasField("state"):
                    self.landed = f.state.is_landed
                    if f.state.HasField("gear"):
                        self.gear = DEPLOY.get(f.state.gear, self.gear)
                if f.HasField("instruments"):
                    self.fuel = pct(f.instruments.hydrogen_fuel)
                    self.quantum = pct(f.instruments.quantum_fuel)
            if v.HasField("power"):
                p = v.power
                self.power = {k: getattr(p, k) for k in ("powerplants", "engines", "weapons", "shield_generators", "coolers", "life_support")}
            if v.HasField("annunciators"):
                a = v.annunciators
                self.alarms = {k: getattr(a, k) in MAJOR for k in ("pwr", "wpn", "thr", "shld", "cool", "qdrv", "prox", "hit", "misl")}
        if frame.HasField("weapons"):
            for cm in frame.weapons.countermeasures:
                if cm.type == 1:
                    self.decoys = cm.amount
                elif cm.type == 2:
                    self.noise = cm.amount
        if frame.HasField("actor"):
            a = frame.actor
            if a.HasField("vitals"):
                vt = a.vitals
                for attr, g in (("health", "health"), ("oxygen", "oxygen"), ("hunger", "hunger"), ("thirst", "thirst"), ("body_temp", "body_temperature")):
                    if vt.HasField(g):
                        setattr(self, attr, gauge_pct(getattr(vt, g)))
            if a.HasField("state"):
                s = a.state
                self.locomotion = LOCOMOTION.get(s.locomotion, self.locomotion)
                self.role = ROLE.get(s.crew_role)
                self.seat = s.seat_name or None
                self.uec = s.uec_balance
            if a.HasField("environment"):
                self.breathable = a.environment.breathable_atmosphere
        if frame.HasField("events"):
            self.events = [e.type for e in frame.events.events]
        self.updated = time.monotonic()

    def summary(self) -> str:
        """One short line for her prompt: what she knows right now, numbers first."""
        bits = []
        if self.in_vehicle and self.name:
            ship = f"{self.manufacturer} {self.name}".strip() if self.manufacturer and self.manufacturer not in self.name else self.name
            bits.append(f"Ship {ship}")
            if self.mode:
                bits.append(self.mode)
            if self.landed is not None:
                bits.append("landed" if self.landed else "flying")
            if self.gear:
                bits.append(f"gear {self.gear}")
            for label, v in (("fuel", self.fuel), ("quantum fuel", self.quantum), ("shields", self.shields), ("hull", self.hull)):
                if v is not None:
                    bits.append(f"{label} {v:.0f}%")
            if self.lights is not None:
                bits.append("lights on" if self.lights else "lights off")
            if self.doors_open is not None:
                bits.append(("doors open" if self.doors_open else "doors closed") + (", locked" if self.doors_locked else ""))
            off = [k.replace("_", " ") for k, on in self.power.items() if on is False]
            if self.power:
                bits.append("power: " + ("all on" if not off else "off: " + ", ".join(off)))
            if self.decoys is not None or self.noise is not None:
                bits.append(f"decoys {self.decoys or 0}, noise {self.noise or 0}")
            alarms = [k for k, on in self.alarms.items() if on]
            if alarms:
                bits.append("warnings: " + ", ".join(alarms))
        pilot = []
        for label, v in (("health", self.health), ("oxygen", self.oxygen), ("hunger", self.hunger), ("thirst", self.thirst)):
            if v is not None:
                pilot.append(f"{label} {v:.0f}%")
        if self.locomotion:
            pilot.append(self.locomotion + (f" as {self.role}" if self.role else ""))
        if self.uec is not None:
            pilot.append(f"{self.uec:,} aUEC")
        out = ". ".join(filter(None, [", ".join(bits), "Raven: " + ", ".join(pilot) if pilot else ""]))
        return out


# Callouts: (key, test, line, priority). Each fires once when its condition starts, and can fire
# again only after the condition clears (hysteresis), so a hovering value never repeats.
SAFETY, CALLOUT, CHATTER = 3, 2, 1
RULES = [
    ("misl", lambda s: s.alarms.get("misl"), "Missile lock!", SAFETY),
    ("hit", lambda s: s.alarms.get("hit"), "We're taking hits.", CALLOUT),
    ("shields_down", lambda s: s.shields is not None and s.in_vehicle and s.shields < 5 and not s.landed, "Shields are down.", SAFETY),
    ("hull_50", lambda s: s.hull is not None and s.in_vehicle and s.hull < 50, "Hull under half.", CALLOUT),
    ("hull_25", lambda s: s.hull is not None and s.in_vehicle and s.hull < 25, "Hull critical, Raven.", SAFETY),
    ("fuel_25", lambda s: s.fuel is not None and s.in_vehicle and s.fuel < 25, "Hydrogen under a quarter.", CALLOUT),
    ("fuel_10", lambda s: s.fuel is not None and s.in_vehicle and s.fuel < 10, "Hydrogen almost gone.", SAFETY),
    ("qt_20", lambda s: s.quantum is not None and s.in_vehicle and s.quantum < 20, "Quantum fuel low.", CALLOUT),
    ("oxygen", lambda s: s.oxygen is not None and s.oxygen < 25, "Oxygen low, Raven.", SAFETY),
    ("health", lambda s: s.health is not None and s.health < 30, "You're hurt. Get patched up.", CALLOUT),
    ("thirst", lambda s: s.thirst is not None and s.thirst < 15, "You need water.", CHATTER),
    ("hunger", lambda s: s.hunger is not None and s.hunger < 15, "You should eat something.", CHATTER),
]


class Callouts:
    def __init__(self):
        self.active: set[str] = set()
        self.last_ship: str | None = None

    def check(self, s: Ship) -> list[tuple[str, int]]:
        out = []
        for key, test, line, prio in RULES:
            try:
                on = bool(test(s))
            except Exception:
                on = False
            if on and key not in self.active:
                self.active.add(key)
                out.append((line, prio))
            elif not on:
                self.active.discard(key)
        if DOWNED in s.events:
            out.append(("You're down, Raven. Hang on.", SAFETY))
        if ENTER_SEAT in s.events and s.in_vehicle and s.name and s.role == "pilot" and s.name != self.last_ship:
            self.last_ship = s.name
            out.append((f"Aboard the {s.name}.", CHATTER))
        return out


# Explicit on/off actions she can confirm from the stream, so she never toggles blind:
# action -> (state attribute, value it should have afterwards)
CONFIRMABLE = {
    "v_lights_on": ("lights", True), "v_lights_off": ("lights", False),
    "v_deploy_landing_system": ("gear", "down"), "v_retract_landing_system": ("gear", "up"),
    "v_master_mode_set_nav": ("mode", "NAV"), "v_master_mode_set_scm": ("mode", "SCM"),
    "v_open_all_doors": ("doors_open", True), "v_close_all_doors": ("doors_open", False),
    "v_lock_all_doors": ("doors_locked", True), "v_unlock_all_doors": ("doors_locked", False),
}
# Toggles become the explicit action when the state is known (never a blind toggle)
TOGGLES = {
    "v_lights": ("lights", "v_lights_on", "v_lights_off"),
    "v_toggle_landing_system": ("gear", "v_deploy_landing_system", "v_retract_landing_system"),
    "v_master_mode_cycle": ("mode", "v_master_mode_set_nav", "v_master_mode_set_scm"),  # in NAV -> SCM, else -> NAV
    "v_toggle_all_doors": ("doors_open", "v_open_all_doors", "v_close_all_doors"),
    "v_toggle_all_doorlocks": ("doors_locked", "v_lock_all_doors", "v_unlock_all_doors"),
}


def plan_action(action: str, s: Ship, available: set[str] | None) -> tuple[str | None, str]:
    """Turn a requested action into the one to send, or a reason not to. Returns (action, note)."""
    action = (action or "").strip()
    if action in BLOCKED:
        return None, "That one is not mine to do."
    if available is not None and action not in available:
        return None, f"The game does not offer {action} right now."
    if action in TOGGLES:
        attr, on_action, off_action = TOGGLES[action]
        now = getattr(s, attr)
        if now is None:
            return None, "I can't see that state yet, so I won't toggle it blind."
        is_on = now in (True, "down", "NAV")
        action = off_action if is_on else on_action
    if action in CONFIRMABLE:
        attr, want = CONFIRMABLE[action]
        if getattr(s, attr) == want:
            return None, f"Already {describe(attr, want)}."
    return action, ""


def describe(attr: str, value) -> str:
    return {
        ("lights", True): "lights on", ("lights", False): "lights off",
        ("gear", "down"): "gear down", ("gear", "up"): "gear up",
        ("mode", "NAV"): "in NAV", ("mode", "SCM"): "in SCM",
        ("doors_open", True): "doors open", ("doors_open", False): "doors closed",
        ("doors_locked", True): "doors locked", ("doors_locked", False): "doors unlocked",
    }.get((attr, value), f"{attr} {value}")


def confirmed(action: str, s: Ship) -> bool | None:
    """After sending: True if the stream shows the result, False if not, None if it can't tell."""
    if action not in CONFIRMABLE:
        return None
    attr, want = CONFIRMABLE[action]
    return getattr(s, attr) == want
