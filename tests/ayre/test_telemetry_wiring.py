"""Her other skills on telemetry: flight reads altitude and vertical speed from the game (HUD only
as fallback), power on/off skips or confirms from the stream, the self-check reports the link.
Run: python tests/ayre/test_telemetry_wiring.py"""
import asyncio, sys, types, importlib.util, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE / "stubs"), str(ROOT)]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


flight = load("flight_under_test", ROOT / "skills/ayre_flight/main.py")
eyes = load("eyes_under_test", ROOT / "skills/ayre_eyes/main.py")
ship_skill = load("ship_under_test", ROOT / "skills/ayre_ship/main.py")

# before the telemetry skill loads: the HUD path, and the self-check says so
assert flight.telemetry() is None and flight.live_ship() is None
assert ship_skill.AyreShip._check_telemetry() == (False, "telemetry skill not loaded")

S = load("ayre_telemetry_state", ROOT / "skills/ayre_telemetry/state.py")  # what AyreTelemetry loads
ship = S.Ship(landed=False, radar_alt=37.0, altitude=900.0, vs=-4.0, speed=30.0,
              power={"engines": True, "weapons": True, "shield_generators": False, "sensors": True})
ship.updated = time.monotonic()
sk = types.SimpleNamespace(connected=True, ship=ship, available={"v_lights_on"})
S.publish(sk)

# flight: telemetry reader, no screen touched
f = flight.AyreFlight.__new__(flight.AyreFlight)
f._latest = {}
f._make_hud_reader = lambda *a: (_ for _ in ()).throw(AssertionError("screen read while telemetry is live"))
read = f._make_reader("altitude", "vertical_speed")
t, alt, vs = read()
assert (alt, vs) == (37.0, -4.0) and f._latest["altitude"][1] == 37.0
read.close()

# the link drops mid-landing: falls back to the HUD reader, once
hud_calls = []
def hud(*a):
    r = lambda: (time.monotonic(), 12.0, None)
    r.close = lambda: hud_calls.append("closed")
    hud_calls.append("built")
    return r
f._make_hud_reader = hud
read = f._make_reader("altitude", "vertical_speed")
sk.connected = False
assert read()[1] == 12.0 and read()[1] == 12.0 and hud_calls == ["built"]
read.close(); assert hud_calls == ["built", "closed"]
sk.connected = True

# target distance is never in telemetry: always the HUD
f._make_hud_reader = lambda *a: "hud"
assert f._make_reader("target_distance") == "hud"

# eyes: power on/off from the stream
pressed = []
e = eyes.AyreEyes.__new__(eyes.AyreEyes)
async def press(action):
    pressed.append(action)
    if action == "v_power_set_shields_on":
        ship.power["shield_generators"] = True
        ship.updated = time.monotonic()
    return True
e._press = press

async def go():
    assert await e.set_power("guns", "on") == "Weapons already on."
    assert await e.set_power("shields", "on") == "Shields on, confirmed."
    assert pressed == ["v_power_set_shields_on"], pressed
    pressed.clear()
    ship.power["sensors"] = False
    assert await e.set_power("radar", "off") == "Radar already off."
    assert not pressed, "nothing pressed, no power screen opened"
asyncio.run(go())

assert ship_skill.AyreShip._check_telemetry() == (True, "live game data, 1 game actions")
S.publish(None)
print("telemetry wiring: ok")
