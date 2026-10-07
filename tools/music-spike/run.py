"""Phase 0 gate: a disposable offline worker with objective audio checks.

Run through both bench locks. No microphone, user settings or recordings are used.
"""
import argparse
import ctypes
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

WORKTREE = Path(__file__).resolve().parents[2]
# gate.sh points HOME at a throwaway directory, so the bench root must not derive from it.
BENCH = Path(os.environ.get('TT_BENCH') or Path.home() / "Library/Caches/ThunderTalk-bench")
LYRICS = """[Verse]
Morning light upon the water
Footsteps on an empty road
We can build a little shelter
From the kindness that we sow
[Chorus]
Carry light across the river
Let the quiet garden grow
Every day a new beginning
Every seed a place to go
"""


def footprint(pid):
    buf = ctypes.create_string_buffer(1024)
    lib = ctypes.CDLL('/usr/lib/libproc.dylib')
    if lib.proc_pid_rusage(pid, 2, ctypes.byref(buf)):
        return 0
    return ctypes.c_uint64.from_buffer(buf, 16 + 7 * 8).value


def free_percent():
    output = subprocess.check_output(['memory_pressure'], text=True)
    return int(output.split('free percentage:')[-1].strip().rstrip('%'))


def serve(connection, config):
    try:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['HF_HOME'] = str(BENCH / 'music-models/hf-cache')
        os.environ['ACESTEP_PROJECT_ROOT'] = config['output']
        os.chdir(config['output'])
        sys.path.insert(0, str(WORKTREE / 'third_party'))
        if config['transformers'] == '4.57.6':
            fallback = BENCH / 'venv-music/transformers457'
            if not fallback.is_dir():
                raise RuntimeError(f'Missing transformers fallback: {fallback}')
            sys.path.insert(0, str(fallback))
        if config['vae_fp16']:
            os.environ['ACESTEP_MLX_VAE_FP16'] = '1'
        if config['mx_cache_gb']:
            import mlx.core as mx
            mx.set_cache_limit(int(config['mx_cache_gb'] * 1e9))

        def event(kind, **data):
            connection.send({'kind': kind, 'time': time.monotonic(), **data})

        event('import')
        import numpy as np
        import soundfile as sf
        import torch
        import transformers
        from acestep import _hooks
        from acestep.handler import AceStepHandler
        from acestep.inference import GenerationConfig, GenerationParams, generate_music
        from acestep.llm_inference import LLMHandler

        if transformers.__version__ != config['transformers']:
            raise RuntimeError(f"Wrong transformers: {transformers.__version__}")
        event('load', transformers=transformers.__version__, torch=torch.__version__)
        def step(done, total):
            # A boundary means GPU completion, not merely a queued Python call.
            if config['mlx']:
                import mlx.core as mx
                mx.synchronize()
            else:
                torch.mps.synchronize()
            event('step', done=done, total=total)

        _hooks.on_step = step
        _hooks.on_tokens = lambda done, total: event('tokens', done=done, total=total)
        handler = AceStepHandler()
        status, ok = handler.initialize_service(
            project_root=str(BENCH / 'music-models'), config_path='acestep-v15-' + config['model'],
            device='mps', dtype=torch.bfloat16, use_mlx_dit=config['mlx'], compile_model=False)
        if not ok:
            raise RuntimeError(status)
        if config['vae_chunk']:
            handler.mlx_vae_chunk_size = config['vae_chunk']
        if config['vae_nocompile']:
            handler._mlx_compiled_decode = None
        if config['torch_vae']:
            handler.use_mlx_vae = False
        resolve = handler._resolve_mlx_decode_fn

        def traced_resolve():
            import mlx.core as mx
            fn = resolve()

            def tile(z):
                out = fn(z)
                mx.eval(out)
                event('vae_tile', frames=int(z.shape[1]), active_gb=round(mx.get_active_memory() / 1e9, 2),
                      cache_gb=round(mx.get_cache_memory() / 1e9, 2), mlx_peak_gb=round(mx.get_peak_memory() / 1e9, 2))
                return out
            return tile

        handler._resolve_mlx_decode_fn = traced_resolve
        decode = handler._decode_generate_music_pred_latents

        def traced_decode(*args, **kwargs):
            import mlx.core as mx
            event('vae_decode', torch_mps_gb=round(torch.mps.driver_allocated_memory() / 1e9, 2),
                  mlx_active_gb=round(mx.get_active_memory() / 1e9, 2), mlx_cache_gb=round(mx.get_cache_memory() / 1e9, 2))
            result = decode(*args, **kwargs)
            event('vae_done')
            return result

        handler._decode_generate_music_pred_latents = traced_decode
        event('loaded', mlx_dit=bool(handler.use_mlx_dit), mlx_vae=bool(handler.use_mlx_vae),
              vae_chunk=handler.mlx_vae_chunk_size, dtype=str(handler.dtype))
        if config['mlx'] and not handler.use_mlx_dit:
            raise RuntimeError('MLX initialization fell back to torch')
        rotary_buffers = {}
        for name, module in handler.model.named_modules():
            if module.__class__.__name__ == 'Qwen3RotaryEmbedding':
                rotary_buffers[name] = module.inv_freq.detach().cpu().float().tolist()
        event('rotary_buffers', values=rotary_buffers)
        lm = LLMHandler()
        if config['planner']:
            event('load_planner')
            status, ok = lm.initialize(str(BENCH / 'music-models/checkpoints'),
                                      'acestep-5Hz-lm-1.7B', backend='mlx', device='mps')
            if not ok:
                raise RuntimeError(status)
        event('generate')
        params = GenerationParams(
            caption='Warm folk pop, acoustic guitar, soft piano, gentle brushed drums, clear vocal, calm sunrise mood.' if config['vocal']
            else 'Warm instrumental folk, acoustic guitar, soft piano, gentle brushed drums, calm sunrise mood.',
            lyrics=LYRICS if config['vocal'] else '[Instrumental]', instrumental=not config['vocal'],
            vocal_language='en' if config['vocal'] else 'unknown', duration=config['duration'], bpm=90,
            inference_steps=config['steps'], seed=42, thinking=config['planner'],
            use_cot_metas=config['planner'], use_cot_caption=False, use_cot_language=False,
            dcw_enabled=False)
        result = generate_music(handler, lm, params,
                                GenerationConfig(batch_size=1, use_random_seed=False, seeds=[42], audio_format='wav'),
                                save_dir=config['output'])
        if not result.success or not result.audios:
            raise RuntimeError(result.error or result.status_message)
        audio = result.audios[0]
        samples = audio['tensor'].detach().cpu().float().numpy().T
        if samples.ndim != 2 or samples.shape[1] != 2 or not np.isfinite(samples).all():
            raise RuntimeError('Invalid channels or non-finite audio')
        peak = float(np.abs(samples).max())
        rms = float(np.sqrt(np.mean(samples ** 2)))
        duration = len(samples) / audio['sample_rate']
        if audio['sample_rate'] != 48000 or abs(duration - config['duration']) > 1 or not 0.001 < peak <= 1 or rms < 0.0001:
            raise RuntimeError(f'Invalid audio: duration={duration}, peak={peak}, rms={rms}')
        path = str(Path(config['output']) / 'gate.wav')
        sf.write(path, samples, 48000, subtype='PCM_16')
        latents = result.extra_outputs.get('pred_latents')
        if latents is not None:
            latents = latents.detach().cpu().float()
            torch.save(latents, Path(config['output']) / 'latents.pt')
            event('latents', shape=list(latents.shape), mean=float(latents.mean()), std=float(latents.std()),
                  abs_max=float(latents.abs().max()))
        event('done', path=path, duration=duration, sample_rate=48000, channels=2,
              peak_dbfs=20 * float(np.log10(peak)), rms=rms,
              time_costs=result.extra_outputs.get('time_costs', {}))
    except BaseException:
        connection.send({'kind': 'error', 'time': time.monotonic(), 'detail': traceback.format_exc()})
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['turbo', 'xl-sft'], default='turbo')
    parser.add_argument('--transformers', choices=['5.6.2', '4.57.6'], default='5.6.2')
    parser.add_argument('--mlx', action='store_true')
    parser.add_argument('--planner', action='store_true')
    parser.add_argument('--vocal', action='store_true')
    parser.add_argument('--duration', type=int, default=10)
    parser.add_argument('--steps', type=int, default=8)
    parser.add_argument('--vae-chunk', type=int, default=0, help='MLX VAE tile size in latent frames (min 192)')
    parser.add_argument('--vae-fp16', action='store_true')
    parser.add_argument('--vae-nocompile', action='store_true', help='decode without mx.compile')
    parser.add_argument('--mx-cache-gb', type=float, default=0, help='cap the MLX buffer cache')
    parser.add_argument('--torch-vae', action='store_true', help='decode with the PyTorch MPS VAE')
    parser.add_argument('--tag', default='', help='suffix for the output directory')
    args = vars(parser.parse_args())
    label = f"{args['model']}-{args['transformers']}-{'mlx' if args['mlx'] else 'mps'}-{'vocal' if args['vocal'] else 'instrumental'}"
    if args['vae_chunk']:
        label += f"-vae{args['vae_chunk']}"
    if args['vae_fp16']:
        label += '-vaefp16'
    if args['vae_nocompile']:
        label += '-nocompile'
    if args['mx_cache_gb']:
        label += f"-mxcache{args['mx_cache_gb']:g}"
    if args['torch_vae']:
        label += '-torchvae'
    if args['tag']:
        label += f"-{args['tag']}"
    output = WORKTREE / 'artifacts/music-spike' / label
    output.mkdir(parents=True, exist_ok=True)
    args['output'] = str(output)
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=serve, args=(child, args), daemon=True)
    # lock.py rechecks after acquisition but currently only warns if headroom dropped.
    # Keep the required admission threshold here as well.
    while free_percent() < 40:
        print('Waiting for 40% free memory after acquiring the bench locks', flush=True)
        time.sleep(3)
    start = last_event = time.monotonic()
    events = []
    peak = 0
    minimum = 100
    low = 0
    phase = 'spawn'
    phase_peaks = {}
    process.start()
    child.close()
    try:
        while process.is_alive() or parent.poll():
            if parent.poll(0.5):
                try:
                    event = parent.recv()
                except EOFError:
                    break
                last_event = time.monotonic()
                event['footprint_gb'] = round(footprint(process.pid) / 1e9, 2)
                if event['kind'] not in ('step', 'tokens'):
                    phase = event['kind']
                elif event['kind'] == 'step':
                    phase = 'diffusion'
                else:
                    phase = 'planning'
                events.append(event)
                print(json.dumps(event), flush=True)
            sample = footprint(process.pid)
            peak = max(peak, sample)
            phase_peaks[phase] = max(phase_peaks.get(phase, 0), round(sample / 1e9, 2))
            free = free_percent()
            minimum = min(minimum, free)
            low = low + 1 if free < 15 else 0
            if low >= 2 or time.monotonic() - last_event > 180 or time.monotonic() - start > 1800:
                events.append({'kind': 'abort', 'free_percent': free, 'reason': 'memory_or_watchdog'})
                break
    finally:
        process.join(3)
        if process.is_alive():
            process.terminate()
            process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)
        parent.close()
    report = {'config': args, 'events': events, 'wall_seconds': time.monotonic() - start,
              'peak_worker_footprint_bytes': peak, 'phase_peak_gb': phase_peaks,
              'minimum_free_percent': minimum, 'exitcode': process.exitcode}
    process.close()
    if events and events[-1]['kind'] == 'done':
        loudness = subprocess.run(['ffmpeg', '-hide_banner', '-i', events[-1]['path'], '-af', 'ebur128=peak=true',
                                   '-f', 'null', '-'], capture_output=True, text=True, check=True)
        report['loudness'] = loudness.stderr.rsplit('Summary:', 1)[-1]
        if args['vocal']:
            from thundertalk.core import audio_io
            from thundertalk.core.asr import AsrEngine
            asr = AsrEngine()
            try:
                asr.load_model(str(BENCH / 'music-models/lyric-asr'), 'Qwen3-ASR', 'onnx')
                asr.set_language('en')
                samples = audio_io.decode_audio(events[-1]['path'], 16000)
                report['read_back'] = ' '.join(asr.recognize(samples[i:i + 15 * 16000], 16000).text
                                             for i in range(0, len(samples), 13 * 16000))
                report['original_lyrics'] = LYRICS
            finally:
                asr.unload()
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'events'}), flush=True)
    return 0 if events and events[-1]['kind'] == 'done' and report['exitcode'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
