"""Train Ayre's voice with GPT-SoVITS on the Mac, the same steps its web page runs, no clicking.

Run with GPT-SoVITS's own Python from anywhere:
    ~/Claude/Projects/GPT-SoVITS/.venv/bin/python voice_tools/train_mac.py
Each finished step is skipped on a rerun, so stopping (`ayre-mac train stop`) and starting again
picks up where it left off. CPU only: GPT-SoVITS says Mac-GPU training makes a worse voice.
Weights land in GPT-SoVITS/SoVITS_weights_v2Pro and GPT_weights_v2Pro as ayre_*.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SOVITS = Path.home() / "Claude/Projects/GPT-SoVITS"
CLIPS = REPO / "voice/clips"
EXP, VERSION = "ayre", "v2Pro"
SOVITS_EPOCHS, GPT_EPOCHS = 8, 15  # the web page's defaults
BATCH = 6  # web page default on a 48 GB Mac without half precision
OPT = SOVITS / "logs" / EXP
PY = sys.executable

os.chdir(SOVITS)
OPT.mkdir(parents=True, exist_ok=True)
base = {
    "version": VERSION, "is_half": "False", "exp_name": EXP, "opt_dir": str(OPT),
    "inp_text": str(CLIPS / "train.list"), "inp_wav_dir": str(CLIPS),
    "i_part": "0", "all_parts": "1", "_CUDA_VISIBLE_DEVICES": "0",
    "bert_pretrained_dir": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
    "cnhubert_base_dir": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
    "sv_path": "GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt",
    "pretrained_s2G": "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth",
    "s2config_path": f"GPT_SoVITS/configs/s2{VERSION}.json",
    "hz": "25hz",
    # the web page puts these on the import path for its scripts
    "PYTHONPATH": f"{SOVITS}{os.pathsep}{SOVITS / 'GPT_SoVITS'}",
}


def run(step: str, script: str, *args: str) -> None:
    t = time.time()
    print(f"== {step}", flush=True)
    subprocess.run([PY, "-s", script, *args], env={**os.environ, **base}, check=True)
    print(f"== {step} done in {(time.time() - t) / 60:.1f} min", flush=True)


lines = sum(1 for _ in open(CLIPS / "train.list"))

# 1A text -> phonemes
text = OPT / "2-name2text.txt"
if not text.exists() or text.stat().st_size < 100:
    run("1A text", "GPT_SoVITS/prepare_datasets/1-get-text.py")
    (OPT / "2-name2text-0.txt").rename(text)

# 1B audio features and speaker embeddings (v2Pro)
hubert = OPT / "4-cnhubert"
if not hubert.exists() or len(list(hubert.iterdir())) < lines * 0.95:
    run("1B audio features", "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py")
sv = OPT / "7-sv_cn"
if not sv.exists() or len(list(sv.iterdir())) < lines * 0.95:
    run("1B speaker embeddings", "GPT_SoVITS/prepare_datasets/2-get-sv.py")

# 1C semantic tokens
semantic = OPT / "6-name2semantic.tsv"
if not semantic.exists() or semantic.stat().st_size < 31:
    run("1C semantic tokens", "GPT_SoVITS/prepare_datasets/3-get-semantic.py")
    part = OPT / "6-name2semantic-0.tsv"
    semantic.write_text("item_name\tsemantic_audio\n" + part.read_text())
    part.unlink()

# 2 SoVITS (the voice's sound)
sovits_dir = SOVITS / f"SoVITS_weights_{VERSION}"
if not list(sovits_dir.glob(f"{EXP}_e{SOVITS_EPOCHS}_*.pth")):
    s2 = json.loads(Path(base["s2config_path"]).read_text())
    s2["train"].update(fp16_run=False, batch_size=BATCH, epochs=SOVITS_EPOCHS, text_low_lr_rate=0.4,
                       pretrained_s2G=base["pretrained_s2G"],
                       pretrained_s2D=base["pretrained_s2G"].replace("s2G", "s2D"),
                       if_save_latest=True, if_save_every_weights=True, save_every_epoch=4,
                       gpu_numbers="0", grad_ckpt=False, lora_rank=32)
    s2["model"]["version"] = VERSION
    s2["data"]["exp_dir"] = s2["s2_ckpt_dir"] = str(OPT)
    s2.update(save_weight_dir=f"SoVITS_weights_{VERSION}", name=EXP, version=VERSION)
    (OPT / f"logs_s2_{VERSION}").mkdir(exist_ok=True)
    cfg = OPT / "tmp_s2.json"
    cfg.write_text(json.dumps(s2))
    run("2 SoVITS training", "GPT_SoVITS/s2_train.py", "--config", str(cfg))

# 3 GPT (the voice's rhythm and delivery)
gpt_dir = SOVITS / f"GPT_weights_{VERSION}"
if not list(gpt_dir.glob(f"{EXP}-e{GPT_EPOCHS}.ckpt")):
    s1 = yaml.safe_load(Path("GPT_SoVITS/configs/s1longer-v2.yaml").read_text())
    s1["train"].update(precision="32", batch_size=BATCH, epochs=GPT_EPOCHS, save_every_n_epoch=5,
                       if_save_every_weights=True, if_save_latest=True, if_dpo=False,
                       half_weights_save_dir=f"GPT_weights_{VERSION}", exp_name=EXP)
    s1.update(pretrained_s1="GPT_SoVITS/pretrained_models/s1v3.ckpt",
              train_semantic_path=str(semantic), train_phoneme_path=str(text),
              output_dir=str(OPT / f"logs_s1_{VERSION}"))
    cfg = OPT / "tmp_s1.yaml"
    cfg.write_text(yaml.dump(s1, default_flow_style=False))
    run("3 GPT training", "GPT_SoVITS/s1_train.py", "--config_file", str(cfg))

print("TRAINING DONE")
print("SoVITS:", sorted(p.name for p in sovits_dir.glob(f"{EXP}_*.pth")))
print("GPT:", sorted(p.name for p in gpt_dir.glob(f"{EXP}-*.ckpt")))
