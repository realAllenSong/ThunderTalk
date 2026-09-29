# TTS you can rely on, and the Lab becomes Studio (v1.5.0)

## Why

Two reports against v1.4.0:

1. Generated speech "jumps up and down": the pitch climbs and drops for no
   reason, so it was not usable as a product feature.
2. The **Lab** was still an experiment. The wish: drop in a recording and get a
   transcript fast; drop in a multi-person recording and get speakers;
   record a sentence and clone that voice; and even without cloning, offer
   built-in voices that sound like a normal person reading normally.

The requirement was that "normal" be *verified*, not assumed.

## What was wrong (root causes, all confirmed)

| # | Cause | Effect |
|---|-------|--------|
| 1 | TTS ran through an external `mlx-tts serve` sidecar that was never bundled | In the shipped app the feature could not work at all |
| 2 | Whole text sent as one autoregressive pass | Long passages drift, skip or repeat text |
| 3 | No length cap | A piece could run to 4096 tokens (a 17 s sentence produced 327 s of babble) |
| 4 | Sampling: temperature 0.9, top_p 1.0, repetition penalty 1.2 | Extra pitch wander |
| 5 | Default model 0.6B 4-bit | Intrinsically the least stable of the family |
| 6 | Speed control had no effect (the model ignores `speed`) | Dead control |
| 7 | Playback used scipy, file decoding needed ffmpeg | Neither ships in the app; Play and Transcribe failed silently |

## How "normal" was measured

Pitch is compared in semitones against the utterance's own median, so voices of
any register are comparable. Praat F0 (parselmouth), 20-second windows, plus the
swing between successive 5-second block medians.

**Natural read speech was measured first, as the yardstick:**

| Source | range20 (st) | sd20 | drift20 | 5-s swing avg / max |
|--------|-------------:|-----:|--------:|--------------------:|
| LibriSpeech, English (481 s) | 10.2 | 3.2 | 1.5 | 1.1 / 4.2 |
| THCHS-30, Chinese, 3 speakers | 7.6–12.5 | 3.0–4.2 | 1.2–2.0 | 0.9–1.4 / 2.0–3.8 |
| A user recording (13 s) | 8.7 | 4.2 | 1.7 | 1.8 / 2.3 |

(An early "human baseline" turned out to be a song, not speech, and was
discarded; every number here uses actual read speech.)

Intelligibility is checked by **ASR round trip**: the generated audio is
recognised by a *different, stronger* model than the one that guards
generation, and scored as CER (Chinese) / WER (English) against the source
text. English WER has a floor of about 3.8 % from spelling variants alone.

## Findings

**1. The model is the biggest lever.** 0.6B-4bit, the previous default, on a
70 s passage: 5-second swing 2.3 (zh) and 4.6 (en) against natural 0.9–1.4, with
jumps of more than 5 semitones between adjacent 10 ms frames in 0.5 % of frames (natural: ≈ 0). The 1.7B model on the same
text stays inside the natural range.

**2. Chunking: two or three sentences per piece beats both extremes.**
1.7B bf16, three seeds each, temperature 0.5:

| Strategy | zh sd20 | zh swing | en sd20 | en swing | Content errors |
|----------|--------:|---------:|--------:|---------:|----------------|
| Whole passage in one pass | 4.0 | 1.4 | 3.2 | 0.9 | **2 of 6 runs wrong** (zh CER 23 %, en WER 43 % after a runaway) |
| ~2–3 sentences per piece (**chosen**) | 3.9 | 1.6 | 3.5 | 1.0 | none |
| One sentence per piece | 4.5 | 2.0 | 4.2 | 2.0 | none |

Sentence-by-sentence generation, my first design, was measurably *worse*: every
sentence restarts its own register, which is exactly what "jumping" sounds like.
Whole-passage generation is smooth but unreliable. Pieces of a few sentences keep
the voice coherent without giving the model room to lose its place.

**3. Quantisation.** 8-bit is indistinguishable from bf16 in stability
(zh sd20 3.91 vs 3.90, en 3.74 vs 3.51) while being 1.4–2.3× faster and
3.1 GB instead of 4.2 GB. 4-bit is not usable (pieces of implausible length,
up to 186 s for a 60 s passage).

**4. Two different failure modes, one guard each.**

- *Runaway / early stop*: caught cheaply by the expected-duration band
  (accepted 0.72–1.9× of the estimate; every healthy take measured was ≥ 0.89×,
  the truncated ones about 0.6×).
- *Right length, wrong words* (a clause skipped or garbled): only caught by
  reading the audio back. Each piece is re-recognised with the user's own
  dictation model and redone if the error exceeds 12 %.

**5. Retrying must change the context, not just the dice.** Cooling the
temperature on each retry made the model repeat its favourite failure three
times in a row (one 8-bit sample). Retries now keep the temperature and change
the seed; if a piece still fails, it is redone sentence by sentence, a genuinely
different context.

**6. Voice cloning fidelity** (user's own 13 s recording as reference,
1.7B Base bf16), embedding cosine from Qwen3-TTS's own speaker encoder, median
F0 difference, long-term spectrum correlation:

| Pair | embedding cos | ΔF0 (semitones) | spectrum r |
|------|--------------:|----------------:|-----------:|
| Reference half A vs half B (same speaker) | 0.986 | −2.8 | 0.70 |
| Clone (4 generations, zh and en) | 0.971–0.988 | −1.2…+0.2 | 0.67–0.85 |
| Other real speakers (3) | 0.894–0.926 | 5.6–17.4 | 0.53–0.63 |

The encoder is the model's own, so the cosine alone would be circular; the F0
and spectrum columns are independent and agree. This is an objective proxy, not
a listening test.

## What shipped

- `core/tts.py`: piece planning (`chunk_scale`), duration band, retry and
  sentence fallback, WSOLA pitch-preserving speed (the model's own `speed` is
  ignored), pause-aware assembly, level matching, peak normalisation.
- `core/tts_verify.py`: ASR read-back; switches itself off if the recogniser
  cannot read the language (two confident disagreements in a row).
- `core/voices.py`: saved voices under `~/.thundertalk/voices`; reference
  preparation (24 kHz, trim, ≤ 20 s cut at a pause, warnings for clipping /
  too quiet / too short).
- `core/audio_io.py`, `core/playback.py`: decode and export with `afconvert`
  (no ffmpeg), FFT resampler, numpy player with pause and seek (no scipy).
- `core/transcribe.py`: adaptive pause-based segmentation, fast path with the
  active dictation model, speaker path with MOSS, TXT/MD/SRT/VTT/JSON export.
- `core/gpu_lock.py`: one lock so dictation, file jobs and TTS never share the
  Metal queue concurrently.
- `ui/studio/*`, `ui/pages/studio_page.py`: Transcribe and Speak tabs, clone
  dialog (record or import, auto-transcribed reference to confirm), voice
  library, seekable player. The Lab page, its sidecar and its strings are gone.
- `thundertalk --selftest audio|tts|asr|clone`: runs inside the packaged app.

Bugs found by the new tests along the way: speech detection failed on
recordings that are almost all speech (threshold was relative to the 10th
percentile), a segment could exceed its 30 s limit when the next pause was far
away, M4A export failed at 24 kHz (AAC rejects ≥ 96 kbps), `p.m.` was treated as
a sentence end, and closing the app during a job could crash a running QThread.

## Verification

All numbers below are from the shipping code and models (8-bit voice models).

**Speech generation, end to end.** 16 generations (Chinese and English, a
~70 s passage and a ~23 s message, 4 seeds each), with the in-app read-back
guard using Qwen3-ASR 0.6B, scored independently by Qwen3-ASR 1.7B:

| | worst read-back error | flagged pieces |
|---|---:|---:|
| English, long (4) | 1.7 % | 0 |
| Chinese, long (4) | 0.0 % | 0 |
| English, short (4) | 0.0 % | 1 (false alarm: content was correct) |
| Chinese, short (4) | 1.1 % | 0 |

English errors of ~1 % are spelling variants in the recogniser, not speech
errors. Two failures seen during development — a skipped clause (7.1 %) and a
lost final sentence (44 % → 11 % → caught) — are why retries now change the seed
instead of cooling the temperature, and why a missing ending counts as a failure
even when the overall error is small; both seeds now read back at 0 %.

**Cloning** (user's own 13 s recording): see finding 6. Read-back error of the
cloned speech: 0.0 % in all four generations.

**File transcription** against reference transcripts (LibriSpeech, 73
utterances, 8.6 min; THCHS-30, 36 utterances, 5.5 min):

| Path | English WER | Chinese CER | Speed |
|------|------------:|------------:|------:|
| One speaker (Qwen3-ASR 0.6B, per segment) | 4.7 % | 5.0 % | 11× real time (6–7× in 1.5.0) |
| Multiple speakers (MOSS-Transcribe-Diarize) | 3.6 % | 3.5 % | 15–21× real time |

Two bugs surfaced here. MOSS stopped after ~5.5 minutes of English without
any error (the library's 2048-token default; English WER was 36 % before the
fix). The recogniser ran with MLX's buffer cache disabled, making every
recognition — including everyday dictation — about 3× slower (18 s of audio:
6.4 s → 2.0 s). 1.5.0 shipped a 512 MB limit; a sweep in 1.5.1 (0 → 4×,
512 MB → 6.5×, 1 GB → 7×, 2 GB → 11×, 4 GB → 12×, peak memory 4.4 GB at every
setting) moved it to 2 GB.

**Other dictation models, same data, M3 Max** (added in 1.5.1 to replace
copy that had never been measured): Qwen3-ASR 0.6B ONNX int8 — WER 5.5 %,
CER 4.5 %, 12.8× real time; Parakeet-TDT v2 — WER 3.0 %, 53×; Parakeet-TDT v3
— WER 4.5 %, 53×. The old claims ("Parakeet RTF 0.035, 8× faster than Qwen
ONNX", "Qwen ONNX RTF 0.3", "MLX fastest on Apple Silicon") were wrong and have
been replaced with these.

**Speakers.** A conversation assembled from three real THCHS-30 speakers
(12 turns, 177 s): MOSS found 3 speakers and attributed 12 of 12 turns
correctly. On the 36-utterance set it merged two of the three speakers, so
speaker counts are good, not perfect.

**Packaged app.** `ThunderTalk.app --selftest audio|tts|asr|clone` all pass
with real models (TTS: 4.8 s of speech in 11.9 s including model load; clone
read-back error 0.0 %).

**Tests.** 344 automated tests, including the Studio UI with real widgets and
worker threads, the playback engine, audio I/O, segmentation, exports, the
voice library, the TTS pipeline with a fake model (retry, fallback, cancel,
speed) and every Studio string in both languages.

## Known limits

- Apple Silicon only for Studio's speech features (MLX).
- The speaker model and both voice models are one-time multi-GB downloads.
- Clones follow the reference: a noisy or emotional recording gives a noisy or
  emotional clone. The dialog warns about clipping, low level and length.
- The read-back check depends on the user's dictation model being able to read
  the language; if it cannot, it stops checking and only the duration guard
  remains.
- Not verified: listening tests by other people; other voices than the ones
  named above; languages other than Chinese and English for stability.
