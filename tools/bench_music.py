"""Performance A–D through the real MusicProcess; run under lock.py gpu.

No downloads. Isolated model root, original lyrics, synthetic dictation probe.
Results and phase events are retained as JSON beside each WAV. Peak is the
child's physical footprint (0.5 s), including Apple unified-memory heaps.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from thundertalk.core import audio_io, memory_policy, music
from thundertalk.core.music_metrics import physical_footprint
from thundertalk.core.music_process import MusicProcess
from thundertalk.core.music_worker import MusicRequest
from thundertalk.core.priority import DICTATION

LYRICS = '''[intro]
清晨的风吹过窗台
纸上的灯慢慢醒来

[verse]
我把星光折进信封
沿着小路走向天空
一行公式藏着希望
原文记下新的方向

[pre-chorus]
雨停以后继续远行
把每个字都唱清晰

[chorus]
我们唱欧拉SONG
让小小的愿望发光
穿过河流穿过山岗
把每一天写成诗章

[verse]
晚风轻轻推开木门
路边花朵陪着行人
地图边缘还没有名字
我们一步一步开始

[bridge]
不用赶上谁的脚步
听见自己的节奏

[chorus]
我们唱欧拉SONG
让小小的愿望发光
穿过河流穿过山岗
把每一天写成诗章

[outro]
清晨的风吹过窗台
新的故事正在到来
'''


def audio_metrics(path):
    import soundfile as sf
    import numpy as np
    x, sr = sf.read(path, dtype='float32', always_2d=True)
    # ffmpeg's BS.1770 loudness calculation; raw result retained for audit.
    ff = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-af',
                         'loudnorm=I=-16:TP=-1:LRA=11:print_format=json', '-f', 'null', '-'],
                        capture_output=True, text=True, check=True)
    end = ff.stderr.rfind('}') + 1
    loud = json.loads(ff.stderr[ff.stderr.rfind('{', 0, end):end])
    rms = float(np.sqrt(np.mean(x.astype('float64') ** 2)))
    peak = float(np.max(np.abs(x)))
    return {'sample_rate': sr, 'channels': x.shape[1], 'duration': len(x) / sr,
            'finite': bool(np.isfinite(x).all()), 'rms': rms, 'peak': peak,
            'lufs': float(loud['input_i']), 'true_peak_db': float(loud['input_tp']),
            'lra_lu': float(loud['input_lra'])}


def probe_clip(folder):
    import numpy as np
    source = folder / 'dictation-probe.aiff'
    subprocess.run(['say', '-v', 'Samantha', '-o', str(source), 'Morning lanterns light the river. We walk toward a quiet garden.'], check=True)
    x = audio_io.decode_audio(str(source), 16000)
    x = np.pad(x, (0, max(0, 80000 - len(x))))[:80000]
    audio_io.write_wav(str(folder / 'dictation-probe.wav'), x, 16000)
    source.unlink()
    return x


def main():
    from thundertalk.core.music_lyrics import sung_lyrics
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model', choices=['xl', 'turbo'], required=True)
    ap.add_argument('--song', action='store_true')
    ap.add_argument('--dit', choices=['mlx', 'mps'], default='mlx')
    ap.add_argument('--no-planner', action='store_true')
    ap.add_argument('--dictation', action='store_true')
    ap.add_argument('--probe-only', action='store_true', help='cancel after the concurrent probe; no WAV expected')
    ap.add_argument('--max-dictation-ratio', type=float, help='cancel early if the concurrent probe exceeds this ratio')
    ap.add_argument('--models-root', type=Path, default=Path.home() / 'Library/Caches/ThunderTalk-bench/music-models')
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    if a.probe_only and not a.dictation:
        ap.error('--probe-only requires --dictation')
    a.output.mkdir(parents=True, exist_ok=True)
    if list(a.output.glob("take-*.wav")):
        raise ValueError("Choose a fresh benchmark output directory; an earlier take already exists")
    m = music.XL if a.model == 'xl' else music.TURBO
    request = MusicRequest(project_root=str(a.models_root.resolve()), model_folder=m.folder,
                           job_dir=str(a.output.resolve()), caption='Warm folk pop, acoustic guitar, soft piano, '
                           'gentle brushed drums, calm sunrise mood.' + (' Clear Chinese vocal.' if a.song else ''),
                           lyrics=sung_lyrics(LYRICS, 'zh', user={}) if a.song else '',
                           shown_lyrics=LYRICS if a.song else '', instrumental=not a.song,
                           vocal_language='zh' if a.song else 'unknown', duration=170 if a.song else 60,
                           steps=m.steps, planner=not a.no_planner, dit_backend=a.dit, seeds=[42], estimate_s=music.estimate_take_s(m, 170 if a.song else 60, not a.no_planner))
    process = MusicProcess()
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *args: cancel.set())
    signal.signal(signal.SIGTERM, lambda *args: cancel.set())
    stop = threading.Event()
    first_step = threading.Event()
    peak, minimum, events, latencies = [0], [100.0], [], []
    start = time.monotonic()

    def on_event(event):
        now = time.monotonic() - start
        events.append({'seconds': now, 'event': event if event[0] != 'take' else event[:3]})
        if event[0] == 'step':
            first_step.set()
        if event[0] != 'tokens' or event[1] % 128 == 0:
            print(f'{now:.2f}s {event[:3]}', flush=True)

    def sample():
        while not stop.wait(0.5):
            peak[0] = max(peak[0], physical_footprint(process.pid))
            free = memory_policy.free_percent()
            if free is not None:
                minimum[0] = min(minimum[0], free)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    probe_thread = None
    engine = None
    if a.dictation:
        from thundertalk.core.asr import AsrEngine
        x = probe_clip(a.output)
        engine = AsrEngine()
        engine.load_model(str(a.models_root / 'lyric-asr'), 'Qwen3-ASR', 'onnx')
        engine.set_language('en')

        def recognise(label):
            press = time.monotonic()
            with _dictation():
                # app._start_capture raises priority at key press, before recording.
                # Simulate the five-second synthetic capture, then time release-to-text.
                capture_s = len(x) / 16000
                time.sleep(capture_s)
                t0 = time.monotonic()
                result = engine.recognize(x, 16000)
            latencies.append({'when': label, 'wall_s': time.monotonic() - t0,
                              'capture_s': capture_s, 'press_to_text_s': time.monotonic() - press,
                              'thread_id': threading.get_ident(),
                              'inference_ms': result.inference_ms, 'text': result.text})
            (a.output / 'dictation.json').write_text(json.dumps(latencies, indent=2) + '\n')
            print(f"Dictation {label}: {latencies[-1]['wall_s']:.3f}s", flush=True)
            if label == 'during_generation' and a.max_dictation_ratio is not None:
                if latencies[-1]['wall_s'] > a.max_dictation_ratio * latencies[0]['wall_s']:
                    print('Concurrent dictation exceeded the acceptance ratio; cancelling this attempt', flush=True)
                    cancel.set()

        idle_ready = threading.Event()

        def during():
            try:
                recognise('idle')
            finally:
                idle_ready.set()
            while not first_step.wait(0.1):
                if stop.is_set():
                    return
            time.sleep(0.2)
            recognise('during_generation')
            if a.probe_only:
                cancel.set()

        probe_thread = threading.Thread(target=during, daemon=True)
        probe_thread.start()
        if not idle_ready.wait(30) or not latencies:
            stop.set()
            engine.unload()
            raise RuntimeError('Idle dictation probe failed')
    start = time.monotonic()
    minimum[0] = 100.0
    outcome = 'error'
    error = ''
    try:
        outcome = process.run(request, on_event, cancel, ram_need_mb=music.ram_need_mb(m, not a.no_planner, dit_backend=a.dit))
    except Exception as exc:
        error = str(exc)
        print(error, flush=True)
    finally:
        wall = time.monotonic() - start
        stop.set()
        sampler.join(2)
        if probe_thread:
            probe_thread.join(30)
        if engine:
            engine.unload()
    takes = sorted(a.output.glob('take-*.wav'))
    phases = {event['event'][1]: event['seconds'] for event in events if event['event'][0] == 'phase'}
    steps = [event['seconds'] for event in events if event['event'][0] == 'step']
    take_time = next((e['seconds'] for e in events if e['event'][0] == 'take'), wall)
    load = phases.get('load', 0)
    plan = phases.get('plan', phases.get('compose', load))
    render = phases.get('render', take_time)
    write = phases.get('write', take_time)
    tokens, last = 0, 0
    for event in events:
        if event['event'][0] == 'tokens':
            n = event['event'][1]
            tokens += n - last if n >= last else n
            last = n
    planning = steps[0] - plan if steps else None
    costs = {'spawn_import_s': load, 'model_load_s': plan - load, 'planning_s': planning,
             'diffusion_s': render - steps[0] if steps else None, 'vae_decode_s': write - render,
             'write_s': take_time - write, 'exit_s': wall - take_time,
             'first_step_s': steps[1] - steps[0] if len(steps) > 1 else None,
             'steady_step_s': (steps[-1] - steps[1]) / (len(steps) - 2) if len(steps) > 2 else None,
             'planner_tokens_reported': tokens,
             'planner_tokens_per_second': tokens / planning if planning and tokens else None}
    result = {'model': a.model, 'song': a.song, 'dit_backend': a.dit, 'planner': not a.no_planner, 'outcome': outcome, 'error': error,
              'wall_s': wall, 'phase_costs': costs, 'peak_worker_bytes': peak[0], 'peak_worker_gib': peak[0] / 2**30,
              'minimum_system_free_percent': minimum[0], 'child_alive_after': process.pid is not None,
              'events': events, 'dictation': latencies,
              'audio': [audio_metrics(p) for p in takes]}
    (a.output / 'benchmark.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'events'}, ensure_ascii=False, indent=2))
    probes_ok = not a.dictation or (len(latencies) == 2 and all(p['text'].strip() for p in latencies))
    audio_ok = len(result['audio']) == 1 and all(
        p['finite'] and p['sample_rate'] == 48000 and p['channels'] == 2
        and abs(p['duration'] - request.duration) <= 1 and p['rms'] > 0.003
        and 0 < p['peak'] <= 1 for p in result['audio'])
    if a.probe_only:
        return 0 if outcome == 'cancelled' and probes_ok and process.pid is None else 1
    return 0 if outcome == 'done' and audio_ok and probes_ok and process.pid is None else 1


class _dictation:
    def __enter__(self):
        DICTATION.begin()

    def __exit__(self, *args):
        DICTATION.end()


if __name__ == '__main__':
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    raise SystemExit(main())
