"""The pilot's controls survive Ayre: every stick, pedal, mouse and keyboard bind of his is still
in the generated profile, Ayre only adds keys where he had none, and none of hers collide with his.
Needs bindings/ (the installer fills it on the PC); skips without it."""
import json, sys, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
B = ROOT / "bindings"
ayre_files = list(B.glob("layout_*_AYRE_exported.xml"))
if not ayre_files:
    print("SKIP: no bindings/ yet (run sc_bindings/build_layer.py or the installer first)"); sys.exit(0)
sys.path.insert(0, str(ROOT / "sc_bindings"))
import yaml

ayre_path = ayre_files[0]
orig_path = B / ayre_path.name.replace("_AYRE_exported", "_exported")
pilot = yaml.safe_load((ROOT / "sc_bindings/pilot_keys.yaml").read_text()) or {}
keys = json.loads((ROOT / "sc_bindings/ayre_keys.json").read_text())

def binds(path):
    out = {}
    for amap in ET.parse(path).getroot().iter("actionmap"):
        for act in amap.iter("action"):
            for rb in act.iter("rebind"):
                inp = rb.get("input", "")
                out.setdefault((amap.get("name"), act.get("name")), set()).add(
                    (inp, rb.get("activationMode"), rb.get("multiTap")))
    return out

orig, new = binds(orig_path), binds(ayre_path)
failures = 0
def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f": {detail}" if detail and not cond else "")); failures += not cond

lost_sticks = [(k, b) for k, bs in orig.items() for b in bs
               if b[0].startswith(("js", "mo")) and b[0].strip()[-1] != "_" and b not in new.get(k, set())]
check("every stick, pedal and mouse-axis bind kept", not lost_sticks, lost_sticks[:5])

lost_kb = [(k, b) for k, bs in orig.items() for b in bs
           if b[0].startswith("kb1_") and b[0][4:].strip() and k[1] not in pilot and b not in new.get(k, set())]
check("every keyboard and mouse-button bind kept (except his own changes)", not lost_kb, lost_kb[:5])

wrong_pilot = [a for a, k in pilot.items() if not any(b[0] == f"kb1_{k}" for (m, n), bs in new.items() if n == a for b in bs)]
check("his key changes applied (strafe down Left Ctrl, decoupled C)", not wrong_pilot, wrong_pilot)

his_keys = {b[0][4:] for (m, n), bs in orig.items() for b in bs if b[0].startswith("kb1_") and b[0][4:].strip()} | set(pilot.values())
clash = {a: k for a, k in keys.items() if k in his_keys}
check("none of Ayre's keys are keys he uses", not clash, clash)
check("Ayre's keys are all different from each other", len(set(keys.values())) == len(keys))

stole = [a for a in keys if any(b[0].startswith("kb1_") and b[0][4:].strip() and not b[0][4:].startswith("mouse")
                                for (m, n), bs in orig.items() if n == a for b in bs)]
check("Ayre only took the keyboard slot where he had no keyboard key", not stole, stole)

missing = [a for a, k in keys.items() if not any(b[0] == f"kb1_{k}" for (m, n), bs in new.items() if n == a for b in bs)]
check("every Ayre key is in the profile", not missing, missing[:5])

o, n = ET.parse(orig_path).getroot(), ET.parse(ayre_path).getroot()
same_devices = [ET.tostring(e) for e in o.iter("options")] == [ET.tostring(e) for e in n.iter("options")]
check("device setup (sticks, pedals, inversions, curves) unchanged", same_devices)
print(f"\n{orig_path.name} -> {ayre_path.name}: {'all passed' if not failures else str(failures) + ' FAILED'}")
