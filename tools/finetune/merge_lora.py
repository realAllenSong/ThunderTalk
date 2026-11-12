"""Merge a LoRA checkpoint into Qwen3-ASR and write a complete model folder.

    <venv-qwen>/bin/python tools/finetune/merge_lora.py CHECKPOINT OUT [--base Qwen/Qwen3-ASR-0.6B]

The result has the base model's architecture and size (float16 weights), so it
loads anywhere the base does (the app's MLX loader, ``eval --engines
qwen-mlx:OUT``) and runs at the same speed.
"""
import argparse
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoint")
    ap.add_argument("out")
    ap.add_argument("--base", default="Qwen/Qwen3-ASR-0.6B")
    a = ap.parse_args()
    from huggingface_hub import snapshot_download
    from peft import PeftModel
    from qwen_asr import Qwen3ASRModel
    from qwen3_asr_sft import copy_required_hf_files_for_qwen_asr
    base_dir = a.base if os.path.isdir(a.base) else snapshot_download(a.base)
    wrapper = Qwen3ASRModel.from_pretrained(base_dir, dtype=torch.float32, device_map=None)
    merged = PeftModel.from_pretrained(wrapper.model, a.checkpoint).merge_and_unload()
    from transformers import GenerationConfig
    merged.generation_config = GenerationConfig.from_model_config(merged.config)   # base file is copied below
    os.makedirs(a.out, exist_ok=True)
    merged.half().save_pretrained(a.out, safe_serialization=True)
    # save_pretrained drops tensors tied to another (lm_head = the embeddings);
    # the MLX loader wants every key, as in the original release.
    from safetensors.torch import save_file
    for fn in os.listdir(a.out):
        if fn.endswith(".safetensors"):
            os.remove(os.path.join(a.out, fn))
    save_file({k: v.detach().clone().contiguous() for k, v in merged.state_dict().items()},
              os.path.join(a.out, "model.safetensors"), metadata={"format": "pt"})
    copy_required_hf_files_for_qwen_asr(base_dir, a.out)
    print("merged →", a.out)


if __name__ == "__main__":
    main()
