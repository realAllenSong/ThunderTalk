"""Fine-tune Qwen3-ASR (0.6B) on the prepared data, on Apple silicon.

    uv run python -m tools.finetune.train_qwen [--steps 1200] [--freeze-encoder]

Runs tools/finetune/qwen3_asr_sft.py (the upstream trainer, float32 on MPS) in
the isolated venv at ~/Library/Caches/ThunderTalk-bench/finetune/venv-qwen.
Checkpoints are complete Hugging Face model folders, which the app's MLX
loader reads as they are (``models.get_model_path`` → mlx_qwen3_asr), so a
checkpoint is evaluated with ``thundertalk eval --engines <path>``-style runs
through tools/finetune/eval_checkpoint.py.

Language prefixes follow what the base model emits: Chinese for Chinese and
for mixed Chinese–English speech, English for English.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / "Library" / "Caches" / "ThunderTalk-bench" / "finetune"
PREP = ROOT / "prepared"
PY = ROOT / "venv-qwen" / "bin" / "python"
LANG = {"zh": "Chinese", "mixed": "Chinese", "en": "English"}


def to_qwen(src: Path, dst: Path) -> int:
    n = 0
    with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            r = json.loads(line)
            fout.write(json.dumps({"audio": r["audio"],
                                   "text": f"language {LANG[r['lang']]}<asr_text>{r['text']}"},
                                  ensure_ascii=False) + "\n")
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-ASR-0.6B")
    ap.add_argument("--out", default=str(ROOT / "runs" / "qwen06b"))
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--grad-acc", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--save-steps", type=int, default=100)
    ap.add_argument("--freeze-encoder", action="store_true")
    a = ap.parse_args()
    work = Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    n_train = to_qwen(PREP / "train.jsonl", work / "train.jsonl")
    n_valid = to_qwen(PREP / "valid.jsonl", work / "valid.jsonl")
    print(f"train {n_train} / valid {n_valid} lines", file=sys.stderr)
    cmd = [str(PY), str(Path(__file__).with_name("qwen3_asr_sft.py")),
           "--model_path", a.model, "--train_file", str(work / "train.jsonl"),
           "--eval_file", str(work / "valid.jsonl"), "--output_dir", str(work),
           "--batch_size", str(a.batch), "--grad_acc", str(a.grad_acc), "--lr", str(a.lr),
           "--epochs", str(a.epochs), "--save_steps", str(a.save_steps), "--save_total_limit", "12",
           "--num_workers", "0", "--pin_memory", "0", "--persistent_workers", "0",
           "--freeze_encoder", "1" if a.freeze_encoder else "0", "--resume", "1"]
    env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1", TOKENIZERS_PARALLELISM="false")
    sys.exit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    main()
