"""``ThunderTalk --cli eval``: measure local models against a cloud reference.

    eval [INPUT …] [--recent] [--suite standard] [--reference openai:gpt-transcribe]
         [--engines best,apple,…] [--per-category N] [--preview] [--limit N] [--out report.md]

``--suite standard`` is the fixed benchmark: short and long clips in Chinese,
English and mixed Chinese-English, with human reference transcripts (FLEURS
and ASCEND, from the eval v2 corpus in ~/Library/Caches/ThunderTalk-bench/
asr-eval or $THUNDERTALK_EVAL_CORPUS). Results are split by category, so a
model that is fine on short Chinese but breaks on long or mixed speech shows
it. ``--preview`` also replays each long clip through the live preview as if
it were being dictated (same windows, commits and too-slow cut-off as the
app) and reports whether the preview kept up to the end.

Each clip needs a reference transcript. Recent dictations (``--recent``, the
last 20 kept in ~/.thundertalk/recordings when "keep recent recordings" is on)
that were dictated with a cloud engine already have one, at no cost. Other
clips get one from ``--reference`` (a cloud model: the audio is sent and
billed; answers are cached in ~/.thundertalk/eval/refs.json so a clip is never
paid for twice). Clips with neither are skipped.

Each local engine then transcribes every clip, one engine loaded at a time.
The result reports, per engine, the token error rate (CJK characters and
Latin words; case, punctuation and Traditional/Simplified differences
ignored), the same after core/tidy.py removes fillers and restarts (what
dictation pastes), how many Latin terms of the reference it got, and the terms it
missed most: those are the hotword suggestions.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

RECORDINGS = Path.home() / ".thundertalk" / "recordings"
CACHE = Path.home() / ".thundertalk" / "eval" / "refs.json"
AUDIO_EXT = {".wav", ".m4a", ".mp3", ".flac", ".aac", ".ogg", ".opus", ".mp4", ".mov", ".webm", ".aiff"}


def add_parser(sub) -> None:
    p = sub.add_parser("eval", help="compare local models with a cloud reference transcript")
    p.add_argument("inputs", nargs="*", help="audio files or folders")
    p.add_argument("--recent", action="store_true", help="use the recent dictation recordings")
    p.add_argument("--suite", choices=("standard",), help="the fixed benchmark (short/long × zh/en/mixed)")
    p.add_argument("--per-category", type=int, default=12, help="short clips per language in the suite")
    p.add_argument("--preview", action="store_true", help="replay long clips through the live preview")
    p.add_argument("--split", choices=("test", "dev"), default="test",
                   help="suite split: test (fixed, report) or dev (other clips, for tuning)")
    p.add_argument("--hotwords", action="store_true", help="suite: also load the user's hotwords")
    p.add_argument("--context", help="experiment: Qwen3-ASR context prompt ('terms' = built-in terms)")
    p.add_argument("--reference", default="", help="cloud model for clips without a reference, "
                   "e.g. openai:gpt-transcribe (sends the audio; billed)")
    p.add_argument("--engines", default="best", help="comma-separated local engines (default: best)")
    p.add_argument("--limit", type=int, default=200, help="at most this many clips (default 200)")
    p.add_argument("--out", help="also write a Markdown report with every clip")


# ── scoring ──────────────────────────────────────────────────────────────

_TOKEN = re.compile(r"[一-鿿぀-ヿ]|[A-Za-z0-9]+(?:['.][A-Za-z0-9]+)*")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.+#-]*[A-Za-z0-9+#]|[A-Za-z]{2,}")
# Everyday English that is not worth a hotword.
_COMMON = set("""a an and are as at be been but by can could did do does for from had has have he her
his how i if in is it its just like me my no not of ok okay on or our out she so some than that the
their them then there these they this to too up us very was we were what when where which who why
will with would yeah yes you your all any get got one two make sure maybe""".split())


_SCORE_FILLERS = {"um", "uh", "erm", "hmm", "mm", "uhm", "呃", "嗯", "额", "哼"}


def tokens(text: str) -> list[str]:
    """Scoring tokens. Normalised so a reference's conventions are not counted
    as errors: Traditional→Simplified, Chinese numbers → digits (the app's ITN),
    fillers and [UNK] dropped, spelled-out letters joined ("i s m" = "ISM")."""
    from thundertalk.core.cloud import to_simplified
    from thundertalk.core.itn import normalize_numbers
    text = re.sub(r"\[unk\]|<unk>", " ", text or "", flags=re.IGNORECASE)
    # Spelled-out letters first ("i s m" → "ism"), before ITN touches spacing.
    text = re.sub(r"(?<![A-Za-z])[A-Za-z](?:\s+[A-Za-z](?![A-Za-z]))+",
                  lambda m: re.sub(r"\s+", "", m.group(0)), text)
    raw = [t.lower() for t in _TOKEN.findall(normalize_numbers(to_simplified(text)))]
    return [t for t in raw if t not in _SCORE_FILLERS]


def edit_distance(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def latin_terms(text: str) -> list[str]:
    """Latin words/names in a transcript worth getting right (not everyday words)."""
    out = []
    for m in _LATIN.finditer(text or ""):
        w = m.group(0).strip(".-")
        if len(w) >= 2 and w.lower() not in _COMMON:
            out.append(w)
    return out


def score(ref: str, hyp: str) -> dict:
    from thundertalk.core.tidy import tidy
    r, h = tokens(ref), tokens(hyp)
    tidy_errors = edit_distance(tokens(tidy(ref)), tokens(tidy(hyp)))
    terms = latin_terms(ref)
    hyp_l = (hyp or "").lower()
    hit = [t for t in terms if t.lower() in hyp_l]
    return {"errors": edit_distance(r, h), "tidy_errors": tidy_errors, "ref_tokens": len(r), "terms": len(terms),
            "terms_hit": len(hit), "missed": [t for t in terms if t.lower() not in hyp_l]}


# ── clips ────────────────────────────────────────────────────────────────

def corpus_root() -> Path:
    import os
    env = os.environ.get("THUNDERTALK_EVAL_CORPUS")
    return Path(env).expanduser() if env else Path.home() / "Library" / "Caches" / "ThunderTalk-bench" / "asr-eval"


def _lang_of(text: str) -> str:
    han = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z]{2,}", text or ""))
    if han >= 4 and latin >= 2:
        return "mixed"
    return "zh" if han >= latin else "en"


def _category(seconds: float, lang: str) -> str:
    return ("long" if seconds >= 30 else "short") + "-" + lang


def _suite_clips(per_category: int, split: str = "test") -> list[dict]:
    from thundertalk.cli import CliError
    root = corpus_root()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CliError("not_found", f"No eval corpus at {root} (set THUNDERTALK_EVAL_CORPUS)") from None
    # test = the first per_category short clips per language + the long clips;
    # dev = the next 3×per_category short clips (never scored in a report).
    lo, hi = (0, per_category) if split == "test" else (per_category, 4 * per_category)
    in_long = {part for r in manifest if r.get("group") == "long" for part in r.get("parts") or []}
    picked, counts = [], Counter()
    for r in manifest:
        lang, group = r.get("lang") or "", r.get("group")
        if split == "dev" and r.get("id") in in_long:
            continue                                    # no test audio in dev
        if lang not in ("zh", "en", "mixed") or group not in ("clean", "long"):
            continue
        if group == "long" and split == "dev":
            continue
        if group == "clean":
            counts[lang] += 1
            if not lo < counts[lang] <= hi:
                continue
        picked.append({"path": str(root / r["path"]), "reference": r["reference"],
                       "reference_by": "human", "language": "auto",
                       "category": ("long-" if group == "long" else "short-") + lang})
    return picked

def _recent_clips() -> list[dict]:
    clips = []
    for meta in sorted(RECORDINGS.glob("*.json")):
        wav = meta.with_suffix(".wav")
        if not wav.is_file():
            continue
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        from thundertalk.core import cloud
        ref = d.get("final_asr_text") or ""
        clips.append({"path": str(wav), "reference": ref if cloud.is_cloud_id(d.get("model") or "") and ref else "",
                      "reference_by": d.get("model") if cloud.is_cloud_id(d.get("model") or "") else "",
                      "language": d.get("language") or "auto",
                      "category": _category(float(d.get("duration") or 0), _lang_of(ref)) if ref else ""})
    return clips


def _input_clips(inputs: list[str]) -> list[dict]:
    from thundertalk.cli import CliError
    out = []
    for raw in inputs:
        p = Path(raw).expanduser()
        if p.is_dir():
            out += [{"path": str(f), "reference": "", "reference_by": "", "language": "auto"}
                    for f in sorted(p.rglob("*")) if f.suffix.lower() in AUDIO_EXT]
        elif p.is_file():
            out.append({"path": str(p), "reference": "", "reference_by": "", "language": "auto"})
        else:
            raise CliError("not_found", f"Not found: {p}")
    return out


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


# ── command ──────────────────────────────────────────────────────────────

def cmd_eval(a, out) -> dict:
    from thundertalk.cli import CliError, _settings, model_slot, resolve_asr
    from thundertalk.core import audio_io, cloud
    from thundertalk.core.asr import AsrEngine
    from thundertalk.core.models import find, get_model_path
    settings = _settings()
    clips = ((_suite_clips(a.per_category, a.split) if a.suite else []) + (_recent_clips() if a.recent else [])
             + _input_clips(a.inputs))
    if not clips:
        raise CliError("bad_args", "No clips: pass audio files/folders, --recent or --suite standard")
    clips = clips[: max(1, a.limit)]
    if a.reference:
        if not cloud.is_cloud_id(a.reference):
            raise CliError("bad_args", "--reference must be a cloud model, e.g. openai:gpt-transcribe")
        resolve_asr(a.reference, settings)          # checks the key
    engines = []
    for name in [e.strip() for e in a.engines.split(",") if e.strip()]:
        if cloud.is_cloud_id(name):
            raise CliError("bad_args", "--engines are local models; the cloud one goes in --reference")
        mid = resolve_asr(name, settings)
        if mid not in engines:
            engines.append(mid)

    # 1. references
    out.stage("reference")
    cache, spent = _load_cache(), 0.0
    audio: dict[str, object] = {}
    for c in clips:
        x = audio_io.decode_audio(c["path"], 16000)
        audio[c["path"]] = x
        c["seconds"] = round(len(x) / 16000, 2)
        if c["reference"] or not a.reference:
            continue
        key = hashlib.sha1(x.tobytes()).hexdigest() + "|" + a.reference
        if key not in cache:
            r = cloud.transcribe(a.reference, x, 16000, language=c["language"], settings=settings)
            cache[key] = r.text
            spent += c["seconds"]
            _save_cache(cache)
        c["reference"], c["reference_by"] = cache[key], a.reference
    for c in clips:
        if not c.get("category") and c["reference"]:
            c["category"] = _category(c["seconds"], _lang_of(c["reference"]))
    usable = [c for c in clips if c["reference"].strip()]
    if not usable:
        raise CliError("bad_args", "No clip has a reference. Dictate with a cloud engine first, or pass "
                       "--reference <provider>:<model>.")

    # 2. local engines, one at a time
    report = []
    for mid in engines:
        info = find(mid)
        out.stage("load_model")
        out.emit(type="stage", label="engine", engine=mid)
        with model_slot(out, heavy=not info.built_in, info=info):
            eng = AsrEngine()
            if settings.hotwords and (a.hotwords or not a.suite):
                eng.set_hotwords(settings.hotwords)
            if a.context:
                from thundertalk.core.terms import BUILTIN_TERMS
                eng._context_override = " ".join(BUILTIN_TERMS) if a.context == "terms" else a.context
            eng.set_builtin_terms(settings.get("builtin_terms") is not False)
            eng.set_auto_languages(settings.get("auto_languages") or ["zh", "en"])
            eng.load_model(get_model_path(mid), info.family, info.backend, memory_mode=settings.memory_mode)
            out.stage("transcribe")
            from thundertalk.core.dictation import recover_final
            rows, t0 = [], time.monotonic()
            for i, c in enumerate(usable):
                eng.set_language(c["language"])
                x = audio[c["path"]]
                t1 = time.monotonic()
                hyp = recover_final(eng, x).text             # the app's dictation path
                row = {"clip": Path(c["path"]).name, "category": c["category"], "hyp": hyp,
                       "rtf": round((time.monotonic() - t1) / max(c["seconds"], 0.1), 3),
                       **score(c["reference"], hyp)}
                if a.preview and c["seconds"] >= 30:
                    row["preview"] = simulate_preview(lambda s: eng.recognize(s, preview=True).text, x)
                rows.append(row)
                out.progress(int((i + 1) * 100 / len(usable)))
            eng.unload()
        errors = sum(r["errors"] for r in rows)
        ref_tokens = sum(r["ref_tokens"] for r in rows) or 1
        terms = sum(r["terms"] for r in rows)
        missed = Counter(t for r in rows for t in r["missed"])
        have = {w.lower() for w in settings.hotwords}
        tidy_errors = sum(r["tidy_errors"] for r in rows)
        report.append({"engine": mid, "clips": len(rows), "token_error_rate": round(errors / ref_tokens, 4),
                       "token_error_rate_tidy": round(tidy_errors / ref_tokens, 4),
                       "term_recall": round(sum(r["terms_hit"] for r in rows) / terms, 3) if terms else None,
                       "seconds": round(time.monotonic() - t0, 1),
                       "by_category": _by_category(rows),
                       **({"preview": _preview_summary(rows)} if a.preview else {}),
                       "missed_terms": [{"term": t, "count": n} for t, n in missed.most_common(20)],
                       "suggested_hotwords": [t for t, _n in missed.most_common(20) if t.lower() not in have][:10],
                       "_rows": rows})

    result = {"clips": len(usable), "skipped": len(clips) - len(usable),
              "audio_minutes": round(sum(c["seconds"] for c in usable) / 60, 1),
              "references": dict(Counter(c["reference_by"] for c in usable)),
              "reference_minutes_billed": round(spent / 60, 2),
              "engines": [{k: v for k, v in r.items() if k != "_rows"} for r in report]}
    if a.out:
        path = Path(a.out).expanduser()
        path.write_text(_markdown(result, report, usable), encoding="utf-8")
        result["report"] = str(path)
        # Every clip's reference and hypothesis, for rescoring and A/B diffs.
        refs = {Path(c["path"]).name: c["reference"] for c in usable}
        with open(path.with_suffix(".rows.jsonl"), "w", encoding="utf-8") as fh:
            for r in report:
                for row in r["_rows"]:
                    fh.write(json.dumps({"engine": r["engine"], "reference": refs.get(row["clip"], ""), **row},
                                        ensure_ascii=False) + "\n")
    return result


def _by_category(rows: list[dict]) -> dict:
    out = {}
    for cat in sorted({r["category"] for r in rows}):
        rs = [r for r in rows if r["category"] == cat]
        n = sum(r["ref_tokens"] for r in rs) or 1
        out[cat] = {"clips": len(rs), "ter": round(sum(r["errors"] for r in rs) / n, 4),
                    "ter_tidy": round(sum(r["tidy_errors"] for r in rs) / n, 4),
                    "rtf": round(sum(r["rtf"] for r in rs) / len(rs), 3)}
    return out


def _preview_summary(rows: list[dict]) -> dict:
    ps = [r["preview"] for r in rows if "preview" in r]
    return {"long_clips": len(ps), "kept_up": sum(1 for p in ps if p["off_at"] is None),
            "cut_off_at_s": [p["off_at"] for p in ps if p["off_at"] is not None],
            "max_decode_s": max((p["max_decode_s"] for p in ps), default=0.0)}


def simulate_preview(recognize, x, sr: int = 16000) -> dict:
    """Replay ``x`` through LivePreview as if it were being dictated: the same
    tail windows, commits, adaptive interval and MAX_DECODE_S cut-off. Time is
    simulated (decode time advances the clock), so it runs as fast as decoding."""
    import threading
    from thundertalk.core import live_preview as lp_mod
    cur = {"n": 0}
    lp = lp_mod.LivePreview(lambda: x[:cur["n"]], recognize, lock=threading.Lock())
    lp.stats = {"ran": 0, "skipped_busy": 0, "skipped_lock": 0, "errors": 0, "decode_s": [],
                "uncontended_decode_s": [], "contended": 0, "latency_s": [], "commits": 0, "loops": 0}
    t, total, off_at, text, slowest = lp_mod.FIRST_TICK_MS / 1000, len(x) / sr, None, "", 0.0
    while t < total:
        cur["n"] = int(t * sr)
        n0 = len(lp.stats["uncontended_decode_s"])
        t0 = time.perf_counter()
        r = lp._decode(lp._gen)
        dt = time.perf_counter() - t0
        tick_max = max(lp.stats["uncontended_decode_s"][n0:], default=0.0)
        slowest = max(slowest, tick_max)
        if r is not None:
            text = r
        if tick_max > lp_mod.MAX_DECODE_S:
            off_at = round(t, 1)
            break
        interval = max(lp._base_interval, min(lp_mod.MAX_INTERVAL_MS, dt * 2500)) / 1000
        t += max(interval, dt)
    return {"off_at": off_at, "max_decode_s": round(slowest, 2), "ticks": lp.stats["ran"],
            "commits": lp.stats["commits"], "chars": len(text)}


def _markdown(result: dict, report: list[dict], clips: list[dict]) -> str:
    lines = ["# ThunderTalk eval", "",
             f"{result['clips']} clips, {result['audio_minutes']} min; references: "
             + ", ".join(f"{k} ×{v}" for k, v in result["references"].items()), "",
             "| Engine | Token error rate | …after removing fillers/restarts | Term recall | Time |",
             "| --- | --- | --- | --- | --- |"]
    for r in report:
        recall = "–" if r["term_recall"] is None else f"{r['term_recall']:.0%}"
        lines.append(f"| {r['engine']} | {r['token_error_rate']:.1%} | {r['token_error_rate_tidy']:.1%} "
                     f"| {recall} | {r['seconds']} s |")
    cats = sorted({c for r in report for c in r["by_category"]})
    if cats:
        lines += ["", "Token error rate by category (after removing fillers):", "",
                  "| Engine | " + " | ".join(cats) + " |", "| --- |" + " --- |" * len(cats)]
        for r in report:
            lines.append(f"| {r['engine']} | " + " | ".join(
                f"{r['by_category'][c]['ter_tidy']:.1%}" if c in r["by_category"] else "–" for c in cats) + " |")
    if any("preview" in r for r in report):
        lines += ["", "Live preview on long clips:", ""]
        for r in report:
            p = r.get("preview") or {}
            lines.append(f"- {r['engine']}: kept up on {p.get('kept_up', 0)}/{p.get('long_clips', 0)}; "
                         f"cut off at {p.get('cut_off_at_s') or '—'} s; slowest decode {p.get('max_decode_s', 0)} s")
    for r in report:
        lines += ["", f"## {r['engine']}", "",
                  "Missed terms: " + (", ".join(f"{m['term']} ×{m['count']}" for m in r["missed_terms"]) or "none"), ""]
        for c, row in zip(clips, r["_rows"]):
            if row["errors"] == 0:
                continue
            lines += [f"**{row['clip']}** ({row['errors']}/{row['ref_tokens']} tokens)", "",
                      f"- reference: {c['reference']}", f"- {r['engine']}: {row['hyp']}", ""]
    return "\n".join(lines) + "\n"
