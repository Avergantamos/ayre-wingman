"""Ayre's eyes: she looks at the Star Citizen screen herself.

look: answer a question about what's on screen (target, scan, status, landing), in her voice.
read_loadout: at boot, find the MFD view that lists weapons (trying the views she has found
  it on most often first), read weapons, groups and missiles, and remember them per ship.
arrange_weapon_groups: set the groups to the pilot's layout (first group all weapons, then all
  ballistics, then all lasers where the ship has them) by steering the MFD step by step,
  looking after every key press.
scan_target: scan mode, scan MFD, run the scan, then read ship, owner (player/NPC/unknown),
  cargo, power, shields and crime status; the last scan is kept in her prompt for follow-ups.

She gets better as he plays in two ways: she remembers which view held the weapons and tries
it first next time, and every frame she reads is saved with its question and answer under
generated_files, the dataset for the fast HUD reader.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from mss import mss
from PIL import Image

from api.interface import CommandConfig, SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill, tool

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

ACTIONS = json.loads((Path(__file__).parent / "ayre_actions.json").read_text())

# MFD views in the order to try when she has no history yet
VIEWS = ["v_mfd_select_view_configuration_short", "v_mfd_select_view_self_status_short",
         "v_mfd_select_view_resource_network_short", "v_mfd_select_view_diagnostics_short",
         "v_mfd_select_view_target_status_short", "v_mfd_select_view_scanning_short",
         "v_mfd_select_view_ifcs_short", "v_mfd_select_view_comms_short"]

# the only keys she may press while arranging groups
MFD_KEYS = {"up": "v_mfd_movement_up_short", "down": "v_mfd_movement_down_short",
            "left": "v_mfd_movement_left_short", "right": "v_mfd_movement_right_short",
            "next": "v_mfd_interact_cycle_forwards_short", "previous": "v_mfd_interact_cycle_backwards_short",
            "select": "v_mfd_soft_select_mfd_primary_short"}
MAX_STEPS = 30

HUD = """You are Ayre, the ship AI riding with your pilot Raven in Star Citizen. You are looking at
Raven's screen right now. Star Citizen HUD notes: the selected target's panel shows ship type,
pilot or owner name, distance, shield and hull state; the scan screen shows signatures (EM, IR,
CS), cargo, crew, power state and crime status; the ship status screen shows your own shields,
hull, power and fuel; in landing mode the HUD shows radar altitude and vertical speed.
Own signatures (emissions): the power MFD (tabs PWR WPN THR SHLD COOL) has a bar under the tabs with
three readouts: heat-waves icon = IR, lightning icon = EM, double-diamond arrows icon = CS, each with
an upper and a lower number (for example 618.7 over 53.1). Report the numbers exactly as shown with
their signature names; never call the ship stealthy or quiet unless Raven asks you to judge it.
Missiles: the HUD shows the selected missile as [<seeker><size>] NAME, e.g. "[CS3] ARRSTR" is an
Arrester, size 3, CS = cross-section seeker (IR = infrared, EM = electromagnetic). A number like
"1/1" next to it is missiles armed/locked, NOT how many are carried; the total is only known if a
screen lists the rack. Read seeker and size exactly from the brackets.
Answer only what was asked, numbers first, one or two short sentences, calm and direct, in
character, calling the pilot Raven. If something is not visible, say so plainly; never guess a
count or a type. Never offer to fire weapons or missiles."""

LOADOUT = """Read this Star Citizen ship screen and return JSON only, no prose. The cockpit has several MFD
screens; the weapons list may be on any of them (often titled WEAPON CONFIG or VEHICLE CONFIGURATION,
listing weapon names with group columns). "GUNS (ALL)" and similar are group headers, not weapons:
weapons are the rows under them with real item names (e.g. OmniSky-9, Panther). Read whichever screen
shows them:
{"ship": "<ship name if shown, else null>",
 "groups": [{"number": <group number as shown>, "weapons": ["<weapon name>", ...]}],
 "weapons": [{"name": "...", "size": <int or null>, "type": "laser|ballistic|distortion|neutron|tachyon|other"}],
 "missiles": [{"name": "...", "size": <int or null>, "seeker": "EM|IR|CS|null", "count": <int or null>}],
 "visible": true|false}
Set "visible" to false if no weapons or loadout list is on screen. Include only what you can read.
Missiles: the HUD shows the selected one as [<seeker><size>] NAME ("[CS3] ARRSTR" = Arrester, size
3, CS seeker); take seeker and size from the brackets. Its "1/1" is armed/locked, not a count:
use count null unless a screen lists how many are carried."""

SCAN = """Read the Star Citizen scan results for the scanned target (scan MFD and HUD) and return JSON only:
{"visible": true|false,
 "ship": "<ship type/model, or null>",
 "owner": "<owner or pilot name as shown, or null>",
 "owner_type": "player|npc|unknown",
 "powered": true|false|null,
 "shields": "on|off|null",
 "cargo": ["<item and amount as shown>", ...] or null,
 "crime": "<crime status as shown, or null>"}
powered and shields: only true/false or on/off when the scan clearly shows it (a shield bar or value
for the target's shields, a power state field); otherwise null. A wrong "off" is worse than null.
cargo matters most: look for a cargo or contents section, commodity names and SCU amounts anywhere
in the scan results; use [] when the scan shows cargo as empty and null when no cargo info is shown.
owner_type: player when a player handle is shown; npc when the owner is a game faction, company or
security force, or the HUD marks it as an NPC; unknown when no owner is readable. Set "visible" to
false if no scan results are on screen yet. Include only what you can read; never guess."""

QED_SELECTED = """Look at the Star Citizen HUD weapon indicator. Is the selected weapon group the quantum
dampener / QED group (labelled QED, Q DAMPENER, QUANTUM DAMPENER or JAMMER)? Return JSON only:
{"qed_selected": true|false, "label": "<the selected group's label as shown, or null>"}
Answer true only if the label clearly says QED / dampener / jammer; guns or lasers selected is false."""

SPINE_GLOW = """This is a third-person view of Raven's ship. When the quantum dampener is active there is a
red glow along the ship's central spine (the middle ridge on top of the hull). Red lights at the
wingtips are navigation lights and do NOT count. Return JSON only:
{"spine_glow": true|false|null, "why": "<short>"}  (null if the ship isn't visible)"""

QED_GROUP = "v_weapon_preset_guns3"  # weapon group 4, the QED on the Sabre Raven EX
CAMERA = {"keyboard": {"hotkey": "f4"}}  # Star Citizen default: cockpit <-> third person
FIRE = {"mouse": {"button": "left"}}     # Star Citizen default fire (mouse 1); his profile doesn't rebind it

STEP = """You are operating a Star Citizen ship MFD (multi-function display) with keys only.
Goal for the weapon groups: {goal}
Available keys: up, down, left, right (move the highlight), next, previous (cycle the value of the
highlighted item), select (activate the highlighted item).
Look at the screen and return JSON only:
{{"done": true|false, "key": "<one key from the list, or null when done>", "why": "<short>"}}
Never change a group that holds the QED / quantum dampener / jammer.
Set done to true only when the groups on screen already match the goal. If the weapons screen is
not visible or you cannot tell how to proceed, return {{"done": false, "key": null, "why": "..."}}."""


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}


def wanted_groups(data: dict) -> list[tuple[str, list[str]]]:
    """The pilot's layout: everything first, then all ballistics, then all lasers, when present."""
    weapons = data.get("weapons", [])
    names = lambda kind: [w["name"] for w in weapons if w.get("type") == kind]
    layout = [("all weapons", [w["name"] for w in weapons])]
    for label, kind in (("all ballistics", "ballistic"), ("all lasers", "laser")):
        if names(kind):
            layout.append((label, names(kind)))
    return layout


def _calm(text):
    """HUD text arrives in capitals ("ESPERIA BLADE", "UNKNOWN"); her voice spells capitals out."""
    return re.sub(r"\b[A-Z][A-Z'-]{3,}\b", lambda m: m.group(0).capitalize(), str(text)) if text else text


def scan_summary(data: dict) -> str:
    data = {k: (_calm(v) if isinstance(v, str) else [_calm(x) for x in v] if isinstance(v, list) else v)
            for k, v in data.items()}
    owner = data.get("owner") or "no owner shown"
    kind = {"player": "player", "npc": "NPC"}.get(data.get("owner_type"), "unknown owner type")
    cargo = ", ".join(data.get("cargo") or []) or ("empty" if data.get("cargo") == [] else "not visible")
    power = {True: "powered on", False: "powered off"}.get(data.get("powered"), "power unknown")
    shields = {"on": "shields on", "off": "shields off"}.get(data.get("shields"), "shields unknown")
    crime = f", crime status {data['crime']}" if data.get("crime") else ""
    return f"{data.get('ship') or 'unknown ship'}, owner {owner} ({kind}), cargo: {cargo}; {power}, {shields}{crime}."


def groups_match(data: dict) -> bool:
    have = [set(g.get("weapons", [])) for g in data.get("groups", [])]
    return all(i < len(have) and have[i] == set(want) for i, (_, want) in enumerate(wanted_groups(data)))


class AyreEyes(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.loadout: dict | None = None
        self.last_scan: dict | None = None

    async def prepare(self) -> None:
        await super().prepare()
        # load the model on the Mac now, so Raven's first request doesn't wait ~30 s for it
        asyncio.create_task(self._warm_up())

    async def _warm_up(self) -> None:
        try:
            await self.llm_call([{"role": "user", "content": "Reply with: ready"}])
        except Exception:
            pass  # warming is best effort; a real request will load it anyway

    async def validate(self) -> list[WingmanInitializationError]:
        errors = await super().validate()
        self.retrieve_custom_property_value("display", errors)
        return errors

    def _dir(self, *parts) -> Path:
        d = Path(self.get_generated_files_dir(), *parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _grab(self) -> Image.Image:
        errors: list[WingmanInitializationError] = []
        display = self.retrieve_custom_property_value("display", errors) or 1
        with mss() as sct:
            shot = sct.grab(sct.monitors[display])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    async def _ask(self, image: Image.Image, system: str, question: str, label: str, full_res: bool = False) -> str:
        sample = image.resize((64, 27)).convert("L")
        lo, hi = sample.getextrema()
        if hi < 12 and sum(sample.getdata()) / (64 * 27) < 4:  # black capture: exclusive fullscreen
            return "I can't see the screen, Raven: it comes through black. Set Star Citizen to borderless window."
        # wide enough that HUD numbers stay readable on a 3440 px ultrawide; MFD text needs full resolution
        w = image.width if full_res else min(2048, image.width)
        small = image.resize((w, int(image.height * w / image.width)))
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=88)
        b64 = base64.b64encode(buf.getvalue()).decode()
        completion = await self.llm_call([
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
            ]},
        ])
        answer = completion.choices[0].message.content if completion and completion.choices else ""
        # keep the full-resolution frame with its question and answer for building the HUD reader
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        folder = self._dir("frames", label)
        image.save(folder / f"{stamp}.png")
        (folder / f"{stamp}.json").write_text(json.dumps({"question": question, "answer": answer}, indent=1))
        return answer

    async def _press(self, action: str) -> bool:
        """Press one of Ayre's bound actions through Wingman's own key sender."""
        if action not in ACTIONS:
            return False
        await self.wingman.execute_action(CommandConfig.model_validate({"name": action, "actions": ACTIONS[action]}))
        return True

    # memory: which MFD view held the weapons, so she goes there first next time
    def _view_order(self) -> list[str]:
        stats_file = self._dir() / "views.json"
        stats = json.loads(stats_file.read_text()) if stats_file.exists() else {}
        return sorted(VIEWS, key=lambda v: -stats.get(v, 0))

    def _remember_view(self, view: str) -> None:
        stats_file = self._dir() / "views.json"
        stats = json.loads(stats_file.read_text()) if stats_file.exists() else {}
        stats[view] = stats.get(view, 0) + 1
        stats_file.write_text(json.dumps(stats, indent=1))

    async def _read_loadout_now(self) -> dict:
        data = parse_json(await self._ask(self._grab(), "Return JSON only.", LOADOUT, "loadout", full_res=True))
        # the model sometimes lists the weapons and still says "not visible": weapons on screen is what counts
        return data if (data.get("visible") or data.get("weapons")) else {}

    async def _find_loadout(self) -> dict:
        # ships often show the weapons screen on one of their MFDs already: look before pressing anything
        data = await self._read_loadout_now()
        if data:
            return data
        for view in self._view_order():
            if not await self._press(view):
                continue
            await asyncio.sleep(0.8)  # let the screen switch before looking
            data = await self._read_loadout_now()
            if data:
                self._remember_view(view)
                return data
        return {}

    @tool(
        description="""Look at Raven's Star Citizen screen and answer about it: who is attacking,
        the selected target (ship, pilot, distance, shields, hull), scan results (cargo, owner,
        power state, signatures), own ship status, or altitude and speed when landing.
        For 'who is shooting me', run the Next Attacker command first, then call this.""",
        wait_response=True,
        summarize=False,
    )
    async def look(self, question: str, focus: str = "general") -> str:
        """
        Args:
            question: What Raven wants to know, in his words.
            focus: One of target, scan, status, landing, general. Labels the saved frame.
        """
        return await self._ask(self._grab(), HUD, question, focus, full_res=True)  # HUD and MFD text is small

    @tool(
        description="""Find the ship's weapons screen, read weapons, weapon groups and missiles,
        and remember them for this ship. Call right after Flight Ready and whenever Raven says the
        loadout changed. Says whether the groups still need arranging.""",
        wait_response=True,
    )
    async def read_loadout(self) -> str:
        data = await self._find_loadout()
        if not data:
            return "Could not find the weapons list on any ship screen. Say so to Raven in one line."
        self.loadout = data
        ship = re.sub(r"[^\w-]+", "_", data.get("ship") or "unknown_ship")
        (self._dir("loadouts") / f"{ship}.json").write_text(json.dumps(data, indent=1))
        status = "Groups already match Raven's layout." if groups_match(data) else \
            "Groups do not match Raven's layout yet: call arrange_weapon_groups."
        return f"Loadout read and saved: {self._summary(data)} {status}"

    @tool(
        description="""Arrange the weapon groups to Raven's layout: first group all weapons, then
        all ballistics, then all lasers where the ship has them. Steers the weapons screen key by
        key and checks after each press. Takes up to a minute.""",
        wait_response=True,
    )
    async def arrange_weapon_groups(self) -> str:
        if not self.loadout:
            return "Read the loadout first."
        goal = "; ".join(f"group {i + 1} = {label} ({', '.join(names)})"
                         for i, (label, names) in enumerate(wanted_groups(self.loadout)))
        for step in range(MAX_STEPS):
            move = parse_json(await self._ask(self._grab(), "Return JSON only.", STEP.format(goal=goal), "arrange"))
            if move.get("done"):
                break
            if move.get("key") not in MFD_KEYS:
                return f"Stopped arranging after {step} steps: {move.get('why') or 'could not tell what to press'}."
            await self._press(MFD_KEYS[move["key"]])
            await asyncio.sleep(0.4)
        else:
            return f"Stopped after {MAX_STEPS} steps without finishing. Groups may be partly arranged."
        await self.read_loadout()  # re-read to confirm what the groups hold now
        return ("Weapon groups arranged and confirmed: " if groups_match(self.loadout)
                else "Arranging finished but the groups do not fully match yet: ") + self._summary(self.loadout)

    @tool(
        description="""Scan the selected target and read the results: switches to scan mode and the
        scan screen, runs the scan, then reads ship type, owner (player, NPC or unknown), cargo,
        power, shields and crime status. Use when Raven says 'scan', 'scan him', 'what's he
        carrying', 'who owns that'. Tell Raven ship, owner and cargo; power and shields only if he
        asked. Takes a few seconds.""",
        wait_response=True,
    )
    async def scan_target(self) -> str:
        for action in ("v_set_scan_mode", "v_mfd_select_view_scanning_short", "v_scanning_trigger_scan"):
            await self._press(action)
            await asyncio.sleep(0.5)
        data = {}
        for wait in (2.5, 2.5):  # results fill in over a few seconds
            await asyncio.sleep(wait)
            data = parse_json(await self._ask(self._grab(), "Return JSON only.", SCAN, "scan"))
            if data.get("visible"):
                break
        if not data.get("visible"):
            return "No scan results on screen. Is a target selected and in range? Tell Raven in a few words."
        self.last_scan = data
        if data.get("cargo"):
            lead = "CARGO FOUND. Lead with it: 'Cargo aboard, Raven: <cargo>', then ship and owner."
        elif data.get("cargo") == []:
            lead = "Hold is empty: say 'No cargo', then ship and owner."
        else:
            lead = "Cargo not visible in the scan: say so briefly, then ship and owner."
        return f"Scan read: {scan_summary(data)} {lead} Power and shields only if asked."

    async def _run(self, name: str, *steps: dict) -> None:
        await self.wingman.execute_action(CommandConfig.model_validate({"name": name, "actions": list(steps)}))

    @tool(
        description="""Activate the quantum dampener (QED) on the Sabre Raven EX: selects weapon group 4,
        checks on the HUD that the QED is selected, fires it once, then checks in third person for the
        red glow on the ship's spine and switches back to all weapons. Use for 'QD', 'cutie' (QD
        misheard), 'activate my QD', 'quantum dampener', 'dampener on', 'jammer', 'QED'. The QD must be
        ON first (cockpit QD ON button or 3 power); if there's no glow, say QD may be off or bugged.""",
        wait_response=True,
    )
    async def activate_qd(self) -> str:
        await self._press(QED_GROUP)
        await asyncio.sleep(0.6)
        sel = parse_json(await self._ask(self._grab(), "Return JSON only.", QED_SELECTED, "qed", full_res=True))
        if not sel.get("qed_selected"):
            await self._press("v_weapon_preset_guns0")  # back to all weapons; nothing fired
            return (f"Weapon group 4 didn't show as the QED (saw {sel.get('label') or 'nothing readable'}). "
                    "Nothing fired; back on all weapons. Tell Raven in a few words.")
        await self._run("Fire QED", FIRE)  # the one fire she's allowed: the QED, never guns or missiles
        await asyncio.sleep(1.0)
        await self._run("Third person", CAMERA)
        await asyncio.sleep(1.5)
        glow = parse_json(await self._ask(self._grab(), "Return JSON only.", SPINE_GLOW, "qed"))
        await self._run("Cockpit view", CAMERA)
        await asyncio.sleep(0.5)
        await self._press("v_weapon_preset_guns0")  # his trigger fires guns again
        if glow.get("spine_glow"):
            return "Quantum dampener active: red glow on the spine. Back on all weapons. Tell Raven it's up."
        if glow.get("spine_glow") is False:
            return ("Fired the QED but no glow on the spine: QD may not be ON, or Q DAMPENER ACTIVATE is bugged. "
                    "Back on all weapons. Tell Raven to check QD ON.")
        return "Fired the QED but couldn't see the ship to confirm. Back on all weapons. Tell Raven it's unconfirmed."

    def _summary(self, data: dict) -> str:
        groups = "; ".join(
            f"group {g.get('number')}: {', '.join(g.get('weapons', [])) or 'empty'}" for g in data.get("groups", []))
        weapons = ", ".join(f"{w.get('name')} (S{w.get('size')}, {w.get('type')})" for w in data.get("weapons", []))
        missiles = ", ".join(f"{str(m['count']) + 'x ' if isinstance(m.get('count'), int) else ''}{m.get('name')}"
                             f" (S{m.get('size')}, {m.get('seeker')})" for m in data.get("missiles", []))
        return f"{data.get('ship') or 'ship'}. Weapons: {weapons or 'none'}. Groups: {groups or 'none'}. Missiles: {missiles or 'none'}."

    async def get_prompt(self) -> str | None:
        base = await super().get_prompt() or ""
        if self.last_scan:  # follow-ups ("is he powered?") answer from the last scan, no rescan
            base += "\n\nLast scan: " + scan_summary(self.last_scan)
        if not self.loadout:
            return base.strip() or None
        return (base + "\n\nCurrent ship loadout (read at boot): " + self._summary(self.loadout) +
                "\nPick weapon groups and missiles from this: shields up, energy weapons; shields down, "
                "ballistics; to disable, distortion. Match missile seeker to the target's strongest "
                "signature. Select only, never fire.").strip()
