"""Compare two state_dump.py reports and print every difference that matters."""
import json
import sys


def close(a, b):
    if a[0] != b[0]:
        return False
    return all(abs(x - y) <= 1e-3 * max(1.0, abs(x), abs(y)) for x, y in zip(a[3:], b[3:]))


def main():
    a, b = (json.load(open(p)) for p in sys.argv[1:3])
    print('versions', a['transformers'], b['transformers'])
    for key in ('tokenizer_class', 'pad_token_id', 'eos_token_id', 'dit_config_attn', 'loading_info'):
        if a.get(key) != b.get(key):
            print(f'DIFF {key}: {a.get(key)} != {b.get(key)}')
    for group in ('tokens', 'tokens_special'):
        for name in a[group]:
            if a[group][name] != b[group].get(name):
                print(f'DIFF {group}.{name}: len {len(a[group][name])} vs {len(b[group].get(name) or [])}')
                print('   a', a[group][name][:24])
                print('   b', (b[group].get(name) or [])[:24])
    for key in ('encoder_hidden', 'lyric_embed'):
        if not close(a[key], b[key]):
            print(f'DIFF {key}: {a[key]} != {b[key]}')
    for group in ('encoder_buffers', 'encoder_params', 'dit_buffers', 'dit_params'):
        names = set(a[group]) | set(b[group])
        bad = 0
        for name in sorted(names):
            x, y = a[group].get(name), b[group].get(name)
            if x is None or y is None or not close(x, y):
                bad += 1
                if bad <= 40:
                    print(f'DIFF {group}.{name}: {x} != {y}')
        print(f'{group}: {len(names)} tensors, {bad} differ')
    for key in sorted(set(a['dit_config']) | set(b['dit_config'])):
        if a['dit_config'].get(key) != b['dit_config'].get(key):
            print(f"config {key}: {a['dit_config'].get(key)} != {b['dit_config'].get(key)}")


if __name__ == '__main__':
    main()
