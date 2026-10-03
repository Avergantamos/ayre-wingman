"""Build Ayre's layer: spare keyboard combos for her actions, merged into the pilot's profile.

Usage: python sc_bindings/build_layer.py [bindings_dir]   (default: bindings/)
Reads   bindings/actionmaps.xml (live binds, used for collisions and action maps)
        bindings/layout_*_exported.xml (the pilot's profile, the base we merge into)
        sc_bindings/ayre_layer.yaml (what she controls)
        sc_bindings/ayre_keys.json (stable key assignments, created on first run)
Writes  bindings/layout_<PROFILE>_AYRE_exported.xml  (import this in game)
        Ayre's commands in templates/configs/_Star Citizen/Ayre.template.yaml
"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "sc_bindings"
TEMPLATE = ROOT / "templates/configs/_Star Citizen/Ayre.template.yaml"

PUNCT = {"comma": ",", "period": ".", "slash": "/", "semicolon": ";", "apostrophe": "'",
         "lbracket": "[", "rbracket": "]", "minus": "-", "backslash": "\\"}

# Spare combos, in the order they get handed out. Right-hand modifiers only: Star
# Citizen's defaults and the pilot's own binds live on the left-hand ones.
POOL = ([f"rctrl+np_{n}" for n in range(10)] + [f"ralt+np_{n}" for n in range(10)]
        + [f"rctrl+f{n}" for n in range(1, 13)] + [f"rctrl+{n}" for n in "1234567890"]
        + [f"rctrl+{c}" for c in "abdefghijklmnopqrstuwxyz"]  # no ctrl+c/ctrl+v
        + [f"ralt+{c}" for c in "abcdefghijklmnopqrstuvwxyz"]
        + [f"ralt+{n}" for n in "1234567890"]
        + [f"ralt+f{n}" for n in (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12)]  # never alt+f4
        + [f"{m}+{p}" for m in ("rctrl", "ralt") for p in PUNCT])

# Template commands Ayre's layer replaces (toggles and duplicates of the same action).
REPLACED = {"Cycle Attacker", "Cycle Hostile", "Launch Countermeasure Decoy", "Launch Countermeasure Noise",
            "Increase Decoys", "Decrease Decoys", "Toggle Main Power On or Off", "Toggle Thrusters",
            "Toggle Shields", "Toggle Weapons", "Initiate Quantum Jump", "More missiles", "Reset missiles",
            "Toggle Weapon Gimbals", "Flight Ready", "Toggle Decoupled Mode", "Toggle Landing System",
            "Toggle VToL", "Ping Area for Resources and Vehicles", "Toggle Headlights",
            "Toggle Light Amplification", "Contact ATC",
            # blind toggle chains; routines replace them
            "Launch Sequence", "Landing Sequence"}


def wingman_key(sc):
    """Star Citizen key token -> Wingman keyboard action."""
    special = {"np_enter": {"hotkey": "enter", "hotkey_codes": [28], "hotkey_extended": True},
               "equals": {"hotkey": "=", "hotkey_codes": [13]}}
    if sc in special:
        return special[sc]
    names = {"rctrl": "right ctrl", "ralt": "right alt", "lctrl": "left ctrl", "lalt": "left alt",
             "lshift": "shift", "rshift": "right shift"}
    parts = [names.get(p) or PUNCT.get(p) or (f"num {p[3:]}" if p.startswith("np_") else p) for p in sc.split("+")]
    return {"hotkey": "+".join(parts)}


def wingman_actions(key, hold_modifier=False):
    """Wingman actions for a key. hold_modifier: press the modifier, then the key, then
    release, with the timing upstream found Star Citizen needs for Flight Ready."""
    if key.startswith("mouse"):  # mouse4 / mouse5 are the side buttons x / x2
        return [{"mouse": {"button": {"mouse4": "x", "mouse5": "x2"}.get(key, key)}}]
    if not hold_modifier or "+" not in key:
        return [{"keyboard": wingman_key(key)}]
    mod, base = key.split("+", 1)
    mod = wingman_key(mod)["hotkey"]
    return [{"keyboard": {"hotkey": mod, "press": True}}, {"wait": 0.55},
            {"keyboard": {"hotkey": wingman_key(base)["hotkey"], "hold": 0.1}}, {"wait": 0.15},
            {"keyboard": {"hotkey": mod, "release": True}}]


def kb_binds(action_el, mouse=False):
    """Non-empty single-press binds in the keyboard/mouse slot of an <action>, as SC tokens
    without the kb1_ prefix. Mouse buttons (kb1_mouse5) only when mouse=True."""
    return [rb.get("input")[4:] for rb in action_el.iter("rebind")
            if rb.get("input", "").startswith("kb1_") and rb.get("input")[4:].strip()
            and (mouse or not rb.get("input")[4:].startswith("mouse"))
            and not rb.get("activationMode") and not rb.get("multiTap")]


def main(bdir):
    live = ET.parse(bdir / "actionmaps.xml").getroot()
    export_path = next(p for p in sorted(bdir.glob("layout_*_exported.xml")) if "_AYRE_" not in p.name)
    export = ET.parse(export_path)
    layer = yaml.safe_load((HERE / "ayre_layer.yaml").read_text())
    keys_file = HERE / "ayre_keys.json"
    keys = json.loads(keys_file.read_text()) if keys_file.exists() else {}
    pilot = yaml.safe_load((HERE / "pilot_keys.yaml").read_text()) or {}
    reserved = {spec["key"] for actions in layer.values() for spec in actions.values() if spec.get("key")}

    # every action the game knows, which actionmaps it sits in, and keys already taken
    maps_of, taken, slot = {}, {}, {}
    for root in (live, export.getroot()):
        for amap in root.iter("actionmap"):
            for act in amap.iter("action"):
                maps_of.setdefault(act.get("name"), set()).add(amap.get("name"))
                if act.get("name") in pilot:
                    continue
                slot.setdefault(act.get("name"), set()).update(kb_binds(act, mouse=True))
                for k in kb_binds(act):
                    taken.setdefault(k, set()).add(act.get("name"))
    for action, k in pilot.items():
        taken.setdefault(k, set()).add(action)

    problems, commands = [], []
    for action in [a for a, k in keys.items() if k in reserved | set(taken)]:
        del keys[action]  # a pilot or default key now; she gets a fresh spare
    free = [k for k in POOL if k not in taken and k not in reserved and k not in keys.values()]
    for category, actions in layer.items():
        for action, spec in actions.items():
            if action not in maps_of:
                problems.append(f"{action}: not in your bindings files, skipped")
                continue
            own = sorted(slot.get(action, set()) | {k for k, acts in taken.items() if action in acts})
            if spec.get("key"):
                key, source = spec["key"], "default"
                keys.pop(action, None)
            elif own:
                key, source = own[0], "yours"
                keys.pop(action, None)
            else:
                if action not in keys:
                    if not free:
                        problems.append(f"{action}: out of spare keys")
                        continue
                    keys[action] = free.pop(0)
                key, source = keys[action], "ayre"
                clash = taken.get(key, set()) - {action}
                if clash:
                    problems.append(f"{action}: {key} is also bound to {', '.join(sorted(clash))}")
            commands.append((category, action, spec, key, source))

    # merge Ayre's keys into a copy of the pilot's profile
    root = export.getroot()
    name = root.get("profileName") + "_AYRE"
    root.set("profileName", name)
    root.find("CustomisationUIHeader").set("label", name)
    edits = [(a, k) for a, k in pilot.items()] + [(a, k) for _, a, _, k, src in commands if src == "ayre"]
    for action, key in edits:
        for amap_name in sorted(maps_of[action]):
            amap = root.find(f"actionmap[@name='{amap_name}']")
            if amap is None:
                amap = ET.SubElement(root, "actionmap", name=amap_name)
            act = amap.find(f"action[@name='{action}']")
            if act is None:
                act = ET.SubElement(amap, "action", name=action)
            for rb in [rb for rb in act.iter("rebind") if rb.get("input", "").startswith("kb1_")]:
                act.remove(rb)
            ET.SubElement(act, "rebind", input=f"kb1_{key}")
    ET.indent(export, " ")
    out = bdir / f"layout_{name}_exported.xml"
    export.write(out, encoding="unicode")

    # Ayre's Wingman commands
    tpl = yaml.safe_load(TEMPLATE.read_text())
    cat_ids = {c["name"]: c["id"] for c in tpl["command_categories"]}
    ours = {spec["name"] for _, _, spec, _, _ in commands}
    (bdir / "ayre_actions.json").write_text(json.dumps(
        {action: wingman_actions(key, spec.get("hold_modifier")) for _, action, spec, key, _ in commands},
        indent=1, sort_keys=True))
    # keep a template command only if none of its keys hit the pilot's keys or Ayre's
    used = {wingman_key(k)["hotkey"] for k in set(taken) | {c[3] for c in commands} if not k.startswith("mouse")}
    kept = []
    for c in tpl["commands"]:
        if c["name"] in REPLACED | ours:
            continue
        hits = {a["keyboard"]["hotkey"] for a in c.get("actions", []) if a.get("keyboard")} & used
        if hits:
            problems.append(f"dropped template command '{c['name']}': its key {', '.join(sorted(hits))} is already bound")
            continue
        kept.append(c)
    tpl["commands"] = kept
    for category, action, spec, key, _ in commands:
        if spec.get("assist_only"):
            continue
        context = spec.get("context", "")
        if spec.get("risky"):
            context = (context + ". " if context else "") + "Dangerous: ask Raven to confirm and wait for a yes first."
        cmd = {"name": spec["name"]}
        if context:
            cmd["additional_context"] = context
        cmd.update({"category_id": cat_ids[category], "is_system_command": False})
        if spec.get("say") and not spec.get("risky"):
            cmd["instant_activation"] = spec["say"]
        cmd.update({"force_instant_activation": False, "actions": wingman_actions(key, spec.get("hold_modifier"))})
        tpl["commands"].append(cmd)

    class Dumper(yaml.SafeDumper):
        pass
    Dumper.add_representer(str, lambda d, s: d.represent_scalar(
        "tag:yaml.org,2002:str", s, style="|" if "\n" in s else None))
    TEMPLATE.write_text(yaml.dump(tpl, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=1000))
    keys_file.write_text(json.dumps(keys, indent=1, sort_keys=True) + "\n")

    print(f"{len(commands)} commands ({sum(c[4] == 'yours' for c in commands)} on your keys), "
          f"{len(free)} spare keys left")
    print(f"import in game: {out.name}")
    for p in problems:
        print("WARN", p)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "bindings")
