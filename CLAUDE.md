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
6. Music: the official soundtrack via the upstream `spotify` skill, switched by mood (combat, quantum, landing, calm), ducked while Ayre speaks.

## Voice

- Target is a true Ayre voice, not a sound-alike. Local clone (GPT-SoVITS or similar) trained on clean dialogue clips cut from YouTube with `yt-dlp` + `ffmpeg`, music and SFX removed with `demucs`.
- Clips, trained voice models and any game audio stay out of git (`voice/` is gitignored) and never get shared.
- Comms sound comes from the upstream `voice_changer` effects on top of the clone.
