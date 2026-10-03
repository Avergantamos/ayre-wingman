"""Ayre's eyes: she looks at the Star Citizen screen herself.

look: answer a question about what's on screen (target, scan, status, landing), in her voice.
read_loadout: at boot, open the ship's configuration screen, read weapons, groups and
missiles, and remember them per ship so weapon and missile picks need no setup.

Every screenshot she reads is saved with its question and answer under generated_files,
so later the fast HUD reader can be built and checked against real frames from this PC.
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

from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill, tool

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

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


class AyreEyes(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.loadout: dict | None = None

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
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = self._dir("frames", label)
        image.save(folder / f"{stamp}.png")
        (folder / f"{stamp}.json").write_text(json.dumps({"question": question, "answer": answer}, indent=1))
        return answer

    async def _press(self, command_name: str) -> bool:
        command = self.wingman.get_command(command_name)
        if not command:
            return False
        await self.wingman.execute_action(command)
        return True

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
        description="""Read the ship's weapons, weapon groups and missiles from its screens and
        remember them for this ship. Call right after Flight Ready, and whenever Raven says the
        loadout changed. Returns a short summary for the ready line.""",
        wait_response=True,
    )
    async def read_loadout(self) -> str:
        for view in ("Show Configuration", "Show Ship Status"):
            if not await self._press(view):
                continue
            await asyncio.sleep(0.8)  # let the screen switch before looking
            raw = await self._ask(self._grab(), "Return JSON only.", LOADOUT, "loadout")
            match = re.search(r"\{.*\}", raw, re.S)
            try:
                data = json.loads(match.group(0)) if match else {}
            except json.JSONDecodeError:
                data = {}
            if data.get("visible"):
                break
        else:
            return "Could not find the weapons list on the ship screens. Say so to Raven in one line."

        self.loadout = data
        ship = re.sub(r"[^\w-]+", "_", data.get("ship") or "unknown_ship")
        (self._dir("loadouts") / f"{ship}.json").write_text(json.dumps(data, indent=1))
        return "Loadout read and saved: " + self._summary(data)

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
