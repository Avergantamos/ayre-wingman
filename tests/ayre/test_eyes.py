import asyncio, json, sys, types, importlib.util
from pathlib import Path
from PIL import Image
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE / "stubs"), str(ROOT)]
spec = importlib.util.spec_from_file_location("eyes", ROOT / "skills/ayre_eyes/main.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import tempfile
OUT = Path(tempfile.mkdtemp())
pressed = []
class W:
    async def execute_action(self, c): pressed.append(c.name.replace("v_mfd_", ""))
before = {"ship": "Cutlass Black", "visible": True,
  "weapons": [{"name": "Panther", "size": 3, "type": "laser"}, {"name": "Mass Driver", "size": 3, "type": "ballistic"}],
  "groups": [{"number": 1, "weapons": ["Panther"]}, {"number": 2, "weapons": ["Mass Driver", "Panther"]}],
  "missiles": [{"name": "Arrester", "size": 3, "seeker": "IR", "count": 4}]}
after = dict(before, groups=[{"number": 1, "weapons": ["Panther", "Mass Driver"]},
  {"number": 2, "weapons": ["Mass Driver"]}, {"number": 3, "weapons": ["Panther", "Panther"]}])
replies = iter([
  '{"visible": false}',                       # first look, before pressing anything: no weapons
  '{"visible": false}',                       # configuration view: no weapons here
  json.dumps(before),                         # self status view: weapons found
  '{"done": false, "key": "down", "why": "move to group 1"}',
  '{"done": false, "key": "next", "why": "add Mass Driver"}',
  '{"done": true, "key": null, "why": "matches"}',
  json.dumps(after),                          # re-read: the weapons screen is already up, nothing pressed
])
async def llm(messages):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=next(replies)))])
e = m.AyreEyes(None, None, W())
e.llm_call = llm; e.get_generated_files_dir = lambda: str(OUT)
e.retrieve_custom_property_value = lambda k, errs: 1
e._grab = lambda: Image.new("RGB", (64, 32), (40, 90, 120))  # not black: a real screen
_real_sleep = asyncio.sleep; m.asyncio.sleep = lambda s: _real_sleep(0)
print("read ->", asyncio.run(e.read_loadout()))
print("arrange ->", asyncio.run(e.arrange_weapon_groups()))
print("pressed:", pressed)
print("views memory:", json.loads((OUT / "views.json").read_text()))
print("wanted:", m.wanted_groups(after))
assert pressed == ["select_view_configuration_short", "select_view_self_status_short", "movement_down_short",
                   "interact_cycle_forwards_short"], pressed  # re-read found the screen already up
assert json.loads((OUT / "views.json").read_text()) == {"v_mfd_select_view_self_status_short": 1}
assert m.groups_match(e.loadout)
# a read that lists weapons but says "not visible" still counts, and nothing gets pressed
pressed.clear()
replies = iter([json.dumps(dict(before, visible=False))])
assert asyncio.run(e._find_loadout()).get("weapons") and pressed == []

# scan: scan mode, scan screen, trigger, then read; retries once while results fill in
pressed.clear()
scan = {"visible": True, "ship": "Caterpillar", "owner": "SomePilot", "owner_type": "player", "powered": True,
        "shields": "off", "cargo": ["Laranite 96 SCU"], "crime": None}
replies = iter(['{"visible": false}', json.dumps(scan)])
out = asyncio.run(e.scan_target())
print("scan ->", out)
assert pressed == ["v_set_scan_mode", "select_view_scanning_short", "v_scanning_trigger_scan"], pressed
assert "Caterpillar" in out and "SomePilot (player)" in out and "Laranite 96 SCU" in out and "shields off" in out
assert "CARGO FOUND" in out  # cargo leads without Raven asking
replies = iter([json.dumps(dict(scan, cargo=[]))])
assert "No cargo" in asyncio.run(e.scan_target())
assert "Last scan: Caterpillar" in asyncio.run(e.get_prompt())  # follow-ups answer without rescanning
replies = iter(['{"visible": false}', '{"visible": false}'])
assert "No scan results" in asyncio.run(e.scan_target())
# QD: group 4, confirm QED on the HUD, fire once, third person spine check, back to cockpit and all weapons
fired = []
async def run_steps(name, *steps): fired.append((name, steps))
e._run = run_steps
pressed.clear(); replies = iter(['{"qed_selected": true, "label": "QED"}', '{"spine_glow": true, "why": "red spine"}'])
out = asyncio.run(e.activate_qd())
assert pressed == ["v_weapon_preset_guns3", "v_weapon_preset_guns0"], pressed
assert [n for n, _ in fired] == ["Fire QED", "Third person", "Cockpit view"] and "active" in out, (fired, out)
# guns still selected: never fires, goes back to all weapons
pressed.clear(); fired.clear(); replies = iter(['{"qed_selected": false, "label": "GUNS (ALL)"}'])
out = asyncio.run(e.activate_qd())
assert fired == [] and pressed == ["v_weapon_preset_guns3", "v_weapon_preset_guns0"] and "Nothing fired" in out, out
# fired but no glow: says QD may be off or bugged
fired.clear(); replies = iter(['{"qed_selected": true, "label": "QED"}', '{"spine_glow": false, "why": "only wingtips"}'])
assert "QD may not be ON" in asyncio.run(e.activate_qd())
# power presets: direct keys for weapons/thrusters/shields, MFD steering for radar etc.
attack = {"visible": True, "free": 0, "emissions": {"ir": "1.7K", "em": "7.7K", "cs": "3.9K"},
          "bars": [{"system": "weapons", "pips": 4}, {"system": "thrusters", "pips": 4}, {"system": "shields", "pips": 2},
                   {"system": "radar", "pips": 4}, {"system": "quantum", "pips": 0}, {"system": "life_support", "pips": 0},
                   {"system": "qed", "pips": 2}, {"system": "cooler_1", "pips": 0}, {"system": "cooler_2", "pips": 1}]}
stealth_read = dict(attack, free=12, emissions={"ir": "463.2", "em": "1.9K", "cs": "3.9K"}, bars=[
    {"system": "weapons", "pips": 0}, {"system": "thrusters", "pips": 1}, {"system": "shields", "pips": 0},
    {"system": "radar", "pips": 1}, {"system": "quantum", "pips": 0}, {"system": "life_support", "pips": 0},
    {"system": "qed", "pips": 0}, {"system": "cooler_1", "pips": 0}, {"system": "cooler_2", "pips": 1}])
pressed.clear()
replies = iter([json.dumps(attack),                                   # first read: attack setup
                '{"done": false, "key": "down", "why": "radar 4 -> 1"}', '{"done": false, "key": "down", "why": ""}',
                '{"done": false, "key": "down", "why": ""}', '{"done": true, "key": null, "why": "radar 1"}',
                '{"done": false, "key": "select", "why": "qed off"}', '{"done": true, "key": null, "why": "qed 0"}',
                json.dumps(stealth_read)])                            # final read
out = asyncio.run(e.apply_power_preset("stealth"))
print("stealth ->", out)
assert pressed[0] == "select_view_resource_network_short"
assert "v_power_set_weapons_off" in pressed and "v_power_set_shields_off" in pressed
assert pressed.count("v_engineering_assignment_engine_decrease") == 3  # thrusters 4 -> 1 by key
assert pressed.count("movement_down_short") == 3 and "soft_select_mfd_primary_short" in pressed
assert out.startswith("Stealth: Power set.") and "IR 463.2" in out
replies = iter([json.dumps(stealth_read)])
assert "Saved 'stealth'" in asyncio.run(e.save_power_preset("stealth"))
assert e._presets()["stealth"]["source"] == "read in game"
assert "No power preset" in asyncio.run(e.apply_power_preset("dogfight"))
print("eyes: all checks passed")
