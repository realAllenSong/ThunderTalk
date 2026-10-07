"""Reproduce the Phase 0 inference-only ACE-Step copy from the pinned checkout."""
from pathlib import Path
import re
import shutil
import subprocess

SOURCE = Path.home() / "Library/Caches/ThunderTalk-bench/ace-step-src"
TARGET = Path(__file__).resolve().parents[2] / "third_party/acestep"
REVISION = "ca1e85fe9430179831e6bc6be790c332190a3866"
INIT_WEIGHTS_UPSTREAM = '''        std = self.config.initializer_range
        if isinstance(module, nn.Linear):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.bias is not None:
                module.bias.data.zero_()
        elif isinstance(module, nn.Embedding):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.padding_idx is not None:
                module.weight.data[module.padding_idx].zero_()
        elif isinstance(module, Qwen3RMSNorm):
            module.weight.data.fill_(1.0)
'''
INIT_WEIGHTS_PATCHED = '''        # ThunderTalk patch: transformers 5 runs this on every module after loading. Only the base
        # initializer's guarded helpers skip loaded tensors (writing through `.data` re-randomised
        # the checkpoint), and it refills the meta-built rotary buffers; the FSQ constants need
        # the same treatment. The base scheme (normal(std), zero bias, unit norms) is upstream's.
        super()._init_weights(module)
        if isinstance(module, ResidualFSQ):
            with torch.device("cpu"):
                fresh = ResidualFSQ(dim=self.config.fsq_dim, levels=self.config.fsq_input_levels,
                                    num_quantizers=self.config.fsq_input_num_quantizers)
            with torch.no_grad():
                for name, buffer in fresh.named_buffers():
                    module.get_buffer(name).copy_(buffer)
'''


def replace(path, old, new):
    source = path.read_text()
    if old not in source:
        raise ValueError(f"Patch no longer applies: {path}: {old[:80]}")
    path.write_text(source.replace(old, new))


def main():
    revision = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise ValueError(f"Unexpected upstream revision: {revision}")
    roots = ["core/generation/handler", "models/common", "models/mlx", "models/turbo", "models/xl_sft"]
    files = ["__init__.py", "handler.py", "inference.py", "llm_inference.py", "llm_backend_compat.py",
             "constrained_logits_processor.py", "constants.py", "gpu_config.py", "audio_utils.py",
             "debug_utils.py", "local_cache.py", "model_downloader.py", "core/__init__.py",
             "core/generation/__init__.py", "models/__init__.py", "core/scoring/dit_alignment.py",
             "core/scoring/_dtw.py"]
    for root in roots:
        files.extend(str(p.relative_to(SOURCE / "acestep")) for p in (SOURCE / "acestep" / root).rglob("*.py")
                     if not p.name.endswith("_test.py") and "lora" not in p.parts
                     and p.stem not in {"training_preset", "lyric_score", "lora_manager"})
    for name in files:
        src = SOURCE / "acestep" / name
        if src.exists():
            dst = TARGET / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    shutil.copyfile(SOURCE / "LICENSE", TARGET / "LICENSE")
    replace(TARGET / "core/scoring/_dtw.py", 'import numba\n', '')
    replace(TARGET / "core/scoring/_dtw.py", '@numba.jit(nopython=True)\n', '')
    for name in ["handler.py", "core/generation/handler/__init__.py"]:
        path = TARGET / name
        text = path.read_text()
        text = re.sub(r'^from \.(lora_manager|training_preset|lyric_score) import .*\n', '', text, flags=re.M)
        text = re.sub(r'^    (LoraManagerMixin|TrainingPresetMixin|LyricScoreMixin),\n', '', text, flags=re.M)
        text = re.sub(r'^    "(LoraManagerMixin|TrainingPresetMixin|LyricScoreMixin)",\n', '', text, flags=re.M)
        path.write_text(text)
    replace(TARGET / "core/generation/handler/init_service_orchestrator.py",
            '        vae_checkpoint: Optional[str] = None,',
            '        vae_checkpoint: Optional[str] = None,\n        dtype=None,')
    replace(TARGET / "core/generation/handler/init_service_orchestrator.py",
            '            self.quantization = normalized_quantization',
            '            if dtype is not None:\n                self.dtype = dtype\n            self.quantization = normalized_quantization')
    replace(TARGET / "core/generation/handler/init_service_orchestrator.py",
            '            self._sync_model_code_if_needed(config_path, checkpoint_path)\n', '')
    replace(TARGET / "core/generation/handler/init_service_loader.py", '        from transformers import AutoModel',
            '        if "xl" in os.path.basename(model_checkpoint_path):\n'
            '            from acestep.models.xl_sft.modeling_acestep_v15_xl_base import AceStepConditionGenerationModel\n'
            '        else:\n'
            '            from acestep.models.turbo.modeling_acestep_v15_turbo import AceStepConditionGenerationModel')
    replace(TARGET / "core/generation/handler/init_service_loader.py", 'AutoModel.from_pretrained(', 'AceStepConditionGenerationModel.from_pretrained(')
    replace(TARGET / "core/generation/handler/init_service_loader.py",
            '        if not self.offload_to_cpu:\n            self.model = self.model.to(device).to(self.dtype)',
            '        if getattr(self, "_defer_mps_decoder", False):\n'
            '            # Convert the temporary CPU decoder directly to MLX before MPS placement.\n'
            '            self.model = self.model.to("cpu").to(self.dtype)\n'
            '        elif not self.offload_to_cpu:\n            self.model = self.model.to(device).to(self.dtype)')
    replace(TARGET / "core/generation/handler/init_service_orchestrator.py",
            '            model_path = os.path.join(checkpoint_dir, config_path)',
            '            # ThunderTalk: avoid a complete temporary MPS decoder alongside MLX weights.\n'
            '            self._defer_mps_decoder = resolved_device == "mps" and use_mlx_dit\n'
            '            model_path = os.path.join(checkpoint_dir, config_path)')
    replace(TARGET / "core/generation/handler/init_service_orchestrator.py",
            '            status_msg = self._build_initialize_status_message(',
            '            if self._defer_mps_decoder:\n'
            '                # Successful MLX conversion removed the decoder. A failed conversion\n'
            '                # keeps it here for the normal torch fallback.\n'
            '                self.model = self.model.to(resolved_device).to(self.dtype)\n\n'
            '            status_msg = self._build_initialize_status_message(')
    replace(TARGET / "core/generation/handler/init_service_loader.py", '                    trust_remote_code=True,', '                    local_files_only=True,')
    replace(TARGET / "llm_inference.py", 'trust_remote_code=True', 'trust_remote_code=False')
    (TARGET / "core/generation/handler/init_service_downloads.py").write_text(
        '"""ThunderTalk downloads models separately; generation is strictly offline."""\n'
        'class InitServiceDownloadsMixin:\n'
        '    def _ensure_models_present(self, *, checkpoint_path, config_path, prefer_source, vae_variant=None):\n'
        '        for name in (config_path, "vae", "Qwen3-Embedding-0.6B"):\n'
        '            folder = checkpoint_path / name\n'
        '            if not folder.is_dir() or not any(folder.glob("*.safetensors")):\n'
        '                return f"Missing model data: {folder}", False\n'
        '        return None\n')
    (TARGET / "model_downloader.py").write_text(
        '"""Offline checkpoint paths. Downloads belong to ThunderTalk, never upstream."""\n'
        'import os\nfrom pathlib import Path\n\nDEFAULT_VAE_VARIANT = "official"\n\n'
        'def get_checkpoints_dir():\n'
        '    path = os.environ.get("ACESTEP_CHECKPOINTS_DIR")\n'
        '    return Path(path) if path else Path(os.environ.get("ACESTEP_PROJECT_ROOT", os.getcwd())) / "checkpoints"\n\n'
        'def resolve_vae_path(checkpoint_dir, vae_variant=None):\n'
        '    if vae_variant not in (None, "", "official"):\n'
        '        raise ValueError("Only the official VAE is supported")\n'
        '    return Path(checkpoint_dir) / "vae"\n')
    replace(TARGET / "models/mlx/dit_convert.py", '        weights.append((new_key, mx.array(np_val)))',
            '        converted = mx.array(np_val).astype(mx.bfloat16)\n'
            '        # ThunderTalk patch: evaluate now so each fp32 copy is freed per tensor;\n'
            '        # a lazy astype kept the whole fp32 decoder alive (XL load peaked > 24 GiB).\n'
            '        mx.eval(converted)\n'
            '        weights.append((new_key, converted))')
    replace(TARGET / "core/generation/handler/mlx_dit_init.py", '            self.mlx_decoder = mlx_decoder',
            '            # The torch encoder/tokenizer remain; only the duplicate decoder is freed.\n'
            '            self.model.decoder = None\n'
            '            import gc\n            import torch\n            gc.collect()\n'
            '            if self.device == "mps":\n                torch.mps.empty_cache()\n'
            '            self.mlx_decoder = mlx_decoder')
    (TARGET / "_hooks.py").write_text('"""ThunderTalk worker hooks; default callbacks do nothing."""\n'
                                     'TIMEOUT_S = 1800\n\n'
                                     'def on_step(done, total):\n    pass\n\n'
                                     'def on_tokens(done, total):\n    pass\n')
    path = TARGET / "core/generation/handler/generate_music_execute.py"
    replace(path, 'import os\n', 'from acestep import _hooks\n')
    replace(path, '_DEFAULT_GENERATION_TIMEOUT = int(os.environ.get("ACESTEP_GENERATION_TIMEOUT", "600"))',
            '_DEFAULT_GENERATION_TIMEOUT = _hooks.TIMEOUT_S')
    for path in [TARGET / "models/mlx/dit_generate.py", TARGET / "models/turbo/modeling_acestep_v15_turbo.py",
                 TARGET / "models/xl_sft/modeling_acestep_v15_xl_base.py"]:
        text = path.read_text()
        text = re.sub(r'^(\s*)for step_idx in (.*):$',
                      r'\1for step_idx in \2:\n\1    from acestep import _hooks\n\1    _hooks.on_step(step_idx, num_steps)', text, flags=re.M)
        path.write_text(text)
    replace(TARGET / "llm_inference.py", '            pbar.update(1)',
            '            if pbar.n % 16 == 0:\n                from acestep import _hooks\n'
            '                _hooks.on_tokens(pbar.n, max_new_tokens)\n            pbar.update(1)')
    replace(TARGET / "models/xl_sft/modeling_acestep_v15_xl_base.py",
            '            for step_idx, (t_curr, t_prev) in enumerate(iterator):',
            '            for step_idx, (t_curr, t_prev) in enumerate(iterator):\n'
            '                from acestep import _hooks\n                _hooks.on_step(step_idx, infer_steps)')
    for path in [TARGET / 'models/turbo/modeling_acestep_v15_turbo.py',
                 TARGET / 'models/xl_sft/modeling_acestep_v15_xl_base.py']:
        text = path.read_text()
        pattern = r'(?m)^        self.quantizer = ResidualFSQ\([\s\S]*?^        \)'
        text, count = re.subn(pattern, lambda match: '        with torch.device("cpu"):\n' +
                             '\n'.join('    ' + line for line in match[0].splitlines()), text)
        if count != 1:
            raise ValueError(f'Expected one FSQ constructor in {path}, found {count}')
        path.write_text(text)
        replace(path, INIT_WEIGHTS_UPSTREAM, INIT_WEIGHTS_PATCHED)
    for path in TARGET.rglob('*.py'):
        path.write_text('# ruff: noqa  # Pinned upstream source; local integration is linted separately.\n' + path.read_text())
    print(f"Vendored {len(files)} files from {revision}")


if __name__ == "__main__":
    main()
