"""Build the ASR eval v2 corpus: real speech, real noise, quiet, long-form, hotwords.

Sources (test splits only, all open licences):
  FLEURS (CC BY 4.0)  cmn_hans_cn and en_us read speech, many speakers/mics
  ASCEND (CC BY-SA 4.0) spontaneous Hong Kong conversation: zh, en, zh-en mixed
  ESC-50 (CC BY-NC 3.0) real environmental noise, eval only, never shipped
Everything is derived deterministically (seed 2026) and written under
~/Library/Caches/ThunderTalk-bench/asr-eval/, with a frozen manifest.json.

Run with an interpreter that has pyarrow + wordfreq (venv-sherpa2):
  python -m tools.asr_eval.corpus fetch | derive
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path.home() / "Library/Caches/ThunderTalk-bench/asr-eval"
ESC = Path.home() / "Library/Caches/ThunderTalk-bench/esc50"
SR = 16000
RNG = np.random.default_rng(2026)
SHARDS = {
    "fleurs-zh": ("https://huggingface.co/datasets/google/fleurs/resolve/168de341b3db6859a9bac1c50a2ef5e3b47647e0/cmn_hans_cn/test/0000.parquet", 695674033),
    "fleurs-en": ("https://huggingface.co/datasets/google/fleurs/resolve/168de341b3db6859a9bac1c50a2ef5e3b47647e0/en_us/test/0000.parquet", 401722686),
    "ascend": ("https://huggingface.co/datasets/CAiRE/ASCEND/resolve/f2451e0d2f6e74ac864a9deb78c1a8ef3140176c/main/test/0000.parquet", 105756434),
}
WANT = {"fleurs-zh": {"zh": 150}, "fleurs-en": {"en": 100}, "ascend": {"zh": 60, "en": 40, "mixed": 150}}
DESK_NOISE = ["keyboard_typing", "vacuum_cleaner", "rain", "washing_machine", "footsteps",
              "mouse_click", "clock_tick", "pouring_water", "wind", "door_wood_knock"]


def save(rel: str, x: np.ndarray) -> str:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.clip(x, -1, 1).astype(np.float32), SR, subtype="PCM_16")
    return rel


def resample(x: np.ndarray, sr: int) -> np.ndarray:
    if x.ndim == 2:
        x = x.mean(axis=1)
    if sr == SR:
        return x.astype(np.float32)
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(SR, sr)
    return resample_poly(x, SR // g, sr // g).astype(np.float32)


def fetch() -> None:
    import pyarrow.parquet as pq
    rows: list[dict] = []
    only = sys.argv[2:]
    for name, (url, size) in SHARDS.items():
        if only and name not in only:
            continue
        want = dict(WANT[name])
        shard = ROOT / f"{name}.parquet"
        if not shard.exists() or shard.stat().st_size != size:
            print("download", name, size // 1_000_000, "MB", flush=True)
            with urllib.request.urlopen(url + "?download=true", timeout=120) as src, shard.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)
        assert shard.stat().st_size == size, name
        pf = pq.ParquetFile(shard, memory_map=True, pre_buffer=False, buffer_size=1 << 16)
        seen_text: set[str] = set()
        counts: Counter = Counter()
        all_refs: list[dict] = []
        for i, batch in enumerate(pf.iter_batches(batch_size=1, use_threads=False)):
            for row in batch.to_pylist():
                lang = row.get("language") if name == "ascend" else ("zh" if name == "fleurs-zh" else "en")
                ref = (row.get("transcription") or "").strip()
                if name == "ascend":
                    all_refs.append(dict(id=row["id"], lang=lang, ref=ref, session=row.get("session_id")))
                if not ref or ref in seen_text or lang not in want or counts[lang] >= want[lang]:
                    continue
                # spread over the shard (speakers), keep 1.5-15 s utterances
                if name != "ascend" and i % 2:
                    continue
                x, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
                x = resample(x, sr)
                if not 1.5 <= len(x) / SR <= 15:
                    continue
                seen_text.add(ref)
                cid = f"{name}-{lang}-{counts[lang]:03}"
                counts[lang] += 1
                rows.append(dict(id=cid, group="clean", lang=lang, dataset=name, reference=ref,
                                 source_id=str(row.get("id")), speaker=str(row.get("original_speaker_id", row.get("gender", ""))),
                                 session=row.get("session_id"), path=save(f"clean/{cid}.wav", x)))
        if name == "ascend":
            (ROOT / "ascend-refs.json").write_text(json.dumps(all_refs, ensure_ascii=False))
        print(name, dict(counts), flush=True)
        if all(counts[k] >= v for k, v in want.items()):
            shard.unlink()                       # keep only what we use
    prev = json.loads((ROOT / "clean.json").read_text()) if (ROOT / "clean.json").exists() else []
    keep = [r for r in prev if r["dataset"] not in {r2["dataset"] for r2 in rows}]
    (ROOT / "clean.json").write_text(json.dumps(keep + rows, ensure_ascii=False, indent=1))


def load(rel: str) -> np.ndarray:
    x, sr = sf.read(ROOT / rel if not str(rel).startswith("/") else rel, dtype="float32")
    return resample(x, sr)


def mix(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    if len(noise) < len(speech):
        noise = np.tile(noise, int(np.ceil(len(speech) / len(noise))))
    start = int(RNG.integers(0, len(noise) - len(speech) + 1))
    n = noise[start:start + len(speech)]
    ps, pn = np.mean(speech ** 2), np.mean(n ** 2) + 1e-12
    out = speech + n * np.sqrt(ps / (pn * 10 ** (snr_db / 10)))
    peak = np.abs(out).max()
    return out / peak * 0.9 if peak > 0.99 else out


def words_en(ref: str) -> list[str]:
    return re.findall(r"[a-z][a-z'\-]+", ref.casefold())


def derive() -> None:
    from wordfreq import zipf_frequency
    clean = json.loads((ROOT / "clean.json").read_text())
    by_lang = defaultdict(list)
    for r in clean:
        by_lang[(r["dataset"], r["lang"])].append(r)
    out = list(clean)
    esc = [l.split() for l in (ESC / "list.txt").read_text().splitlines()]
    desk = [(f, c) for f, c in esc if c in DESK_NOISE]

    # 1. real noise and babble mixed into real speech
    pick = (by_lang[("fleurs-zh", "zh")][:50] + by_lang[("ascend", "mixed")][:50]
            + by_lang[("fleurs-en", "en")][:30])
    babble_pool = by_lang[("fleurs-zh", "zh")][50:] + by_lang[("fleurs-en", "en")][30:]
    for k, r in enumerate(pick):
        s = load(r["path"])
        f, cat = desk[k % len(desk)]
        noise = load(str(ESC / "audio" / f))
        for snr in (10, 0):
            cid = f"{r['id']}-{cat}-{snr}db"
            out.append(dict(r, id=cid, group="noisy", noise=cat, snr=snr, base=r["id"],
                            path=save(f"noisy/{cid}.wav", mix(s, noise, snr))))
        others = [load(o["path"]) for o in RNG.choice(babble_pool, 3, replace=False)]
        bab = sum(np.resize(o, len(s)) for o in others)
        cid = f"{r['id']}-babble-10db"
        out.append(dict(r, id=cid, group="noisy", noise="babble", snr=10, base=r["id"],
                        path=save(f"noisy/{cid}.wav", mix(s, bab, 10))))

    # 2. quiet microphone: -30 dB and -45 dB
    for r in (by_lang[("fleurs-zh", "zh")][60:80] + by_lang[("ascend", "mixed")][60:80]
              + by_lang[("fleurs-en", "en")][40:60]):
        s = load(r["path"])
        for db in (30, 45):
            cid = f"{r['id']}-minus{db}db"
            out.append(dict(r, id=cid, group="quiet", atten=db, base=r["id"],
                            path=save(f"quiet/{cid}.wav", s * 10 ** (-db / 20))))

    # 3. long dictation: consecutive turns joined with natural pauses
    def joined(items, cid):
        parts, refs = [], []
        for it in items:
            parts += [load(it["path"]), np.zeros(int(SR * RNG.uniform(.4, .9)), np.float32)]
            refs.append(it["reference"])
        sep = "" if items[0]["lang"] == "zh" else " "
        return dict(id=cid, group="long", lang=items[0]["lang"] if items[0]["lang"] != "en" else "en",
                    dataset=items[0]["dataset"], reference=sep.join(refs), parts=[i["id"] for i in items],
                    path=save(f"long/{cid}.wav", np.concatenate(parts)))
    for k, (ds, lang, n) in enumerate([("fleurs-zh", "zh", 10), ("fleurs-zh", "zh", 16), ("fleurs-zh", "zh", 24),
                                       ("ascend", "mixed", 14), ("ascend", "mixed", 24), ("ascend", "mixed", 36),
                                       ("fleurs-en", "en", 10), ("fleurs-en", "en", 16)]):
        pool = by_lang[(ds, lang)]
        start = (k * 17) % max(1, len(pool) - n)
        out.append(joined(pool[start:start + n], f"long-{ds}-{lang}-{n}"))

    # 4. non-speech: real ESC-50 + the SH2 synthetic set
    for f, cat in esc:
        out.append(dict(id=f"esc-{f[:-4]}", group="nonspeech", noise=cat, lang="", reference="",
                        path=save(f"nonspeech/{f}", load(str(ESC / "audio" / f)))))
    sh2 = Path.home() / "Desktop/tt-sherpa2/artifacts/sherpa2"
    for r in json.loads((sh2 / "manifest.json").read_text()):
        if r["group"] == "noise":
            out.append(dict(id="synth-" + r["id"], group="nonspeech", noise=r["id"].rsplit("-", 1)[0], lang="",
                            reference="", path=save(f"nonspeech/{r['id']}.wav", load(str(sh2 / r["path"])))))

    # 5. hotwords on real speech: rare English words people actually said (ASCEND)
    mixed = by_lang[("ascend", "mixed")] + by_lang[("ascend", "en")]
    df = Counter(w for r in mixed for w in set(words_en(r["reference"])))
    terms = sorted({w for w, c in df.items() if c <= 3 and len(w) >= 4 and zipf_frequency(w, "en") < 4.2},
                   key=lambda w: (zipf_frequency(w, "en"), w))
    batches, cur = [], []
    for w in terms:
        if len(",".join(cur + [w]).encode()) > 60:
            batches.append(cur)
            cur = []
        cur.append(w)
    if cur:
        batches.append(cur)
    hot = []
    for b, words in enumerate(batches):
        targets = [r["id"] for r in mixed if set(words_en(r["reference"])) & set(words)]
        hot.append(dict(batch=b, terms=words, targets=targets))
    (ROOT / "hotwords.json").write_text(json.dumps(hot, ensure_ascii=False, indent=1))

    for r in out:
        r["sha256"] = hashlib.sha256((ROOT / r["path"]).read_bytes()).hexdigest()
    (ROOT / "manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(Counter(r["group"] for r in out), "hotword batches", len(batches), "terms", len(terms))


def hotfetch() -> None:
    """Hotword group from the whole ASCEND test split: real words people said."""
    import pyarrow.parquet as pq
    from wordfreq import zipf_frequency
    refs = [r for r in json.loads((ROOT / "ascend-refs.json").read_text()) if r["lang"] in ("mixed", "en")]
    df = Counter(w for r in refs for w in set(words_en(r["ref"])))
    terms = sorted({w for w, c in df.items() if c <= 4 and len(w) >= 4 and 1.5 <= zipf_frequency(w, "en") < 4.2},
                   key=lambda w: (zipf_frequency(w, "en"), w))
    batches, cur = [], []
    for w in terms:
        if len(",".join(cur + [w]).encode()) > 60:
            batches.append(cur)
            cur = []
        cur.append(w)
    if cur:
        batches.append(cur)
    need = {}
    for b, words in enumerate(batches):
        for r in refs:
            if set(words_en(r["ref"])) & set(words):
                need.setdefault(r["id"], b)
    url, size = SHARDS["ascend"]
    shard = ROOT / "ascend.parquet"
    if not shard.exists() or shard.stat().st_size != size:
        with urllib.request.urlopen(url + "?download=true", timeout=120) as src, shard.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
    rows = []
    pf = pq.ParquetFile(shard, memory_map=True, pre_buffer=False, buffer_size=1 << 16)
    for batch in pf.iter_batches(batch_size=1, use_threads=False):
        for row in batch.to_pylist():
            if row["id"] not in need:
                continue
            x, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
            x = resample(x, sr)
            if not 1.0 <= len(x) / SR <= 15:
                continue
            cid = f"hot-{row['id']}"
            rows.append(dict(id=cid, group="hotword", lang=row["language"], dataset="ascend",
                             reference=row["transcription"].strip(), batch=need[row["id"]],
                             source_id=row["id"], path=save(f"hotword/{cid}.wav", x)))
    shard.unlink()
    hot = [dict(batch=b, terms=w, targets=[r["id"] for r in rows if r["batch"] == b]) for b, w in enumerate(batches)]
    (ROOT / "hotwords.json").write_text(json.dumps(hot, ensure_ascii=False, indent=1))
    man = [r for r in json.loads((ROOT / "manifest.json").read_text()) if r["group"] != "hotword"]
    for r in rows:
        r["sha256"] = hashlib.sha256((ROOT / r["path"]).read_bytes()).hexdigest()
    (ROOT / "manifest.json").write_text(json.dumps(man + rows, ensure_ascii=False, indent=1))
    print("terms", len(terms), "batches", len(batches), "target clips", len(rows))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "derive", "hotfetch"])
    ap.add_argument("only", nargs="*")
    a = ap.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    {"fetch": fetch, "derive": derive, "hotfetch": hotfetch}[a.step]()
