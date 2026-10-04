"""Short real CPU checks; run through lock.py gpu with an isolated HOME.

Reuses the benchmark cache read-only. Uses public sherpa archive examples (a smoke test, not an accuracy benchmark).
"""

import gc
import json
import os
from pathlib import Path
import time

import numpy as np

from thundertalk.core import audio_io, speech, tts, transcribe
from thundertalk.core.asr import AsrEngine
from thundertalk.core.models import BUILTIN_MODELS
from thundertalk.core.tts_backends.zipvoice import ZipVoiceBackend

root = Path(os.environ["BENCH_MODELS"])
out = Path("artifacts/models-v2")
models = {
    "fireredasr2-ctc-int8": "sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25",
    "fireredasr2-aed-int8": "sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26",
    "funasr-nano-int8": "sherpa-onnx-funasr-nano-int8-2025-12-30",
}
dataset = root / "datasets"
fixtures = {
    lang: json.loads((dataset / f"{lang}.json").read_text())[index]
    for lang, index in (("zh", 0), ("en", 0), ("mixed", 1))
}
clips = {
    lang: audio_io.decode_audio(str(dataset / (item["id"] + ".wav")), 16000)
    for lang, item in fixtures.items()
}
zh = clips["zh"]
results = []
for mid, folder in models.items():
    info = next(m for m in BUILTIN_MODELS if m.id == mid)
    eng = AsrEngine()
    eng.set_hotwords(["开放时间", "ThunderTalk"])
    t0 = time.monotonic()
    eng.load_model(str(root / folder), info.family, info.backend, "low")
    load = time.monotonic() - t0
    try:
        for lang, clip in clips.items():
            r = eng.recognize(clip, 16000)
            row = dict(
                model=mid,
                clip=lang,
                source=fixtures[lang]["dataset"],
                fixture=fixtures[lang]["id"],
                reference=fixtures[lang]["reference"],
                text=r.text,
                audio_s=r.duration_secs,
                inference_ms=r.inference_ms,
                rtf=r.rtf,
                native_timestamps=len(r.token_timestamps),
                load_s=load,
            )
            print(json.dumps(row, ensure_ascii=False), flush=True)
            results.append(row)
        # >18 seconds proves the Studio span/chunk path and export times.
        clip = np.concatenate([zh, np.zeros(16000, np.float32)] * 5)
        wav = out / "public-long.wav"
        audio_io.write_wav(str(wav), clip, 16000)
        tr = transcribe.transcribe_file(str(wav), eng)
        row = dict(
            model=mid,
            clip="studio-long",
            audio_s=len(clip) / 16000,
            segments=[dict(start=s.start, end=s.end, text=s.text) for s in tr.segments],
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
        results.append(row)
    finally:
        eng.unload()
        gc.collect()
b = ZipVoiceBackend(
    root / "sherpa-onnx-zipvoice-distill-int8-zh-en-emilia",
    root / "vocos_24khz.onnx",
    4,
)
speech._BACKENDS["zipvoice"] = b
try:
    for lang, text, slug in [
        ("chinese", "你好，这是本地语音测试。", "warm-female-zh"),
        ("english", "Hello. This is a local speech test.", "male-en"),
        ("chinese", "你好，please bring the report。", "warm-female-zh"),
    ]:
        prompt_audio, prompt_text = b.reference(f"zipvoice:{slug}")
        t0 = time.monotonic()
        r = speech.get_engine().synthesize(
            text,
            tts.ClonePrompt(prompt_audio, prompt_text, "synthetic preset"),
            language=lang,
            clone_backend="zipvoice",
            params=tts.TtsParams(max_attempts=1),
            seed=7,
        )
        name = "mixed" if "please" in text else lang
        audio_io.write_wav(str(out / f"zipvoice-{name}.wav"), r.audio, r.sample_rate)
        row = dict(
            model="zipvoice",
            clip=name,
            text=text,
            audio_s=r.duration,
            seconds_taken=time.monotonic() - t0,
            rms=float(np.sqrt(np.mean(r.audio**2))),
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
        results.append(row)
    # Each preview is rendered by ZipVoice from app-owned synthetic references.
    import soundfile as sf
    from thundertalk.core.tts_backends.previews import PREVIEW_TEXT, preview_path

    for voice in b.voices():
        r = speech.get_engine().synthesize(
            PREVIEW_TEXT[voice.language],
            voice.id,
            language=voice.language,
            seed=7,
            params=tts.TtsParams(max_attempts=1),
        )
        dest = preview_path(voice.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dest), r.audio, r.sample_rate, format="FLAC", subtype="PCM_16")
        print(
            json.dumps(dict(preview=voice.id, audio_s=r.duration), ensure_ascii=False),
            flush=True,
        )
finally:
    speech.get_engine().unload()
(out / "real-results.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2)
)
