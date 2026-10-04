# Ayre: gaming PC setup (for Claude Code on the PC)

## Context

Ayre is a personal Star Citizen ship AI built on Wingman AI (ShipBit). Repo: https://github.com/Avergantamos/ayre-wingman, branch `ayre`. Read `CLAUDE.md` in the repo before changing anything; it holds the design and the safety rules.

- The pilot is Raven. He flies HOSAS (two VKB Gladiator EVO sticks, T-Rudder pedals) plus keyboard and mouse.
- Ayre's brain is NOT on this PC. A local model (Ollama, `qwen3-vl:30b`) runs on the pilot's Mac on the same home network: `http://Ians-MacBook-Pro.local:11434/v1`, fallback `http://10.0.0.96:11434/v1`. Nothing goes to a cloud AI, because screenshots show his handle and session ids. Do not switch her to a cloud provider.
- Everything Ayre-specific is configuration and add-ons for the official Wingman AI app. No Wingman code is modified.

## Rules

- Never fire weapons, launch missiles or aim through Ayre. Risky actions (quantum engage, main power, shields or thrusters off) go only through the `risky_action` lock.
- Don't edit Star Citizen bindings by hand. Ayre's keys come from `sc_bindings/build_layer.py`, which the installer runs.
- Don't push to `upstream` (ShipBit). Commit on `ayre` only when the pilot asks.

## Steps

1. **Python 3.11.** Check with `py -3.11 --version`. If it's missing: `winget install Python.Python.3.11`, or the 3.11.9 Windows installer from python.org with "Add python.exe to PATH" and the py launcher ticked. Any 3.11.x works. Not 3.12 or later: Wingman AI runs 3.11, and add-on libraries must match it.
2. **Wingman AI.** The pilot installs it from https://www.wingman-ai.com, then starts and closes it once. Check that `%APPDATA%\ShipBit\WingmanAI\<version>\configs` exists.
3. **Get the repo:** `git clone -b ayre https://github.com/Avergantamos/ayre-wingman C:\ayre-wingman`. Without git, unzip https://github.com/Avergantamos/ayre-wingman/archive/refs/heads/ayre.zip to `C:\ayre-wingman`.
4. **Find Star Citizen.** Default is `C:\Program Files\Roberts Space Industries\StarCitizen\LIVE`. Confirm `user\client\0\Profiles\default\actionmaps.xml` exists under it. If it's elsewhere, find the LIVE folder and pass it in step 6.
5. **Check the Mac brain is reachable:** `curl http://Ians-MacBook-Pro.local:11434/api/version`. If that fails, try `curl http://10.0.0.96:11434/api/version`.
   - If only the IP works, change `local_llm.endpoint` in `templates\configs\_Star Citizen\Ayre.template.yaml` to `http://10.0.0.96:11434/v1` before step 6.
   - If neither works: the Mac must be awake, on the same network, with its Ollama service loaded. Ask the pilot.
6. **Run the installer** from `C:\ayre-wingman`:
   `powershell -ExecutionPolicy Bypass -File install\install_ayre.ps1` (add `-StarCitizen "<LIVE path>"` if not default). It:
   - copies the live bindings and builds Ayre's key profile
   - installs the skills `ayre_eyes`, `ayre_flight` and `ayre_ship` into `%APPDATA%\ShipBit\WingmanAI\custom_skills`, with their `requirements.txt` libraries
   - installs `Ayre.yaml` into the newest Wingman version's `configs\_Star Citizen`

   Read its output and fix any error before going on.
7. **Safety checks before the game** (Star Citizen closed):
   - `py -3.11 tests\ayre\test_profile.py`: the pilot's binds all survived, Ayre's keys don't overlap his. Must say all passed.
   - `py -3.11 install\keytest.py` (needs `py -3.11 -m pip install keyboard` only if the import fails): every Ayre key leaves Windows correctly, numpad Enter is really numpad Enter, nothing stays held. Must say all keys arrived correctly.
   **Optional sanity tests:** `py -3.11 -m pip install numpy pillow pyyaml mss`, then run each of `tests\ayre\test_eyes.py`, `test_ship.py` and `test_flight.py` with `py -3.11`. Also `test_voice.py` and `test_health.py`. All passed on the Mac.
8. **Pilot steps in Wingman AI.** Restart it and select Ayre. Confirm:
   - Conversation provider: Local LLM, endpoint as above, model `qwen3-vl:30b`
   - Speech-to-text: fasterwhisper
   - Voice: Edge TTS (her trained voice comes later)

   Note any red errors.
9. **Pilot steps in Star Citizen:**
   - Graphics: window mode **Borderless**. Fullscreen can make screen captures black.
   - Options > Keybindings > Control Profiles: load **OCT2525VKBIAN_AYRE** (the installer prints the exact name).
10. **Hangar test, by the pilot:**
    - "Lights on"
    - "Open the doors"
    - "Flight ready": she should press flight ready, read the loadout and give a ready line
    - "What do you see?"
    - "Next attacker"
    - In landing mode with a target selected: "Calibrate the HUD"

## Report back to the Mac session

- What she said at startup: "Online, Raven" (all good) or the problem she named. "Status check" repeats it on demand.
- Which test lines worked and which didn't, and what Ayre said.
- Exact error text from the installer and from Wingman AI (its log is under `%APPDATA%\ShipBit\WingmanAI`).
- FPS in game with Ayre running vs closed, and GPU memory in Task Manager.
- Whether her key presses reach the game, especially numpad Enter for the doors and Right Alt+R for flight ready.
- Saved frames she read, under `%APPDATA%\ShipBit\WingmanAI\generated_files\AyreEyes\frames`. Don't send these anywhere: they show the pilot's handle. Only describe what's in them.

## Later: her voice

`voice_tools\TRAINING.md`. The clips come from the Mac by USB (`voice\clips`, about 200 MB, not in git).
