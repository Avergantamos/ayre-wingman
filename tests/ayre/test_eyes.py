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
  '{"visible": false}',                       # configuration view: no weapons here
  json.dumps(before),                         # self status view: weapons found
  '{"done": false, "key": "down", "why": "move to group 1"}',
  '{"done": false, "key": "next", "why": "add Mass Driver"}',
  '{"done": true, "key": null, "why": "matches"}',
  json.dumps(after),                          # re-read: tries the remembered view first
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
                   "interact_cycle_forwards_short", "select_view_self_status_short"], pressed
assert json.loads((OUT / "views.json").read_text()) == {"v_mfd_select_view_self_status_short": 2}
assert m.groups_match(e.loadout)

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
print("eyes: all checks passed")
