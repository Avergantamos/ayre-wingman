"""Ayre's live telemetry: Star Citizen's own local game data, so she knows the ship's state instead of
guessing it from the screen.

The game (5.0 PTU and later, display-only on 4.10.3 LIVE) serves gRPC on 127.0.0.1:2942 with no
login, only on the PC itself. It is off by default:
  5.0 PTU:  add the line  server.game.telemetry.enabled=1  to user.cfg in the game channel folder
            (e.g. C:\\Program Files\\Roberts Space Industries\\StarCitizen\\PTU\\user.cfg)
  4.10.3 LIVE: close the game and launcher, run  setx GRPC_CLIENT_SERVER_ENABLED true  in PowerShell
The stream starts once the character has spawned (Game.log: "Initializing game telemetry
component of local player"). CIG has not documented it; it can change with any patch.

What she does with it:
- Her prompt gets one line of live state (ship, mode, gear, fuel, quantum fuel, shields, hull,
  lights, doors, power, countermeasures, warnings; the pilot's health, oxygen, hunger, thirst, aUEC).
- Callouts through her speech gate: missile lock, hits, shields down, hull, fuel, quantum fuel,
  oxygen, health, thirst, hunger, downed, and the ship she just sat in. Each fires once per episode.
- One tool, ship_action: runs a game action through the API (5.0), turns toggles into explicit
  on/off from the live state, refuses self destruct, eject and emergency exit, and confirms the
  result from the stream. On LIVE (no actions) it tells the model to use her key commands.
- The first frames of each session are saved raw under generated_files/AyreTelemetry/frames so
  the units (fraction or percent) can be checked on the PC.

telemetry.desc is generated from telemetry.proto (no protoc needed on the PC):
  python -m grpc_tools.protoc -I skills/ayre_telemetry --descriptor_set_out=skills/ayre_telemetry/telemetry.desc skills/ayre_telemetry/telemetry.proto
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill, tool

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

HERE = Path(__file__).parent
SERVICE = "/sc.game.telemetry.v1.GameTelemetryService/"


def _load(name: str, file: str):
    """Sibling modules, loaded by path (custom skills load main.py outside a package)."""
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up while building the class
    spec.loader.exec_module(module)
    return module


STATE = _load("ayre_telemetry_state", "state.py")
VOICE = _load("ayre_voice_telemetry", "ayre_voice.py")


def message_classes() -> dict:
    """Message classes built from telemetry.desc at runtime."""
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString((HERE / "telemetry.desc").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for f in fds.file:
        pool.Add(f)
    get = lambda n: message_factory.GetMessageClass(pool.FindMessageTypeByName("sc.game.telemetry.v1." + n))
    return {n: get(n) for n in ("TelemetryStreamRequest", "TelemetryStreamResponse", "TriggerGameActionRequest",
                                "TriggerGameActionResponse", "ListTriggerableActionsRequest", "ListTriggerableActionsResponse")}


STATUS = {1: "OK", 2: "UNKNOWN_ACTION", 3: "MAP_INACTIVE", 4: "RATE_LIMITED", 5: "FAILED", 6: "HOLD_NOT_ALLOWED", 7: "FILTERED"}


class AyreTelemetry(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.ship = STATE.Ship()
        self.callouts = STATE.Callouts()
        self.available: set[str] | None = None  # actions the game offers (5.0); None = not known or LIVE
        self.connected = False
        self._task: asyncio.Task | None = None
        self._channel = None
        self._msgs: dict = {}
        self._saved = 0

    async def validate(self) -> list[WingmanInitializationError]:
        errors = await super().validate()
        try:
            import grpc  # noqa: F401
            self._msgs = message_classes()
        except Exception as e:
            errors.append(WingmanInitializationError(wingman_name=self.wingman.name, message=f"Ayre telemetry: {e}", error_type="missing_required_property"))
        return errors

    def _address(self) -> str:
        host = self.retrieve_custom_property_value("host", []) or "127.0.0.1"
        port = self.retrieve_custom_property_value("port", []) or 2942
        return f"{host}:{int(port)}"

    async def prepare(self) -> None:
        await super().prepare()
        self.gate = VOICE.SpeechGate.get(self.wingman, asyncio.get_running_loop())
        self._frames_dir = Path(self.get_generated_files_dir()) / "frames"
        self._task = asyncio.create_task(self._run())

    async def unload(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._channel:
            await self._channel.close()
        await super().unload()

    async def _run(self) -> None:
        """Stay connected: the game may start late, restart, or not have the service on."""
        import grpc
        M = self._msgs or message_classes()
        backoff = 5
        while True:
            try:
                self._channel = grpc.aio.insecure_channel(self._address())
                stream = self._channel.unary_stream(SERVICE + "TelemetryStream",
                                                    request_serializer=M["TelemetryStreamRequest"].SerializeToString,
                                                    response_deserializer=M["TelemetryStreamResponse"].FromString)
                await self._load_actions(M)
                started = time.monotonic()
                self._saved = 0
                async for frame in stream(M["TelemetryStreamRequest"]()):
                    if not self.connected:
                        self.connected = True
                        self.printr.print("Ayre telemetry: connected to the game", server_only=True)
                    self.ship.merge(frame)
                    self._save_frame(frame)
                    for line, prio in self.callouts.check(self.ship):
                        if time.monotonic() - started > 4:  # no burst of old warnings at connect
                            self.gate.say(line, prio)
                backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception as e:  # game closed, service off, or not spawned yet
                if self.connected:
                    self.printr.print(f"Ayre telemetry: link lost ({type(e).__name__})", server_only=True)
                self.connected = False
                self.available = None
                if self._channel:
                    try:
                        await self._channel.close()
                    except Exception:
                        pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _load_actions(self, M) -> None:
        """5.0 lists its triggerable actions; LIVE does not, which leaves available as None."""
        try:
            call = self._channel.unary_unary(SERVICE + "ListTriggerableActions",
                                             request_serializer=M["ListTriggerableActionsRequest"].SerializeToString,
                                             response_deserializer=M["ListTriggerableActionsResponse"].FromString)
            reply = await call(M["ListTriggerableActionsRequest"](), timeout=3)
            self.available = {a.action_name for a in reply.actions} or None
        except Exception:
            self.available = None

    def _save_frame(self, frame) -> None:
        """Keep the first 40 frames of each session (for checking units on the PC), then stop."""
        if self._saved >= 40:
            return
        try:
            from google.protobuf.json_format import MessageToDict
            self._frames_dir.mkdir(parents=True, exist_ok=True)
            path = self._frames_dir / time.strftime("%Y-%m-%d.jsonl")
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(MessageToDict(frame)) + "\n")
            self._saved += 1
        except Exception:
            self._saved = 40

    async def get_prompt(self) -> str | None:
        base = await super().get_prompt() or ""
        if not self.connected or time.monotonic() - self.ship.updated > 5:
            return base or None
        line = self.ship.summary()
        if not line:
            return base or None
        extra = "\n\nLive game telemetry (straight from Star Citizen, trust it over the screen): " + line + "."
        return (base + extra).strip()

    @tool(description="""Run a Star Citizen game action through the game's own API and confirm it
        from live telemetry. Exact names, e.g. v_lights_on, v_lights_off, v_deploy_landing_system,
        v_retract_landing_system, v_master_mode_set_scm, v_master_mode_set_nav, v_open_all_doors,
        v_close_all_doors, v_lock_all_doors, v_power_set_shields_on, v_flightready, v_atc_request,
        v_toggle_qdrive_engagement, v_vtol_on, toggle_flashlight, emote_wave. Only when Raven asks.""",
          wait_response=True)
    async def ship_action(self, action: str) -> str:
        if not self.connected:
            return "Game telemetry is not connected. Use the key command for this."
        if self.available is None:
            return "This game version does not take actions through the API (LIVE is read only). Use the key command."
        send, note = STATE.plan_action(action, self.ship, self.available)
        if not send:
            return note
        import grpc
        M = self._msgs or message_classes()
        call = self._channel.unary_unary(SERVICE + "TriggerGameAction",
                                         request_serializer=M["TriggerGameActionRequest"].SerializeToString,
                                         response_deserializer=M["TriggerGameActionResponse"].FromString)
        try:
            reply = await call(M["TriggerGameActionRequest"](action_name=send), timeout=3)
        except grpc.RpcError as e:
            return f"The game didn't take {send}: {e.code().name}."
        status = STATUS.get(reply.status, str(reply.status))
        if status != "OK":
            return f"The game answered {status} for {send}" + (f": {reply.message}" if reply.message else ".")
        for _ in range(15):  # up to 1.5 s for the stream to show the result
            await asyncio.sleep(0.1)
            ok = STATE.confirmed(send, self.ship)
            if ok:
                attr, want = STATE.CONFIRMABLE[send]
                return f"Done: {STATE.describe(attr, want)}, confirmed."
        ok = STATE.confirmed(send, self.ship)
        if ok is None:
            return f"Sent {send}. The game took it; that one isn't in the telemetry to confirm."
        return f"Sent {send}, but the telemetry doesn't show it yet."
