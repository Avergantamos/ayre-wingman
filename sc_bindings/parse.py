"""Read Star Citizen's live actionmaps.xml into a flat binding map for Ayre.

Usage: python sc_bindings/parse.py [path/to/actionmaps.xml] [--json out.json]
Default path is the Windows live install; on the Mac pass bindings/actionmaps.xml.

Each action gets its real inputs with the joystick number resolved to the device
name, so "js4_button18" reads as "Gladiator EVO R button 18". Empty slots
("js65536_ ", "kb1_ ") mean "explicitly unbound" and are dropped; actions not in
the file at all are on Star Citizen's defaults, which this file cannot tell us.
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GLADIATOR = {k: v for k, v in json.loads((Path(__file__).parent / "vkb_gladiator_evo.json").read_text()).items()
             if not k.startswith("_")}

LIVE = Path(r"C:\Program Files\Roberts Space Industries\StarCitizen\LIVE\user\client\0\Profiles\default\actionmaps.xml")


def device_names(root):
    """js instance number -> short device name, from the <options> blocks."""
    names = {}
    for opt in root.iter("options"):
        if opt.get("type") == "joystick" and opt.get("Product"):
            product = re.sub(r"\s*\{.*\}", "", opt.get("Product")).strip()
            names[opt.get("instance")] = product.replace("VKBsim ", "").replace("VKBSim ", "")
    return names


def describe(raw, devices):
    """'js4_button18' -> 'Gladiator EVO R button 18'; None for empty slots."""
    dev, _, key = raw.partition("_")
    key = key.strip()
    if not key:
        return None
    if dev.startswith("kb"):
        return f"mouse {key[5:]}" if key.startswith("mouse") else f"keyboard {key}"
    if dev.startswith("mo"):
        return f"mouse {key}"
    if dev.startswith("js"):
        num = dev[2:]
        if num == "65536":  # placeholder device, never a real binding
            return None
        key = key.replace("button", "button ").replace("hat1_", "hat ")
        name = devices.get(num, "joystick " + num)
        if "Gladiator" in name:
            stick = "left stick" if name.endswith(" L") else "right stick"
            physical = GLADIATOR.get(key.replace("button ", ""))
            return f"{stick}, {physical}" if physical else f"{stick} {key}"
        return f"{name} {key}"
    return raw


def parse(path):
    root = ET.parse(path).getroot()
    devices = device_names(root)
    actions = {}
    for amap in root.iter("actionmap"):
        for action in amap.iter("action"):
            inputs = []
            for rb in action.iter("rebind"):
                text = describe(rb.get("input", ""), devices)
                if not text:
                    continue
                mode = rb.get("activationMode") or ("multitap " + rb.get("multiTap") if rb.get("multiTap") else "")
                inputs.append({"raw": rb.get("input"), "where": text + (f" ({mode})" if mode else "")})
            if inputs:
                actions.setdefault(action.get("name"), {"maps": [], "inputs": inputs})
                actions[action.get("name")]["maps"].append(amap.get("name"))
    return devices, actions


if __name__ == "__main__":
    args = sys.argv[1:]
    out = None
    if "--json" in args:
        out = args[args.index("--json") + 1]
        args = args[:args.index("--json")]
    path = Path(args[0]) if args else LIVE
    devices, actions = parse(path)
    print("devices:", ", ".join(f"js{k}={v}" for k, v in sorted(devices.items())))
    for name, info in sorted(actions.items()):
        print(f"{name:52} {' | '.join(i['where'] for i in info['inputs'])}")
    if out:
        Path(out).write_text(json.dumps({"devices": devices, "actions": actions}, indent=1))
