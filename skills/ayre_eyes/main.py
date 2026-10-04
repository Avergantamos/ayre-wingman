"""Ayre's eyes: she looks at the Star Citizen screen herself.

look: answer a question about what's on screen (target, scan, status, landing), in her voice.
read_loadout: at boot, find the MFD view that lists weapons (trying the views she has found
  it on most often first), read weapons, groups and missiles, and remember them per ship.
arrange_weapon_groups: set the groups to the pilot's layout (first group all weapons, then all
  ballistics, then all lasers where the ship has them) by steering the MFD step by step,
  looking after every key press.

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
Answer only what was asked, numbers first, one or two short sentences, calm and direct, in
character, calling the pilot Raven. If something is not visible, say so plainly; never guess."""

LOADOUT = """Read this Star Citizen ship screen and return JSON only, no prose:
{"ship": "<ship name if shown, else null>",
 "groups": [{"number": <group number as shown>, "weapons": ["<weapon name>", ...]}],
 "weapons": [{"name": "...", "size": <int or null>, "type": "laser|ballistic|distortion|neutron|tachyon|other"}],
 "missiles": [{"name": "...", "size": <int or null>, "seeker": "EM|IR|CS|null", "count": <int>}],
 "visible": true|false}
Set "visible" to false if no weapons or loadout list is on screen. Include only what you can read."""

STEP = """You are operating a Star Citizen ship MFD (multi-function display) with keys only.
Goal for the weapon groups: {goal}
Available keys: up, down, left, right (move the highlight), next, previous (cycle the value of the
highlighted item), select (activate the highlighted item).
Look at the screen and return JSON only:
{{"done": true|false, "key": "<one key from the list, or null when done>", "why": "<short>"}}
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


def groups_match(data: dict) -> bool:
    have = [set(g.get("weapons", [])) for g in data.get("groups", [])]
    return all(i < len(have) and have[i] == set(want) for i, (_, want) in enumerate(wanted_groups(data)))


class AyreEyes(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.loadout: dict | None = None

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

    async def _ask(self, image: Image.Image, system: str, question: str, label: str) -> str:
        # wide enough that HUD numbers stay readable on a 3440 px ultrawide
        w = min(2048, image.width)
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

    async def _find_loadout(self) -> dict:
        for view in self._view_order():
            if not await self._press(view):
                continue
            await asyncio.sleep(0.8)  # let the screen switch before looking
            data = parse_json(await self._ask(self._grab(), "Return JSON only.", LOADOUT, "loadout"))
            if data.get("visible"):
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
        return await self._ask(self._grab(), HUD, question, focus)

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

    def _summary(self, data: dict) -> str:
        groups = "; ".join(
            f"group {g.get('number')}: {', '.join(g.get('weapons', [])) or 'empty'}" for g in data.get("groups", []))
        weapons = ", ".join(f"{w.get('name')} (S{w.get('size')}, {w.get('type')})" for w in data.get("weapons", []))
        missiles = ", ".join(f"{m.get('count')}x {m.get('name')} (S{m.get('size')}, {m.get('seeker')})"
                             for m in data.get("missiles", []))
        return f"{data.get('ship') or 'ship'}. Weapons: {weapons or 'none'}. Groups: {groups or 'none'}. Missiles: {missiles or 'none'}."

    async def get_prompt(self) -> str | None:
        base = await super().get_prompt() or ""
        if not self.loadout:
            return base or None
        return (base + "\n\nCurrent ship loadout (read at boot): " + self._summary(self.loadout) +
                "\nPick weapon groups and missiles from this: shields up, energy weapons; shields down, "
                "ballistics; to disable, distortion. Match missile seeker to the target's strongest "
                "signature. Select only, never fire.").strip()
