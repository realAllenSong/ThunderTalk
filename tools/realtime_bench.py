#!/usr/bin/env python3
"""Measure the speech API like a voice agent would use it.

Recognition: replays WAV clips through ``WS /v1/realtime/transcribe`` in real
time (200 ms PCM16 frames, each sent once it has been "spoken", like a
microphone) and reports, per clip:
  first partial   seconds from speech onset to the first partial
  partial gap     longest wait between partials while the segment was open
  final latency   seconds from the end of the silence window to the final,
                  counted from the frame that carries the window's end (the
                  earliest moment the server can know) and, in brackets, from
                  the exact sample. The window end is found offline with the
                  server's own VAD (same model, same frames).
Speech: times ``POST /v1/audio/speech`` to the first audio byte.

Start the server first (it owns the models; this script loads none, except
the 640 KB Silero VAD for the offline window end):
  PYTHONPATH=$PWD python -m thundertalk.api.server --port 8765
Then, e.g.:
  PYTHONPATH=$PWD python tools/realtime_bench.py --manifest \\
      ~/Library/Caches/ThunderTalk-bench/asr-eval/manifest.json --lang en zh --count 10
  PYTHONPATH=$PWD python tools/realtime_bench.py clip.wav --tts-runs 10
  PYTHONPATH=$PWD python tools/realtime_bench.py --manifest … --concurrent 3   # no-crash check
"""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import math
import statistics
import sys
import time
import wave
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

SR = 16_000
FRAME_S = 0.2
LEAD_S, TAIL_S = 0.5, 1.5


def load_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: 16-bit PCM WAV only")
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    if sr != SR:
        t = np.arange(int(len(x) * SR / sr)) * sr / SR
        x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    return x


def window_ends(audio: np.ndarray, silence_ms: int) -> list[int]:
    """Samples at which the server's VAD closes each segment (offline replay)."""
    from thundertalk.api.realtime import SileroStream
    vad = SileroStream(silence_ms)
    n = int(FRAME_S * SR)
    ends = []
    for i in range(0, len(audio), n):
        ends += [e.at for e in vad.accept(audio[i:i + n]) if e.kind == "end"]
    return ends


def speech_starts(audio: np.ndarray, silence_ms: int) -> list[int]:
    from thundertalk.api.realtime import SileroStream
    vad = SileroStream(silence_ms)
    n = int(FRAME_S * SR)
    out = []
    for i in range(0, len(audio), n):
        out += [e.at for e in vad.accept(audio[i:i + n]) if e.kind == "start"]
    return out


async def replay(url: str, audio: np.ndarray, language: str, silence_ms: int) -> dict:
    import websockets
    n = int(FRAME_S * SR)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    msgs: list[tuple[float, dict]] = []
    async with websockets.connect(f"{url}/v1/realtime/transcribe?language={language}&silence_ms={silence_ms}",
                                  max_size=None) as ws:
        ready = json.loads(await ws.recv())
        assert ready["type"] == "ready", ready

        async def receive():
            async for m in ws:
                msgs.append((time.perf_counter(), json.loads(m)))

        rx = asyncio.create_task(receive())
        t0 = time.perf_counter()
        for k in range(math.ceil(len(audio) / n)):
            delay = t0 + (k + 1) * FRAME_S - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            await ws.send(pcm[2 * k * n:2 * (k + 1) * n])
        await asyncio.sleep(1.0)                       # let the last final arrive
        rx.cancel()
    return {"t0": t0, "ready": ready, "msgs": msgs}


def analyse(run: dict, audio: np.ndarray, silence_ms: int) -> dict:
    t0, msgs = run["t0"], run["msgs"]
    finals = [(t, m) for t, m in msgs if m["type"] == "final"]
    partials = [(t, m) for t, m in msgs if m["type"] == "partial"]
    ends = window_ends(audio, silence_ms)
    starts = speech_starts(audio, silence_ms)
    out = {"finals": [m["text"] for _, m in finals], "n_partials": len(partials),
           "decode_ms": [m["decode_ms"] for _, m in finals], "audio_ms": [m["audio_ms"] for _, m in finals]}
    if finals and ends:
        at = ends[0]
        frame_arrival = t0 + math.ceil(at / (FRAME_S * SR)) * FRAME_S
        out["final_latency"] = finals[0][0] - frame_arrival
        out["final_latency_exact"] = finals[0][0] - (t0 + at / SR)
    if partials and starts:
        seg0 = [t for t, m in partials if m["seg"] == finals[0][1]["seg"]] if finals else [t for t, _ in partials]
        onset = t0 + starts[0] / SR
        if seg0:
            out["first_partial"] = seg0[0] - onset
            gaps = [b - a for a, b in zip(seg0, seg0[1:])]
            out["partial_gap"] = max(gaps) if gaps else None
            out["partial_gaps"] = [round(g, 3) for g in gaps]
    return out


def pick_clips(args) -> list[tuple[str, str, str]]:
    """(path, language, reference)."""
    clips = [(p, args.language, "") for p in args.clips]
    if args.manifest:
        man = json.loads(Path(args.manifest).expanduser().read_text())
        root = Path(args.manifest).expanduser().parent
        for lang in args.lang:
            n = 0
            for item in man:
                if item.get("lang") != lang or item.get("group", "clean") != "clean":
                    continue
                path = root / item["path"]
                with wave.open(str(path)) as w:
                    dur = w.getnframes() / w.getframerate()
                if args.min_s <= dur <= args.max_s:
                    clips.append((str(path), lang, item.get("reference", "")))
                    n += 1
                    if n >= args.count:
                        break
    return clips


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else float("nan")


async def asr_bench(args) -> None:
    rows = []
    clips = pick_clips(args)
    if not clips:
        return
    for path, lang, ref in clips:
        audio = load_wav(path)
        padded = np.concatenate([np.zeros(int(LEAD_S * SR), np.float32), audio, np.zeros(int(TAIL_S * SR), np.float32)])
        if args.concurrent > 1:
            runs = await asyncio.gather(*[replay(args.url, padded, lang, args.silence_ms) for _ in range(args.concurrent)])
        else:
            runs = [await replay(args.url, padded, lang, args.silence_ms)]
        for run in runs:
            r = analyse(run, padded, args.silence_ms)
            r.update(clip=Path(path).name, lang=lang, seconds=round(len(audio) / SR, 2), ref=ref)
            rows.append(r)
            lat = r.get("final_latency")
            print(f"{r['clip']:<26} {lang} {r['seconds']:5.2f}s  partials={r['n_partials']:2d} "
                  f"first={r.get('first_partial', float('nan')):.2f}s gap={r.get('partial_gap') or float('nan'):.2f}s "
                  f"final={'%.3f' % lat if lat is not None else '  -  '}s "
                  f"(exact {r.get('final_latency_exact', float('nan')):.3f}) decode={r['decode_ms']} "
                  f"| {' / '.join(r['finals'])[:60]}", flush=True)
    for lang in sorted({r["lang"] for r in rows}):
        sub = [r for r in rows if r["lang"] == lang]
        lat = [r["final_latency"] for r in sub if "final_latency" in r]
        exact = [r["final_latency_exact"] for r in sub if "final_latency_exact" in r]
        gaps = [r["partial_gap"] for r in sub if r.get("partial_gap")]
        first = [r["first_partial"] for r in sub if "first_partial" in r]
        dec = [d for r in sub for d in r["decode_ms"]]
        missing = sum(1 for r in sub if not r["finals"])
        print(f"\n[{lang}] clips={len(sub)} no-final={missing}")
        print(f"  final latency (from frame) median {statistics.median(lat):.3f}s p90 {pct(lat, .9):.3f}s max {max(lat):.3f}s"
              if lat else "  final latency: -")
        print(f"  final latency (exact)      median {statistics.median(exact):.3f}s p90 {pct(exact, .9):.3f}s max {max(exact):.3f}s"
              if exact else "")
        print(f"  decode_ms median {statistics.median(dec):.0f} max {max(dec)}" if dec else "")
        print(f"  first partial median {statistics.median(first):.2f}s; longest partial gap median "
              f"{statistics.median(gaps):.2f}s max {max(gaps):.2f}s" if gaps and first else "  partials: too few")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1))


def tts_bench(args) -> None:
    u = urlparse(args.url.replace("ws://", "http://").replace("wss://", "https://"))
    body = json.dumps({"model": args.tts_model, "input": args.tts_text, "voice": args.tts_voice,
                       "response_format": "pcm", "speed": 1.0})
    ttfb, total, audio_s = [], [], []
    for i in range(args.tts_runs + 1):                 # the first run is a warm-up, not counted
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=60)
        t0 = time.perf_counter()
        conn.request("POST", "/v1/audio/speech", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise SystemExit(f"speech: HTTP {resp.status}: {resp.read()[:300]!r}")
        first = resp.read(1)
        t1 = time.perf_counter()
        rest = resp.read()
        t2 = time.perf_counter()
        sr = int(resp.getheader("X-Sample-Rate", "24000"))
        conn.close()
        if i:
            ttfb.append(t1 - t0)
            total.append(t2 - t0)
            audio_s.append((len(first) + len(rest)) / 2 / sr)
    print(f"\n[tts] {args.tts_model} {args.tts_voice!r} ({len(args.tts_text.split())} words, {audio_s[0]:.2f}s audio)")
    print(f"  time to first byte median {statistics.median(ttfb) * 1000:.0f} ms, min {min(ttfb) * 1000:.0f}, "
          f"max {max(ttfb) * 1000:.0f} (n={len(ttfb)})")
    print(f"  whole response median {statistics.median(total) * 1000:.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="*", help="WAV files")
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--language", default="auto", help="for clips given on the command line")
    ap.add_argument("--silence-ms", type=int, default=350)
    ap.add_argument("--manifest", help="asr-eval manifest.json (clips with lang + reference)")
    ap.add_argument("--lang", nargs="+", default=["en", "zh"])
    ap.add_argument("--count", type=int, default=10, help="clips per language from the manifest")
    ap.add_argument("--min-s", type=float, default=1.0)
    ap.add_argument("--max-s", type=float, default=6.0)
    ap.add_argument("--concurrent", type=int, default=1, help="connections replaying each clip at once")
    ap.add_argument("--json", help="write per-clip results here")
    ap.add_argument("--tts-runs", type=int, default=0, help="time /v1/audio/speech this many times")
    ap.add_argument("--tts-model", default="kokoro")
    ap.add_argument("--tts-voice", default="kokoro:0")
    ap.add_argument("--tts-text", default="The weather today looks sunny, so let's take a long walk in the park.")
    args = ap.parse_args()
    if args.clips or args.manifest:
        asyncio.run(asr_bench(args))
    if args.tts_runs:
        tts_bench(args)
    if not (args.clips or args.manifest or args.tts_runs):
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
