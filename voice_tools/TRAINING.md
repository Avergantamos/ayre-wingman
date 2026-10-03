# Training Ayre's voice (on the gaming PC)

About an hour, mostly waiting. Close Star Citizen first: training uses the whole GPU.

## 1. Bring the clips over

Copy `voice/clips/` from the Mac (USB or OneDrive, about 200 MB) to `C:\ayre-wingman\voice\clips\`.
It holds the WAV lines plus `train.list` (1,131 clean lines, 53 minutes).

## 2. Install GPT-SoVITS

Download the Windows integrated package from the GPT-SoVITS GitHub releases (RVC-Boss/GPT-SoVITS),
unzip to `C:\GPT-SoVITS`, run `go-webui.bat`. A page opens in the browser.
Labels below are from the v2 WebUI; newer versions may word them slightly differently.

## 3. Prepare the data (tab "1-GPT-SoVITS-TTS", sub-tab "1A")

- Experiment name: `ayre`
- Text labelling file: `C:\ayre-wingman\voice\clips\train.list`
- Audio dataset folder: `C:\ayre-wingman\voice\clips`
- Click the one-click formatting button and wait until it reports done.

## 4. Train (sub-tab "1B")

- SoVITS training: defaults are fine on a 3090 Ti (batch size can go up to 12 to 16). Start it, wait.
- GPT training: start it, wait. 15 epochs is a good first try.

## 5. Listen (sub-tab "1C")

- Pick the newest `ayre` GPT and SoVITS weights, open the inference page.
- Reference audio: `C:\ayre-wingman\voice\clips\0601.wav`
  with text `Raven, those unidentified machines were using encrypted communications.`
- Type a line, listen. If she sounds off, try the weights from an earlier epoch.

## 6. Connect her to Wingman AI

1. Start the GPT-SoVITS API: in `C:\GPT-SoVITS` run `runtime\python.exe api_v2.py` (port 9880).
2. Copy `voice_bridge\config.example.json` to `config.json` and set the two weights paths
   to the files you liked in step 5.
3. `python voice_bridge\server.py --load-weights` (port 9881). Leave it running.
4. Optional, once: `python voice_bridge\server.py --prerender` renders her stock lines so they play instantly.
5. In Ayre's config set `features: tts_provider: openai_compatible` (the bridge settings are already there)
   and restart Wingman AI.
