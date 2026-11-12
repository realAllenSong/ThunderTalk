"""Training data for fine-tuning ThunderTalk's local ASR models.

    uv run python -m tools.finetune.prepare_data [--limit-fleurs 2000] [--no-punctuate]

Sources (train/validation splits only; the eval suite uses the test splits):
  * ASCEND train  — 9,869 utterances, 8.8 h of Chinese, English and mixed
    Chinese–English conversation, human transcripts in lower case without
    punctuation. Training on that as-is would teach a model to drop
    punctuation and capitals, so the words are kept and only punctuation and
    capitals are restored, by Claude (never a paid API; see --export-texts).
    A line whose words changed is dropped, never "fixed".
  * FLEURS cmn/en train — read speech with punctuated transcripts, used as-is
    so single-language speech does not regress.

Output (~/Library/Caches/ThunderTalk-bench/finetune/prepared): 16 kHz mono
WAVs and train.jsonl / valid.jsonl lines {"audio", "text", "lang", "source",
"seconds"}. Each model's trainer formats ``text`` its own way.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path.home() / "Library" / "Caches" / "ThunderTalk-bench" / "finetune"
DATA, OUT = ROOT / "data", ROOT / "prepared"
PUNCT_CACHE = ROOT / "punctuated.json"


def _audio(cell) -> np.ndarray:
    import soundfile as sf
    from thundertalk.core.audio_io import resample
    x, sr = sf.read(io.BytesIO(cell["bytes"]), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return resample(x, sr, 16000) if sr != 16000 else x


def _rows(name: str, columns: list[str]):
    import pyarrow.parquet as pq
    for f in sorted(DATA.glob(name)):
        pf = pq.ParquetFile(f)
        for batch in pf.iter_batches(batch_size=64, columns=columns):
            yield from batch.to_pylist()


# ── punctuation for ASCEND ───────────────────────────────────────────────
# Done by Claude (subagents), never a paid API: --export-texts writes the lines
# still missing punctuation in chunks, the agents write <chunk>.out.json
# ({"lines": [...]} in order), --import-punct checks every line and caches it.

GUIDE = (
    "Add punctuation and standard capitalization to each speech transcript line. Keep every word "
    "exactly as given, in the same order: do not add, remove, translate or correct words, and keep "
    "fillers (嗯, 呃, um, uh). Chinese text gets full-width punctuation (，。？！) with commas at "
    "natural pauses in longer sentences; English gets ASCII punctuation and capitals (I, names, "
    "sentence starts), and a line that is mostly English ends with ASCII punctuation. Join "
    "spelled-out letters into an acronym (\"i s m\" → \"ISM\"). No space between Chinese "
    "characters; one space between an English word and Chinese text is fine."
)


def _cache() -> dict:
    return json.loads(PUNCT_CACHE.read_text()) if PUNCT_CACHE.exists() else {}


def ascend_texts() -> list[str]:
    texts = [r["transcription"].strip() for pat in ("CAiRE_ASCEND_parquet_main_train_*.parquet",
                                                    "CAiRE_ASCEND_parquet_main_validation_*.parquet")
             for r in _rows(pat, ["transcription", "duration"])
             if (r["transcription"] or "").strip() and "[unk]" not in r["transcription"].lower()
             and 0.6 <= r["duration"] <= 30]
    return list(dict.fromkeys(texts))


def export_texts(folder: Path, size: int = 600) -> None:
    cache = _cache()
    todo = [t for t in ascend_texts() if not cache.get(t)]
    folder.mkdir(parents=True, exist_ok=True)
    for k in range(0, len(todo), size):
        (folder / f"chunk{k // size:03d}.json").write_text(
            json.dumps({"guide": GUIDE, "lines": todo[k:k + size]}, ensure_ascii=False, indent=0))
    print(f"{len(todo)} lines → {folder} ({(len(todo) + size - 1) // size} chunks)", file=sys.stderr)


def _tidy_ending(line: str) -> str:
    """A cut-off fragment keeps no final mark (a trailing comma would teach the
    model to end sentences with one); an English line starts with a capital."""
    line = (line or "").strip().rstrip(",，、").rstrip()
    if re.match(r"[a-z]+(?![A-Za-z0-9])", line):          # "when …" yes, "iPhone" no
        line = line[0].upper() + line[1:]
    return line


def import_punct(folder: Path) -> None:
    from tools.finetune.textcheck import same_words
    cache = _cache()
    ok = bad = 0
    for src in sorted(folder.glob("chunk*[0-9].json")):
        out = src.with_suffix(".out.json")
        if not out.exists():
            continue
        lines = json.loads(src.read_text())["lines"]
        got = json.loads(out.read_text()).get("lines") or []
        for a, b in zip(lines, got if len(got) == len(lines) else [""] * len(lines)):
            b = _tidy_ending(b)
            good = bool(b) and same_words(a, b)
            cache[a] = b if good else ""
            ok, bad = ok + good, bad + (not good)
    PUNCT_CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    print(f"imported: {ok} kept, {bad} rejected (words changed)", file=sys.stderr)


# ── build ────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-fleurs", type=int, default=2000, help="FLEURS utterances per language")
    ap.add_argument("--no-punctuate", action="store_true", help="train on ASCEND text as-is")
    ap.add_argument("--export-texts", help="write lines still needing punctuation to this folder")
    ap.add_argument("--import-punct", help="read the agents' <chunk>.out.json files from this folder")
    a = ap.parse_args()
    if a.export_texts:
        return export_texts(Path(a.export_texts).expanduser())
    if a.import_punct:
        return import_punct(Path(a.import_punct).expanduser())
    wav_dir = OUT / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)
    from thundertalk.core.audio_io import write_wav

    def save(x: np.ndarray, key: str) -> str:
        p = wav_dir / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".wav")
        if not p.exists():
            write_wav(str(p), x, 16000)
        return str(p)

    # ASCEND (train → train.jsonl, validation → valid.jsonl)
    splits = {"train": [], "valid": []}
    ascend = {"train": "CAiRE_ASCEND_parquet_main_train_*.parquet",
              "valid": "CAiRE_ASCEND_parquet_main_validation_*.parquet"}
    raw = {sp: list(_rows(pat, ["id", "audio", "transcription", "language", "duration"]))
           for sp, pat in ascend.items()}
    punct = {k: v for k, v in _cache().items() if v}
    dropped = 0
    for sp, rs in raw.items():
        for r in rs:
            t = (r["transcription"] or "").strip()
            if not t or "[unk]" in t.lower() or r["duration"] < 0.6 or r["duration"] > 30:
                dropped += 1
                continue
            text = punct.get(t) if not a.no_punctuate else t
            if not text:
                dropped += 1
                continue
            splits[sp].append({"audio": save(_audio(r["audio"]), "ascend:" + str(r["id"])), "text": text,
                               "lang": r["language"], "source": "ascend", "seconds": round(r["duration"], 2)})
    print(f"ASCEND: {len(splits['train'])} train, {len(splits['valid'])} valid, {dropped} dropped", file=sys.stderr)

    # FLEURS (train only, capped per language)
    for lang, pat in (("zh", "google_fleurs_parquet_cmn_hans_cn_train_*.parquet"),
                      ("en", "google_fleurs_parquet_en_us_train_*.parquet")):
        n = 0
        for r in _rows(pat, ["id", "audio", "raw_transcription", "num_samples"]):
            if n >= a.limit_fleurs:
                break
            secs = r["num_samples"] / 16000
            if secs > 30:
                continue
            splits["train"].append({"audio": save(_audio(r["audio"]), f"fleurs-{lang}:{r['id']}:{n}"),
                                    "text": r["raw_transcription"].strip(), "lang": lang,
                                    "source": "fleurs", "seconds": round(secs, 2)})
            n += 1
        print(f"FLEURS {lang}: {n}", file=sys.stderr)

    random.Random(0).shuffle(splits["train"])
    for sp, rows in splits.items():
        with open(OUT / f"{sp}.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        hours = sum(r["seconds"] for r in rows) / 3600
        print(f"{sp}.jsonl: {len(rows)} lines, {hours:.1f} h", file=sys.stderr)


if __name__ == "__main__":
    main()
