"""Dump DiT/text-encoder state and tokenizer output under one transformers version.

Used to find what differs between transformers 4.57.6 (sings) and 5.6.2 (silent vocals).
Usage: state_dump.py <5.6.2|4.57.6> <out.json>   (run with the venv-music python, behind lock.py gpu)
"""
import json
import os
from pathlib import Path
import sys

WORKTREE = Path(__file__).resolve().parents[2]
BENCH = Path(os.environ.get('TT_BENCH') or Path.home() / 'Library/Caches/ThunderTalk-bench')
CHECKPOINTS = BENCH / 'music-models/checkpoints'
sys.path.insert(0, str(WORKTREE / 'tools/music-spike'))
from run import LYRICS  # noqa: E402

CAPTION = 'Warm folk pop, acoustic guitar, soft piano, gentle brushed drums, clear vocal, calm sunrise mood.'


def stats(tensor):
    t = tensor.detach().float().cpu()
    return [list(t.shape), str(tensor.dtype), str(tensor.device), round(float(t.double().sum()), 4),
            round(float(t.double().abs().sum()), 4), round(float(t.double().std()) if t.numel() > 1 else 0.0, 6)]


def main():
    version, out = sys.argv[1], Path(sys.argv[2])
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.path.insert(0, str(WORKTREE / 'third_party'))
    if version == '4.57.6':
        sys.path.insert(0, str(BENCH / 'venv-music/transformers457'))
    import torch
    import transformers
    from transformers import AutoModel, AutoTokenizer
    from acestep.models.turbo.modeling_acestep_v15_turbo import AceStepConditionGenerationModel
    assert transformers.__version__ == version, transformers.__version__
    report = {'transformers': transformers.__version__}

    tokenizer = AutoTokenizer.from_pretrained(CHECKPOINTS / 'Qwen3-Embedding-0.6B')
    report['tokenizer_class'] = type(tokenizer).__name__
    report['pad_token_id'] = tokenizer.pad_token_id
    report['eos_token_id'] = tokenizer.eos_token_id
    texts = {'caption': CAPTION, 'lyrics': LYRICS, 'header': '# Languages\nen\n\n# Lyric\n' + LYRICS + '<|endoftext|>'}
    report['tokens'] = {k: tokenizer(v, add_special_tokens=False)['input_ids'] for k, v in texts.items()}
    report['tokens_special'] = {k: tokenizer(v)['input_ids'] for k, v in texts.items()}

    encoder = AutoModel.from_pretrained(CHECKPOINTS / 'Qwen3-Embedding-0.6B', dtype=torch.float32).eval()
    report['encoder_buffers'] = {n: stats(b) for n, b in encoder.named_buffers()}
    report['encoder_params'] = {n: stats(p) for n, p in encoder.named_parameters()}
    with torch.inference_mode():
        ids = torch.tensor([report['tokens']['caption']])
        report['encoder_hidden'] = stats(encoder(input_ids=ids).last_hidden_state)
        report['encoder_hidden_head'] = encoder(input_ids=ids).last_hidden_state[0, :3, :6].tolist()
        report['lyric_embed'] = stats(encoder.embed_tokens(torch.tensor([report['tokens']['lyrics']])))
    del encoder

    model, info = AceStepConditionGenerationModel.from_pretrained(
        CHECKPOINTS / 'acestep-v15-turbo', local_files_only=True, attn_implementation='sdpa',
        dtype=torch.bfloat16, output_loading_info=True)
    report['loading_info'] = {k: sorted(map(str, v)) for k, v in info.items()}
    report['dit_buffers'] = {n: stats(b) for n, b in model.named_buffers()}
    report['dit_params'] = {n: stats(p) for n, p in model.named_parameters()}
    report['dit_config_attn'] = getattr(model.config, '_attn_implementation', None)
    report['dit_config'] = {k: v for k, v in model.config.to_dict().items() if isinstance(v, (int, float, str, bool, list, type(None)))}
    out.write_text(json.dumps(report, indent=1) + '\n')
    print('wrote', out)


if __name__ == '__main__':
    main()
