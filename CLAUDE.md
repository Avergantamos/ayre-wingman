# ayre-wingman

A personal Star Citizen ship AI with Ayre's voice and character (Armored Core VI), built as a fork of ShipBit's Wingman AI. Personal use only. Read `AGENTS.md` and `skills/AGENTS.md` first: they are upstream's rules for core and skills, and they still apply.

## Where things run

- Code is written on the Mac, pushed to `origin` (Avergantamos/ayre-wingman), and pulled on the Windows gaming PC, where Star Citizen runs. Anything touching keys, screen capture or game audio can only be tested on the PC.
- Our work lives on the `ayre` branch. `upstream` is ShipBit; merge their `main` in occasionally, never push to it.

## Ayre

- Wingman config: `templates/configs/_Star Citizen/Ayre.template.yaml`, a copy of upstream's `Computer` wingman with her persona. Its keybinds are Star Citizen defaults until we import the real bindings export from the PC.
- She calls the pilot "Raven". Short answers, numbers first, a few words in combat.

## Safety rules (do not relax these)

- Ayre never fires weapons or launches missiles, and never aims.
- State changes (power, modes, doors, flight) happen only when the pilot asks or says yes to her offer. On her own she may only switch screens and read them (boot loadout read, status checks).
- Piloting assists (below) are pilot-started, short, single-axis, speed-limited, and end the moment the pilot touches a stick or says stop.
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
- Keyboard only, by the pilot's choice: no vJoy for Ayre (joysticks are grouped in game and an extra one would disturb his sticks). Her combos replace Star Citizen's keyboard default only on her own actions; the pilot flies on sticks. Assist strafing presses the keyboard defaults (W, S, A, D, Space, his C) without rebinding them.
- `sc_bindings/pilot_keys.yaml` holds the pilot's own keyboard changes (strafe down on Left Ctrl, decoupled on C, the defaults). Flight Ready (Right Alt + R) and ATC/hangar (Left Alt + N) stay on the defaults he leans on; Ayre presses those keys instead of rebinding. Same for his power toggles (U, I, O, P), which she never touches, and his mouse 4/5 missile binds, which she clicks.
- `assist_only` actions (screen navigation, strafe, pitch, yaw) are bound for her code but never exposed to the model as voice commands. `bindings/ayre_actions.json` maps every action to the key she presses.
- Re-run it after any rebind. Never fires weapons or launches missiles; countermeasures, interdiction and target selection are fine.

## Routines: the model plans, the executor acts

The pilot names a goal ("prep for mining", "go dark", "combat ready"). The model builds a plan, the executor runs it, Ayre reports the outcome.

- Vocabulary: a compact list of named actions generated from the real bindings export (action name + one-line meaning). The model sees names, never keys. The executor maps names to keys.
- Plan: an ordered list of action names with optional waits. The executor rejects any step not in the vocabulary; risky steps need a spoken yes.
- State: Star Citizen exposes no state, and many binds are toggles. Prefer explicit on/off binds where they exist; otherwise read the state from the screen with vision before toggling, never toggle blind. After any toggle, Ayre states the resulting state out loud ("Shields on", "Lights off"), confirmed from the screen when possible, and says so when she could not confirm it.
- Verify: after running, read the relevant screen region or the game log and report what actually changed, not what was intended.
- Learn: a plan that worked can be saved by voice as a named routine ("remember this as salvage prep"). Saved routines run instantly with no model call next time.
- Scope: setup and mode changes (industrial, recon, combat, flight). Not real time piloting or aiming; planning takes seconds.

## Ayre's eyes (skill, built)

- `skills/ayre_eyes`: `read_loadout` tries MFD views in the order that has worked most often (`views.json`, so she learns where each pilot's weapons live); `arrange_weapon_groups` steers the MFD key by key with a look after every press (max 30 steps, only MFD keys) to the layout: first group all weapons, then all ballistics, then all lasers when present, and re-reads to confirm. Untested in game: the MFD's real behavior decides how well the loop works.
- `look` answers about the screen in her voice (one model call, no second summarizing pass); `read_loadout` opens Configuration, falls back to Ship Status, reads weapons, groups and missiles as JSON and stores them per ship; the current loadout is fed into her prompt so plain-words weapon and missile picks need no setup.
- Every frame she reads is saved full resolution with its question and answer under Wingman's generated_files/AyreEyes/frames/<focus>/. That is the dataset for the fast HUD reader and its screen regions, collected from real play on the PC.
- Runs on the official app: copy the folder to %APPDATA%\ShipBit\WingmanAI\custom_skills\ayre_eyes. Uses only libraries Wingman already bundles (mss, Pillow).
- Screen capture needs Star Citizen in borderless or windowed mode; exclusive fullscreen can capture black.

## Flight sequences and partner behavior (in her persona; detection is planned)

Sequences, small talk, quips and music rules are in her backstory now and run from Raven's words and what she sees when she looks. Noticing on her own (combat starting, touchdown) waits for the HUD reader and Game.log watcher. Music is the upstream Spotify skill (needs a Spotify developer app and Premium); ducking under her voice is planned.

She acts like a good copilot: one short line per phase, wording varied from a pool of pre-written variants with no repeats close together, silent while numbers are not changing. "Quiet" and "talk more" by voice.

- Boot ("flight ready"): power up, switch an MFD to the view that lists weapons and missiles (Configuration or Self Status, confirm on the PC), read and store the loadout for this ship every boot, check shields, power, fuel. Then one ready line that includes the loadout ("Lasers on one, ballistics on two, four Arresters. Ready, Raven.").
- Takeoff: after liftoff and climbing, she offers gear up.
- SCM / NAV: confirms the mode change; on NAV she readies quantum.
- Landing: offers lights and night vision when the view is dark, gear down near the ground, descent assist if asked. On touchdown: "Down. Cut thrusters?" so the ship does not slide away.
- Power down ("shut her down"): one confirmation, then thrusters, shields, weapons, main power off, and a short sign-off.

## Piloting assists (planned)

Pilot-started, a few seconds long, one axis only, so she never steers:
- Descent: in coupled mode IFCS already holds the hover and kills drift, so landing only needs the descent rate. She pulses strafe down from the radar altitude reading, slowing toward the ground, and stops at contact.
- Close to range: the pilot points the ship at a targeted wreck; she moves fore and aft only, using target distance and closure, until the marked claw or salvage range, then brakes.
- Guards: speed limiter set low first; abort and brake if a reading is lost or stale; any physical stick input or "stop" hands control back instantly; never near other players' ships.
- Steering: allowed when needed (yaw and pitch to face a wreck, lateral strafe to line up), only with weapons powered off for the whole assist and only toward derelicts (scan shows no owner) or the ground. Weapons off is what makes it impossible to use as an aim assist.
- Needs: the fast HUD reader (target marker position on screen, distance, altitude, speeds).

## Intent commands (planned)

- Loadout comes from the boot read (no separate command needed).
- Weapon groups: the first group (guns0) always holds every weapon and is never changed. At boot Ayre arranges the other groups by type (for example lasers, ballistics, distortion) through the MFD weapons screen, using the MFD navigation keys and vision to check each step, then names them in the ready line.
- Weapons: "best weapons to pop this guy", "ballistics", "lasers", "shut him down" (distortion, else the best fallback). Needs the loadout per ship: which groups hold lasers, ballistics, distortion. Read at every boot. She picks the group from the target's state (shields up: energy; shields down: ballistics; disable: distortion). Selects only, never fires.
- Missiles: "EM missiles", "biggest missile", "best to one-shot him". Rack contents learned the same way; seeker type matched to the target's strongest signature from a scan; size and damage from public ship and item data. "One-shot" is an estimate, she says so.

## Callout modes: fast HUD reader (planned, core building block)

- Fixed screen regions read with local OCR several times a second, no LLM in the loop: altitude, vertical speed, speed, target distance, closure rate.
- Landing callouts for dark ground with no external cameras: radar altitude and descent rate countdown, drift, "contact". Pairs with lights, night vision, gear down.
- Parking callouts for the Reclaimer claw and salvage heads: target the wreck, she calls distance and closure until the marked range. Range is calibrated once by voice ("mark this as claw range") instead of guessed.
- Mining: laser range and charge window callouts.
- Callouts by default; the descent and close-to-range assists above use the same readings.

## Game.log

- Reading `Game.log` is passive (a text file on disk, no contact with the game process), the lowest risk data source we have.
- Reported: CIG removed actor death and vehicle destruction events from the public log (kill trackers broke, SC Kill Monitor archived Nov 2025). Hit, damage and scan data were never in it. Check a real log from the PC before building on any event.
- So: who's shooting, ship ID, owner, cargo, power state and signatures come from the screen (vision). The log is for whatever events remain (location, quantum, session) and for triggering Ayre and music.

## Latency (why this fork exists)

Upstream Wingman AI defaults to cloud services for speech-to-text, the model and the voice. Ours keeps the model in the cloud only when thinking or vision is needed:
- Speech-to-text local (upstream's faster-whisper / whisper.cpp providers) on the 3090 Ti.
- Instant phrases skip the model entirely: phrase, then key press, then a pre-rendered Ayre line.
- Ayre's stock lines (confirmations, ready lines, callouts) are rendered once to audio files with her cloned voice and played instantly; only free-form answers are spoken live by the local voice model.
- HUD reader and piloting assists are local code, no model in the loop.
- The cloud model handles questions, plans and screen reading, and starts with a short pre-rendered acknowledgement so the wait is never silent.

## Voice

- Gaming PC GPU: EVGA RTX 3090 Ti (24 GB), enough to train and run the clone locally.
- Target is a true Ayre voice, not a sound-alike. Local clone (GPT-SoVITS or similar) trained on clean dialogue clips cut from YouTube (source: an 85 minute all-English-lines video, `voice/raw/`) with `yt-dlp` + `ffmpeg`, music and SFX removed with `demucs`.
- Clips, trained voice models and any game audio stay out of git (`voice/` is gitignored) and never get shared.
- Comms sound comes from the upstream `voice_changer` effects on top of the clone.
- No Wingman code change for the voice: `voice_bridge/server.py` is an OpenAI-shaped speech endpoint in front of GPT-SoVITS api_v2, used through upstream's `openai_compatible` TTS provider. Every rendered line is cached in `voice/cache`, and `--prerender` renders `stock_lines.txt` ahead of time. Steps: `voice_tools/TRAINING.md`.
- `AYRE_PHRASES.md` (written by the builder) lists the instant phrases; anything else works in natural speech through the model.
