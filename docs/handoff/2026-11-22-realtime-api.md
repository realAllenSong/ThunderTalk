# Handoff: realtime speech API (streaming ASR + streaming TTS), 2026-11-22

Branch `feat/realtime-api` (worktree `~/Desktop/tt-realtime`), based on origin/main 04d42d6.
For the ThunderTalk agent who tests, runs the evals and merges. Nothing was merged, tagged,
released or version-bumped.

## Purpose

Open-Jarvis (a separate full-duplex voice agent, `~/Desktop/Open-Jarvis`) uses ThunderTalk as
its free local ASR and TTS **over HTTP/WebSocket only** (it must not import ThunderTalk code:
licence separation). It needs (1) streaming recognition that closes a segment after a short
pause (~350 ms) and sends the words so far of the open segment, and (2) fast per-sentence
synthesis. The other side is written against the contract below in parallel, so the
contract is fixed.

## Contract (as implemented)

`python -m thundertalk.api.server --port 8765 [--asr <model id>] [--tts kokoro]`, or
`thundertalk serve …` (CLI v3 subcommand; packaged: `ThunderTalk --cli serve …`).
Default host 127.0.0.1, no authentication.

1. `WS /v1/realtime/transcribe?language=en|zh|auto&silence_ms=350&model=<id>`
   - On accept: `{"type":"ready","model":"<id>","sample_rate":16000}`.
   - Client → binary PCM16 LE mono 16 kHz frames of any length (odd byte counts are carried over).
   - Client → `{"type":"commit"}`: close the open segment now (with none open, the audio since
     the last segment is decoded if the Silero speech gate hears speech in it).
     `{"type":"reset"}`: drop the open segment and buffered audio (no final for it); finals of
     segments that had already closed are still delivered.
   - Server → `{"type":"partial","text":…,"seg":n}` about every 0.4 s of audio while speech
     goes on, only when the text changed; at most one partial decode per connection is queued
     or running (a tick that comes while one is busy is skipped).
   - Server → `{"type":"final","text":…,"seg":n,"audio_ms":…,"decode_ms":…}` when Silero VAD
     hears `silence_ms` of silence after speech. Segments are numbered from 0. A segment whose
     text is empty or punctuation only sends no final (so seg numbers can skip).
   - `audio_ms` = length of the decoded audio (VAD start − 0.1 s pre-roll → end of speech + up
     to 0.25 s); `decode_ms` = recognition compute time of the final (not queue wait).
   - Extension (not in the original contract, additive): `{"type":"error","message":…}` for an
     unknown language/model (then the socket closes, 1008/1011), a non-JSON/unknown text message,
     or a failed final decode.
   - `silence_ms` is clamped to 100…5000. Languages: auto, en, zh (+ ja ko yue es fr de ar hi it pt ru nl tr).
2. `POST /v1/audio/speech` `{"model":"kokoro"|"voxcpm2"|"indextts"|"longcat"|"tts-1"…, "input":…,
   "voice":"kokoro:0", "response_format":"pcm"|"wav", "speed":1.0}`
   - Chunked response, one chunk per sentence piece as soon as it is synthesized. `pcm` = raw
     PCM16 mono at the engine rate, header `X-Sample-Rate` (Kokoro 24000); `wav` = a WAV header
     with sizes 0xFFFFFFFF followed by the PCM stream (also has `X-Sample-Rate`). Header `X-Voice`
     names the voice used.
   - `response_format` defaults to `wav`; anything else than pcm/wav → 400. `speed` clamped 0.5…2.
     OpenAI model names (`tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`) and voice names (`alloy`…)
     map to the default engine and its default voice for the text's language (Kokoro: `kokoro:0`
     English, `kokoro:3` Chinese). `"voice":"5"` = `kokoro:5`; all 103 Kokoro speakers are accepted.
   - Errors before the first audio byte are real HTTP errors (400 bad request, 503 engine not
     downloaded, 500 synthesis failed). An error after audio started ends the stream early.
   - The first sentence is cut once more at its first clause mark (or, in English with no comma,
     before a connective such as "and"/"to"/"for" after 4–6 words) when it is longer than 7 words /
     12 Chinese characters: time to first byte = time to synthesize the first piece.
3. `GET /v1/audio/voices` → `{"engines":[{"id","name","sample_rate","default","gpu"}],
   "voices":[{"id","name","engine","language","gender","description"}]}` for the downloaded local engines.
4. `POST /v1/audio/transcriptions` and `GET /health` keep their request/response shapes.
   Behaviour changes (improvements): with no `--asr`, transcriptions use the default (best)
   model instead of answering 503; ffmpeg and the decode run off the event loop; `language`
   applies to that request only (default auto) instead of sticking to the engine.

## How it works

- `thundertalk/api/realtime.py`
  - `DecodeScheduler`: **one worker thread** runs every model call (loads, finals, partials,
    file transcriptions) from every connection, priority 0 (finals, loads) before priority 1
    (partials). So the event loop never blocks, decodes from many connections are serialized,
    and MLX is only touched from one thread (AsrEngine still takes GPU_LOCK / serialized_mlx inside).
  - `SileroStream`: sherpa-onnx Silero VAD (same model file, threshold 0.5 and 0.25 s minimum
    speech as `core/speech_gate.py`) fed frame by frame, `min_silence_duration = silence_ms`.
    A second quick Silero detector (0.1 s hysteresis) answers "is the speaker talking now".
  - `RealtimeSession` (no asyncio; `post`/`send` callbacks are injected, which is what the tests use):
    - partials: every 0.4 s of audio while talking, ≥ 0.5 s audio, skipped while one is in flight,
      dropped if the segment closed meanwhile;
    - long segments: past 6 s the older part is committed at a pause (`live_preview.find_commit_point`)
      with a full-quality decode, so partials and the final only decode the rest; a segment is
      force-closed at 60 s;
    - **early final**: no partial starts in the silence after speech; instead, when the quick
      detector hears the speaker stop (~0.1 s), the segment so far is decoded at full quality.
      If the silence lasts the whole window, that result *is* the final, sent the moment the
      window closes; if speech resumes it is sent as a partial and discarded as a final. This
      is what brings final latency from ~0.22 s to ~0.004 s (numbers below).
- `thundertalk/api/speech_stream.py`: `TtsHost` (engine registry, voice resolution, voices
  listing, streaming generator), `plan_pieces` (Speak page's `tts.plan_sentences` + first-clause
  cut), PCM16/WAV helpers. Kokoro gets its own `KokoroBackend(num_threads=8)` (measured 4 → 8
  threads: 0.77 → 0.50 s for a 13-word sentence). GPU engines load through
  `speech.get_engine().load_backend` (one GPU TTS model at a time) and generate under
  `GPU_LOCK` + `mlx_context()`. One synthesis at a time. No read-back / retakes / loudness
  matching on this path (they multiply time to first audio).
- `thundertalk/api/server.py`: FastAPI app; `AsrHost` (engines per model id, set up like the CLI:
  settings hotwords, builtin terms, auto languages, memory mode; `transcribe._has_model_headroom`
  check; 1 s warm-up decode), `best_model()` = `cli.resolve_asr("best")` restricted to MLX/ONNX
  backends (Apple Speech needs the main run loop, cloud never). `serve()` sets HF offline,
  warms up ASR + `--tts`, runs uvicorn.
- `thundertalk/cli.py`: `serve` subcommand (`--host --port --asr --tts --no-warm-up`), emits a
  `stage` event with the URL; `status.capabilities` gains `serve`.

## Files changed

| File | Change |
| --- | --- |
| `thundertalk/api/realtime.py` | new: scheduler, streaming VAD, session logic |
| `thundertalk/api/speech_stream.py` | new: streaming TTS host, piece planning, WAV/PCM |
| `thundertalk/api/server.py` | WS + speech + voices endpoints, shared engines, `serve()`, `--asr` (`--model` kept as alias), `--tts`, `--no-warm-up` |
| `thundertalk/cli.py` | `serve` subcommand, capability |
| `tools/realtime_bench.py` | new: real-time WS replay + TTS TTFB bench |
| `tests/test_realtime_api.py`, `tests/test_speech_api.py` | new (45 tests, no model loaded) |
| `pyproject.toml`, `uv.lock` | `websockets>=13` added to the `api` extra |
| `ThunderTalk.spec` | bundles `thundertalk.api.*` + fastapi/uvicorn/websockets/starlette when the build venv has them |
| `docs/cli-v3.md` | `serve` row + "Speech API" section |

## How to run (from source)

```sh
cd ~/Desktop/tt-realtime
PY=/Users/songallen/Desktop/ThunderTalk/.venv/bin/python
# websockets was added to the shared venv (additive) with: uv pip install --python $PY "websockets>=13"
PYTHONPATH=$PWD HF_HUB_OFFLINE=1 python3 ~/Library/Caches/ThunderTalk-bench/lock.py gpu -- \
  $PY -m thundertalk.api.server --port 8765            # or: $PY -m thundertalk --cli serve --port 8765
# ready when it prints "ThunderTalk speech API listening on http://127.0.0.1:8765"
curl -s localhost:8765/health
PYTHONPATH=$PWD $PY tools/realtime_bench.py --manifest ~/Library/Caches/ThunderTalk-bench/asr-eval/manifest.json --lang en zh --count 15
PYTHONPATH=$PWD $PY tools/realtime_bench.py --tts-runs 10 --tts-text "I checked your calendar and there is nothing booked for Friday afternoon at all."
```

Start-up on this Mac: Qwen3-ASR 0.6B tuned MLX load + 1 s warm-up decode (722–745 ms) + Kokoro
load/warm-up; listening after ~5 s. Default model resolved here: `qwen3-asr-06b-tuned-mlx`
(first downloaded entry of `cli.ASR_QUALITY`).

## Tests (run per file; a single full pytest process gets killed by memory pressure)

```sh
for f in tests/test_realtime_api.py tests/test_speech_api.py tests/test_api_server.py tests/test_cli_v3.py \
         tests/test_cli.py tests/test_cli_mcp.py tests/test_cli_coordination.py tests/test_live_preview.py; do
  PYTHONPATH=$PWD $PY -m pytest -q $f -p no:cacheprovider; done
```

Results 2026-11-22: test_realtime_api 27 passed · test_speech_api 18 passed · test_api_server 3 ·
test_cli_v3 88 · test_cli 14 passed 2 skipped · test_cli_mcp 18 · test_cli_coordination 8 ·
test_live_preview 41. `ruff check` clean on all changed Python files. The rest of the suite was
not run (nothing else imports the changed modules except `cli.py`, covered above).

The new tests use a level-based fake VAD, a synchronous fake scheduler and a fake recognizer
(segmenting, cadence, in-flight skip, late partial dropped, early final reuse/invalidation,
commit/reset, max length, odd PCM bytes, buffer bound, decode errors), the real 640 KB Silero
model on a bundled voice clip, the WebSocket end to end through FastAPI's TestClient, and the
speech/voices endpoints with a fake backend (pcm/wav, errors before first byte, voice resolution).

## Measured latencies (M3 Max, 36 GB; other agent sessions running: load average 6–14, ~40–50 % memory free)

Recognition, `tools/realtime_bench.py`, 15 English + 15 Chinese clean ASCEND clips of 1.5–5.4 s
from the asr-eval set, each replayed alone in real time (200 ms frames, each sent when "spoken";
0.5 s silence before, 1.5 s after), `silence_ms=350`, model qwen3-asr-06b-tuned-mlx.
"From frame" = from the arrival of the frame that contains the end of the silence window (the
earliest the server can know); "exact" adds the up-to-200 ms of client framing.

| | en (n=15) | zh (n=15) |
| --- | --- | --- |
| final latency from frame: median / p90 / max | **0.004 / 0.004 / 0.004 s** | **0.003 / 0.024 / 0.317 s** |
| final latency exact: median / p90 / max | 0.076 / 0.156 / 0.188 s | 0.080 / 0.197 / 0.365 s |
| decode_ms median / max | 134 / 197 | 128 / 285 |
| first partial after VAD onset, median | 0.34 s | 0.30 s |
| finals missing | 0 | 0 |

- 29 of 30 finals came ≤ 25 ms after the closing frame; the one at 0.317 s (ascend-zh-010) fell
  back to a normal final decode (285 ms): its early final was not usable (not investigated; most
  likely the quick detector heard sound inside the pause, which discards the early final).
- Same code without the early final (decode at window close): median 0.236 s (en) / 0.219 s (zh),
  max 0.315 / 0.346 s, 3 of 30 over 300 ms. An earlier run on a quieter GPU: median 0.158 / 0.147 s,
  max 0.215 / 0.289 s.
- Accuracy of the early final vs a final decoded at window close, same 30 clips: identical error
  counts (en 19/108 words, zh 14/193 characters). Small sample; see "what to check".
- Partials: 135 gaps between consecutive partials, median 0.41 s, 92 % ≤ 0.6 s. Longer gaps
  (max 1.42 s) are where the text did not change or the speaker hesitated (no partial is sent
  for unchanged text, and none starts while the quick detector hears silence).
- 3 connections replaying the same clip at once (3 × 6 clips): no errors, all finals; finals
  serialize on the one worker, so from-frame median 0.07 s (en) / 0.005 s (zh), max 0.40 / 0.45 s.
- 6 ESC-50 non-speech clips (5 s each): no finals; one sent a single partial.
- 94.5 s English FLEURS recording replayed in one connection: 15 finals, 133 partials, no errors.

Speech, `POST /v1/audio/speech` `response_format=pcm`, Kokoro (8 threads), 10 timed requests after
1 warm-up, time to first audio byte:

| text (first piece) | median | min / max | whole response |
| --- | --- | --- | --- |
| "I checked your calendar and there is nothing booked for Friday afternoon at all." (14 words; first piece "I checked your calendar") | **257 ms** | 242 / 294 | 677 ms |
| "The weather today looks sunny, so let's take a long walk in the park." (14 words; first piece "The weather today looks sunny,") | 308 ms (315 ms in an earlier run) | 269 / 402 | 643 ms |
| "Sure, I can do that." (5 words, one piece) | 309 ms (275 ms earlier) | 247 / 842 | 309 ms |
| "今天天气不错，我们一起去公园散步吧，顺便买点水果。" (first piece "今天天气不错，") | 327 ms | 282 / 440 | 1110 ms |

Kokoro itself is the floor: `generate("Hello.")` takes 209 ms with 8 threads under this load
(222 ms with the Chinese text-normalisation FSTs, 206 ms without; the CoreML provider was slower).
Server overhead on top of the raw generate is within the run-to-run noise (raw 319–338 ms vs 308–315 ms TTFB for the same clause).

## Known limitations / risks

1. **Kokoro TTFB target (≤ 300 ms) is met only for first pieces of ≤ ~4 words on this loaded
   machine** (median 257 ms); a 5-word first clause is 300–330 ms and Chinese ~330 ms. Further
   options, not done: MLX Kokoro (mlx-audio 0.4.5 has `tts/models/kokoro`, but needs the `misaki`
   phonemizer and a weights download); a more aggressive first cut (any word boundary after 3–4
   words, audible prosody break); the client sending clause-sized text.
2. One decode worker for everything: N simultaneous speakers serialize their finals; early
   finals use the final priority, so one connection's early final can delay another's final by
   one decode (~0.1–0.3 s). Fine for one voice agent; not a multi-tenant server.
3. The server is a separate process: its GPU_LOCK does not coordinate with the running app's
   dictation or Studio jobs, and it does not yield to dictation (`priority.DICTATION`), because
   both are interactive. Concurrent use shares the GPU (measured elsewhere: dictation 0.28 → 0.31 s
   median when another process decodes).
4. `model=<other id>` loads a second engine on first use and keeps it until the server exits
   (memory headroom checked with the CLI's rule: ≥ 40 % free). Unknown/not downloaded → error + close.
5. A noise burst can open a VAD segment and produce a partial with no final.
6. No authentication. Keep 127.0.0.1 unless the network is trusted (a warning is printed).
7. Packaged app: `ThunderTalk --cli serve` only works if the release venv has the `api` extra
   (`uv sync --extra mlx --extra translation --extra indextts --extra api`); the spec bundles the
   API only when fastapi/uvicorn/websockets are importable. **Not built or tested packaged.**
   Without them, `serve` returns `code: missing_dependency`.
8. `websockets` 17.4 was installed into the shared `~/Desktop/ThunderTalk/.venv` (additive) so
   uvicorn can serve WebSockets.
9. TTS path: no read-back check, retakes or loudness normalisation (Studio has them). GPU engines
   (VoxCPM2, IndexTTS, LongCat) work through the same code but take seconds per sentence and were
   not exercised against real models here (voices listing only).

## What the reviewing agent should check

- Run the test files above and `ruff check`.
- Start the server from source and run `tools/realtime_bench.py` on en + zh (and `--concurrent 3`)
  plus a `--tts-runs 10` speech run; compare with the tables (the machine's load matters, note it).
- Listen to one `response_format=wav` result where the first sentence was cut (e.g. the "weather"
  sentence) and judge whether the cut is audible.
- Early-final accuracy on more data: replay a larger slice (e.g. `--count 60` per language, and the
  `noisy`/`mixed` groups by passing paths) and compare `finals` with the references; the 30-clip
  comparison showed no difference.
- `thundertalk eval`: not required — no ASR model, decoding or text post-processing code changed
  (the server calls `AsrEngine.recognize` like the CLI). Run it only if you want a baseline check.
- Packaging (if this should ship in the app): build with the `api` extra, then
  `ThunderTalk --cli serve --port 8765` + the bench against the packaged binary.
- Decide whether `serve` should be mentioned in README / llms.txt and the Rosetta `thundertalk` skill.

## Merge criteria

- All listed test files pass; ruff clean.
- Server starts from source, `/health` names the model, the bench shows finals for every clip
  and final latency from frame ≤ 300 ms for ≥ 90 % of 1–6 s clips, partial gaps ≤ 0.6 s for most.
- No change to `/v1/audio/transcriptions` and `/health` response shapes.
- Owner (realAllenSong) agrees to the dependency (`websockets` in the `api` extra) and to the
  TTFB result above (limitation 1). Merge only; release/version bump is a separate decision.
