"""Write Ayre's MFD keys straight into Star Citizen's live bindings (Profiles/default/actionmaps.xml).

The game drops vehicle_mfd keyboard binds when it imports a control profile, but keeps them in the
live file (a bind made by hand in the keybindings menu is stored there exactly like this). Run with
Star Citizen CLOSED: the game rewrites the file on exit. Keeps a timestamped backup next to it.
Don't reload the AYRE control profile afterwards; that re-imports and drops these again.
Usage: py -3.11 install/add_mfd_keys.py [path to LIVE]"""
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
args = [a for a in sys.argv[1:] if a != "--test"]  # --test: work on a copy while the game runs
live_dir = Path(args[0] if args else r"C:\Program Files\Roberts Space Industries\StarCitizen\LIVE")
live = live_dir / "user/client/0/Profiles/default/actionmaps.xml"
profile = next((ROOT / "bindings").glob("layout_*_AYRE_exported.xml"))

if "--test" not in sys.argv and "StarCitizen.exe" in subprocess.run(["tasklist", "/FI", "IMAGENAME eq StarCitizen.exe"],
                                       capture_output=True, text=True).stdout:
    sys.exit("Close Star Citizen first: it rewrites its bindings file on exit.")

# her MFD keys, from the profile the builder made
want = {}
for amap in ET.parse(profile).getroot().iter("actionmap"):
    if amap.get("name") != "vehicle_mfd":
        continue
    for act in amap.iter("action"):
        for rb in act.iter("rebind"):
            if rb.get("input", "").startswith("kb1_") and rb.get("input")[4:].strip():
                want[act.get("name")] = rb.get("input")
if not want:
    sys.exit(f"No MFD keys in {profile.name}; run the installer first.")

backup = live.with_name(f"actionmaps.backup-{time.strftime('%Y%m%d-%H%M%S')}.xml")
shutil.copy2(live, backup)
tree = ET.parse(live)
root = tree.getroot()
mfd = next((m for m in root.iter("actionmap") if m.get("name") == "vehicle_mfd"), None)
if mfd is None:  # actionmaps sit under <ActionProfiles>
    mfd = ET.SubElement(next(iter(root.iter("ActionProfiles")), root), "actionmap", name="vehicle_mfd")
added = []
for name, key in sorted(want.items()):
    act = next((a for a in mfd.iter("action") if a.get("name") == name), None)
    if act is None:
        act = ET.SubElement(mfd, "action", name=name)
    for rb in list(act.iter("rebind")):
        if rb.get("input", "").startswith("kb1_"):
            act.remove(rb)  # replace any old keyboard bind on this action
    rb = ET.Element("rebind", input=key)
    act.insert(0, rb)  # keyboard first, as the game writes it
    added.append(f"{name} = {key}")
ET.indent(tree, " ")
tree.write(live, encoding="utf-8", xml_declaration=False)  # as the game writes it: no declaration, no BOM
print(f"backup: {backup.name}")
print(f"wrote {len(added)} MFD keys into the live bindings:")
for a in added:
    print("  " + a)
