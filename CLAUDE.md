# ayre-wingman

A personal Star Citizen ship AI with Ayre's voice and character (Armored Core VI), built as a fork of ShipBit's Wingman AI. Personal use only. Read `AGENTS.md` and `skills/AGENTS.md` first: they are upstream's rules for core and skills, and they still apply.

## Where things run

- Code is written on the Mac, pushed to `origin` (Avergantamos/ayre-wingman), and pulled on the Windows gaming PC, where Star Citizen runs. Anything touching keys, screen capture or game audio can only be tested on the PC.
- Our work lives on the `ayre` branch. `upstream` is ShipBit; merge their `main` in occasionally, never push to it.

## Ayre

- Wingman config: `templates/configs/_Star Citizen/Ayre.template.yaml`, a copy of upstream's `Computer` wingman with her persona. Its keybinds are Star Citizen defaults until we import the real bindings export from the PC.
- She calls the pilot "Raven". Short answers, numbers first, a few words in combat.

## Safety rules (do not relax these)

- Every action is triggered by the pilot's voice. Ayre never targets, aims, fires or acts on her own.
- The model picks from a fixed list of named commands. Only command definitions send keys or clicks, never free text from the model.
- Irreversible or dangerous actions (self destruct, eject, quantum jump, power down) need a spoken "yes" first.

## Plan

1. Ayre wingman running on the PC with a voice, existing commands working.
2. Real bindings: import the PC's Star Citizen bindings export so commands press the right keys.
3. Scan skill: one command runs the bind sequence to focus and scan a target, then `vision_ai` reads the panels and reports what was asked for (cargo, owner, ship ID, power state, signatures, shields/hull). Fixed screen regions per panel, calibrated on the PC.
4. Power skill: named scenario profiles (combat, silent running, evade, cargo run, mining) as bind sequences.
5. Doors and room control: all-doors binds first; individual doors, fire venting and room atmosphere go through the ship's interaction screens, so they need vision to find the button plus a scripted click. Fragile, built last.
6. Music: the official soundtrack via the upstream `spotify` skill, switched by mood, ducked while Ayre speaks.
   - Combat: Contact With You (Balteus, confirmed), plus a rotation of the three final boss themes: Allmind, Cries of Coral, The Man Who Passed the Torch. Allmind is the favorite.
   - Calm, quantum, landing: picked from the OST later.
7. Routines (dynamic sequences), see below.

## Ayre's layer (her own keys)

- `sc_bindings/ayre_layer.yaml` lists what she controls: attacker/hostile cycling, countermeasures, quantum and NAV/SCM, weapon groups, interdiction, missiles, explicit power on/off and engineering allocation, MFD screens, scanning, flight systems, lights, doors.
- The pilot keeps toggles on the sticks. Ayre gets explicit on/off and set actions only, so she never has to guess a state.
- `python sc_bindings/build_layer.py` gives each action a spare right-hand combo (rctrl/ralt + numpad, F-keys, digits, letters), stable in `ayre_keys.json`, reuses the pilot's own keyboard key where one exists, merges them into a copy of the exported profile (`bindings/layout_<PROFILE>_AYRE_exported.xml`, imported in game) and regenerates her commands in the template. It drops any upstream template command whose key collides with the pilot's binds.
- Re-run it after any rebind. Never fires weapons or launches missiles; countermeasures, interdiction and target selection are fine.

## Routines: the model plans, the executor acts

The pilot names a goal ("prep for mining", "go dark", "combat ready"). The model builds a plan, the executor runs it, Ayre reports the outcome.

- Vocabulary: a compact list of named actions generated from the real bindings export (action name + one-line meaning). The model sees names, never keys. The executor maps names to keys.
- Plan: an ordered list of action names with optional waits. The executor rejects any step not in the vocabulary; risky steps need a spoken yes.
- State: Star Citizen exposes no state, and many binds are toggles. Prefer explicit on/off binds where they exist; otherwise read the state from the screen with vision before toggling, never toggle blind. After any toggle, Ayre states the resulting state out loud ("Shields on", "Lights off"), confirmed from the screen when possible, and says so when she could not confirm it.
- Verify: after running, read the relevant screen region or the game log and report what actually changed, not what was intended.
- Learn: a plan that worked can be saved by voice as a named routine ("remember this as salvage prep"). Saved routines run instantly with no model call next time.
- Scope: setup and mode changes (industrial, recon, combat, flight). Not real time piloting or aiming; planning takes seconds.

## Intent commands (planned)

- Weapons: "best weapons to pop this guy", "ballistics", "lasers", "shut him down" (distortion, else the best fallback). Needs the loadout per ship: which groups hold lasers, ballistics, distortion. Learned once per ship by reading the weapons screen with vision ("Ayre, learn my loadout"), stored per ship. She picks the group from the target's state (shields up: energy; shields down: ballistics; disable: distortion). Selects only, never fires.
- Missiles: "EM missiles", "biggest missile", "best to one-shot him". Rack contents learned the same way; seeker type matched to the target's strongest signature from a scan; size and damage from public ship and item data. "One-shot" is an estimate, she says so.

## Callout modes: fast HUD reader (planned, core building block)

- Fixed screen regions read with local OCR several times a second, no LLM in the loop: altitude, vertical speed, speed, target distance, closure rate.
- Landing callouts for dark ground with no external cameras: radar altitude and descent rate countdown, drift, "contact". Pairs with lights, night vision, gear down.
- Parking callouts for the Reclaimer claw and salvage heads: target the wreck, she calls distance and closure until the marked range. Range is calibrated once by voice ("mark this as claw range") instead of guessed.
- Mining: laser range and charge window callouts.
- She talks the pilot in; she never flies the ship. Holding the controls is automated piloting (the botting line) and too slow to be safe anyway.

## Game.log

- Reading `Game.log` is passive (a text file on disk, no contact with the game process), the lowest risk data source we have.
- Reported: CIG removed actor death and vehicle destruction events from the public log (kill trackers broke, SC Kill Monitor archived Nov 2025). Hit, damage and scan data were never in it. Check a real log from the PC before building on any event.
- So: who's shooting, ship ID, owner, cargo, power state and signatures come from the screen (vision). The log is for whatever events remain (location, quantum, session) and for triggering Ayre and music.

## Voice

- Gaming PC GPU: EVGA RTX 3090 Ti (24 GB), enough to train and run the clone locally.
- Target is a true Ayre voice, not a sound-alike. Local clone (GPT-SoVITS or similar) trained on clean dialogue clips cut from YouTube (source: an 85 minute all-English-lines video, `voice/raw/`) with `yt-dlp` + `ffmpeg`, music and SFX removed with `demucs`.
- Clips, trained voice models and any game audio stay out of git (`voice/` is gitignored) and never get shared.
- Comms sound comes from the upstream `voice_changer` effects on top of the clone.
