"""Ayre's telemetry: the state logic, then end to end against a fake Star Citizen telemetry server
speaking the same gRPC service, so the link, her prompt line, callouts and confirmed actions are
proven before the PC. Run: python tests/ayre/test_telemetry.py (needs grpcio and protobuf)."""
import asyncio, sys, importlib.util, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE / "stubs"), str(ROOT)]
spec = importlib.util.spec_from_file_location("telemetry", ROOT / "skills/ayre_telemetry/main.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
S = m.STATE
M = m.message_classes()
Frame = M["TelemetryStreamResponse"]


def frame(**kw):
    f = Frame()
    v = f.vehicle.state
    if kw.get("ship", True):
        v.name, v.manufacturer, v.health = "Taurus", "RSI", kw.get("hull", 0.9)
        v.lights_enabled = kw.get("lights", False)
        v.shields.level = kw.get("shields", 1.0)
        v.master_mode.mode = kw.get("mode", 1)
        v.doors.open_state, v.doors.lock_state = 2, 2
        fl = v.flight
        fl.state.is_landed = kw.get("landed", True)
        fl.state.gear = kw.get("gear", 3)
        fl.instruments.hydrogen_fuel = kw.get("fuel", 0.62)
        fl.instruments.quantum_fuel = kw.get("qt", 0.8)
        for k in ("powerplants", "engines", "weapons", "shield_generators", "coolers", "life_support", "sensors"):
            setattr(v.power, k, True)
        if kw.get("misl"):
            v.annunciators.misl = 5
        else:
            v.annunciators.pwr = 1
    a = f.actor
    a.vitals.oxygen.current, a.vitals.oxygen.max = kw.get("o2", 100), 100
    a.vitals.health.current, a.vitals.health.max = 100, 100
    a.state.locomotion, a.state.crew_role, a.state.uec_balance = 3, 1, 1234567
    for t in kw.get("events", []):
        f.events.events.add().type = t
    return f


# ---- state logic ----
s = S.Ship(); s.merge(frame())
line = s.summary()
for want in ("Ship RSI Taurus", "NAV", "landed", "gear down", "fuel 62%", "quantum fuel 80%", "shields 100%", "hull 90%", "lights off", "doors closed, locked", "power: all on", "1,234,567 aUEC", "seated as pilot"):
    assert want in line, (want, line)

# a partial frame (actor only) keeps the ship state
p = Frame(); p.actor.vitals.oxygen.current, p.actor.vitals.oxygen.max = 50, 100
s.merge(p)
assert s.fuel and round(s.fuel) == 62 and round(s.oxygen) == 50 and s.name == "Taurus"

# percent and fraction both read as percent
assert S.pct(0.5) == 50 and S.pct(75) == 75 and S.pct(150) == 100

# callouts fire once per episode, and again only after clearing
c = S.Callouts(); s2 = S.Ship()
s2.merge(frame(fuel=0.2)); out = [l for l, _ in c.check(s2)]
assert "Hydrogen under a quarter." in out, out
s2.merge(frame(fuel=0.19)); assert not [l for l, _ in c.check(s2) if "Hydrogen" in l]
s2.merge(frame(fuel=0.9)); c.check(s2)
s2.merge(frame(fuel=0.2)); assert "Hydrogen under a quarter." in [l for l, _ in c.check(s2)]
s2.merge(frame(misl=True)); assert ("Missile lock!", S.SAFETY) in c.check(s2)
s2.merge(frame(events=[S.ENTER_SEAT])); assert ("Aboard the Taurus.", S.CHATTER) in c.check(s2)
s2.merge(frame(events=[S.ENTER_SEAT])); assert ("Aboard the Taurus.", S.CHATTER) not in c.check(s2), "same ship twice is quiet"
s2.merge(frame(shields=0.0, landed=False)); assert ("Shields are down.", S.SAFETY) in c.check(s2)
s3 = S.Ship(); s3.merge(frame(shields=0.0, landed=True)); assert not [l for l, _ in S.Callouts().check(s3) if "Shields" in l], "shields off on the pad is not news"

# actions: blocked, unknown, toggles become explicit, already-there is skipped
avail = {"v_lights", "v_lights_on", "v_lights_off", "v_self_destruct", "v_master_mode_cycle", "v_master_mode_set_scm", "v_master_mode_set_nav"}
s4 = S.Ship(); s4.merge(frame(lights=False, mode=1))
assert S.plan_action("v_self_destruct", s4, avail)[0] is None
assert S.plan_action("v_eject", s4, None)[0] is None
assert S.plan_action("v_flightready", s4, avail)[0] is None, "not offered by the game right now"
assert S.plan_action("v_lights", s4, avail)[0] == "v_lights_on"
assert S.plan_action("v_lights_off", s4, avail) == (None, "Already lights off.")
assert S.plan_action("v_master_mode_cycle", s4, avail)[0] == "v_master_mode_set_scm", "NAV cycles to SCM"
blind = S.Ship()
assert S.plan_action("v_lights", blind, avail)[0] is None, "never toggles blind"
# flight readouts, weapons, and the live state her other skills read
fr = frame(landed=False)
i = fr.vehicle.state.flight.instruments
i.speed, i.altitude, i.radar_altitude, i.vertical_speed_indicator, i.heading = 120, 5400, 42, -6.5, 270
w = fr.weapons.weapons.add(); w.name, w.is_powered, w.ready_to_fire = "CF-337 Panther", True, True
w = fr.weapons.weapons.add(); w.name = "Mass Driver"
fr.weapons.preset_name = "All guns"
s5 = S.Ship(); s5.merge(fr)
assert S.reading(s5, "altitude") == 42 and S.reading(s5, "vertical_speed") == -6.5 and S.reading(s5, "speed") == 120
assert S.reading(s5, "target_distance") is None, "not in telemetry: stays on the screen"
i.radar_altitude = 0; s5.merge(fr); assert S.reading(s5, "altitude") == 5400, "out of radar range: sea level"
s5.merge(frame(landed=True)); assert S.reading(s5, "altitude") == 0.0
assert "120 m/s" not in s5.summary() and s5.weapons[0]["name"] == "CF-337 Panther", "weapons persist across frames"
wl = S.weapons_line(s5)
assert "Mass Driver (unpowered)" in wl and "1 of 2 ready" in wl and "preset All guns" in wl, wl
assert S.power_on(s5, "thrusters") is True and S.power_on(s5, "qed") is None
fake = type("Sk", (), {"connected": True, "ship": s5, "available": {"v_lights_on"}})()
assert S.live() is None and S.link()[0] is False
S.publish(fake); assert S.live() is s5 and S.link() == (True, "live game data, 1 game actions")
s5.updated -= 2; assert S.live() is None, "stale data is not live"
fake.connected = False; assert S.link()[0] is False
S.publish(None)
print("state logic: ok")


# ---- end to end against a fake game ----
import grpc

class FakeGame:
    def __init__(self):
        self.lights, self.misl, self.sent = False, False, []

    async def stream(self, request, context):
        while True:
            yield frame(lights=self.lights, misl=self.misl)
            await asyncio.sleep(0.05)

    async def trigger(self, request, context):
        self.sent.append(request.action_name)
        if request.action_name == "v_lights_on":
            self.lights = True
        return M["TriggerGameActionResponse"](status=1)

    async def list_actions(self, request, context):
        r = M["ListTriggerableActionsResponse"]()
        for n in ("v_lights", "v_lights_on", "v_lights_off", "v_self_destruct", "v_eject"):
            r.actions.add(action_name=n)
        return r


async def e2e():
    game = FakeGame()
    server = grpc.aio.server()
    handlers = {
        "TelemetryStream": grpc.unary_stream_rpc_method_handler(game.stream, request_deserializer=M["TelemetryStreamRequest"].FromString, response_serializer=Frame.SerializeToString),
        "TriggerGameAction": grpc.unary_unary_rpc_method_handler(game.trigger, request_deserializer=M["TriggerGameActionRequest"].FromString, response_serializer=M["TriggerGameActionResponse"].SerializeToString),
        "ListTriggerableActions": grpc.unary_unary_rpc_method_handler(game.list_actions, request_deserializer=M["ListTriggerableActionsRequest"].FromString, response_serializer=M["ListTriggerableActionsResponse"].SerializeToString),
    }
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("sc.game.telemetry.v1.GameTelemetryService", handlers),))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    said = []
    class Gate:
        def say(self, text, prio=2): said.append(text)
    m.VOICE = type("V", (), {"SpeechGate": type("G", (), {"get": staticmethod(lambda w, l: Gate())})})
    m.Skill.props = {"host": "127.0.0.1", "port": port}
    m.Skill.gen_dir = tempfile.mkdtemp()
    skill = m.AyreTelemetry(config=None, settings=None, wingman=type("W", (), {"name": "Ayre"})())
    assert await skill.validate() == []
    await skill.prepare()
    for _ in range(50):
        await asyncio.sleep(0.1)
        if skill.connected and skill.available:
            break
    assert skill.connected and "v_lights_on" in skill.available

    prompt = await skill.get_prompt()
    assert "Live game telemetry" in prompt and "lights off" in prompt and "fuel 62%" in prompt, prompt

    assert await skill.ship_action("v_self_destruct") == "That one is not mine to do."
    reply = await skill.ship_action("v_lights")
    assert reply == "Done: lights on, confirmed.", reply
    assert game.sent == ["v_lights_on"], game.sent
    assert await skill.ship_action("v_lights_on") == "Already lights on."

    await asyncio.sleep(4.2)  # past the connect quiet period
    game.misl = True
    await asyncio.sleep(0.4)
    assert "Missile lock!" in said, said

    saved = list(Path(m.Skill.gen_dir, "frames").glob("*.jsonl"))
    assert saved and len(saved[0].read_text().splitlines()) == 40, "first 40 frames kept for unit checks"

    await skill.unload()
    await server.stop(None)
    print("end to end vs fake game: ok")

asyncio.run(e2e())
