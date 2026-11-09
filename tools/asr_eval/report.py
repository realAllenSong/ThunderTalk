"""Score eval v2 results: python -m tools.asr_eval.report [--out report.md]

Error rate = edit distance over units (one unit per Han character, one per
Latin word), micro-averaged. Both sides are normalised the same way: NFKC,
punctuation dropped, case folded, ASCII digits written as Chinese digits for
zh/mixed, acronyms split into letters (ASCEND writes "i s m"), fillers
(um/uh/嗯/呃/啊/哦…) and [UNK] removed. 95% intervals: paired bootstrap over
utterances (2000 resamples, seed 0).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

from tools.asr_eval.corpus import ROOT

FILLERS = {"um", "uh", "er", "erm", "ah", "eh", "hmm", "mm", "oh", "嗯", "呃", "啊", "哦", "噢", "额", "唉", "诶", "欸", "哎", "呀"}
CN_DIGITS = dict(zip("0123456789", "零一二三四五六七八九"))
CONFIGS = [("1.12.38", "funasr"), ("1.12.38", "qwen"), ("2.0.1", "funasr"), ("2.0.1", "qwen")]
NAMES = {"funasr": "Fun-ASR-Nano", "qwen": "Qwen3-ASR"}


def units(text: str, lang: str) -> list[str]:
    s = unicodedata.normalize("NFKC", text).replace("[UNK]", " ").replace("<unk>", " ")
    s = re.sub(r"(?<![A-Za-z])([A-Z]{2,6})(?![A-Za-z])", lambda m: " ".join(m.group(1)), s)
    s = s.casefold()
    if lang != "en":
        s = re.sub(r"\d", lambda m: CN_DIGITS[m.group(0)], s)
    toks = re.findall(r"[a-z0-9]+(?:'[a-z]+)?|[㐀-鿿]", s)
    return [t for t in toks if t not in FILLERS]


def edits(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        cur = [i]
        for j, b in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return prev[-1]


def score(ref: str, hyp: str, lang: str) -> tuple[int, int]:
    r, h = units(ref, lang), units(hyp, lang)
    return edits(r, h), len(r)


def load(ver: str, model: str) -> dict:
    path = ROOT / "results" / f"{ver}-{model}.jsonl"
    out = defaultdict(dict)
    if path.exists():
        for line in path.read_text().splitlines():
            r = json.loads(line)
            out[r["phase"]][(r["id"], r.get("run", 0))] = r
    return out


def rate(pairs) -> float:
    e = sum(p[0] for p in pairs)
    n = sum(p[1] for p in pairs)
    return e / n if n else float("nan")


def boot_ci(pairs, b=2000):
    if not pairs:
        return float("nan"), float("nan")
    rng = np.random.default_rng(0)
    e = np.array([p[0] for p in pairs], float)
    n = np.array([p[1] for p in pairs], float)
    idx = rng.integers(0, len(pairs), (b, len(pairs)))
    r = e[idx].sum(1) / np.maximum(1, n[idx].sum(1))
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def paired_diff(a, b, n_boot=2000):
    """a, b: aligned lists of (errors, units). Returns diff of rates (a-b) and its 95% CI."""
    rng = np.random.default_rng(0)
    ea, eb = np.array([p[0] for p in a], float), np.array([p[0] for p in b], float)
    n = np.array([p[1] for p in a], float)
    idx = rng.integers(0, len(a), (n_boot, len(a)))
    d = (ea[idx].sum(1) - eb[idx].sum(1)) / np.maximum(1, n[idx].sum(1))
    return (ea.sum() - eb.sum()) / max(1, n.sum()), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def pct(x: float) -> str:
    return "–" if x != x else f"{100 * x:.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    clips = {r["id"]: r for r in json.loads((ROOT / "manifest.json").read_text())}
    hot = json.loads((ROOT / "hotwords.json").read_text())
    data = {c: load(*c) for c in CONFIGS}
    present = [c for c in CONFIGS if data[c].get("main")]
    L: list[str] = []
    p = L.append

    def per_clip(cfg, group_filter):
        res = {}
        for (cid, _), r in data[cfg]["main"].items():
            c = clips.get(cid)
            if c and group_filter(c):
                res[cid] = score(c["reference"], r["text"], c["lang"])
        return res

    def table(title, filters, note=""):
        p(f"\n### {title}\n")
        if note:
            p(note + "\n")
        p("| Set | n | " + " | ".join(f"{NAMES[m]} {v}" for v, m in present) + " |")
        p("|---|---|" + "---|" * len(present))
        for label, flt in filters:
            sc = {cfg: per_clip(cfg, flt) for cfg in present}
            common = set.intersection(*(set(s) for s in sc.values())) if sc else set()
            cells = []
            for cfg in present:
                pairs = [sc[cfg][i] for i in sorted(common)]
                lo, hi = boot_ci(pairs)
                cells.append(f"{pct(rate(pairs))} ({pct(lo)}–{pct(hi)})")
            p(f"| {label} | {len(common)} | " + " | ".join(cells) + " |")

    is_ = lambda **kw: (lambda c: all(c.get(k) == v for k, v in kw.items()))
    p("# ASR eval v2\n")
    p(f"Configs with results: {', '.join(f'{NAMES[m]} sherpa {v}' for v, m in present)}. "
      "Cell: error rate (95% bootstrap CI). Lower is better.")

    table("Clean real speech", [
        ("FLEURS zh (read)", is_(group="clean", dataset="fleurs-zh")),
        ("FLEURS en (read)", is_(group="clean", dataset="fleurs-en")),
        ("ASCEND zh (conversation)", is_(group="clean", dataset="ascend", lang="zh")),
        ("ASCEND en (conversation)", is_(group="clean", dataset="ascend", lang="en")),
        ("ASCEND zh-en mixed", is_(group="clean", dataset="ascend", lang="mixed")),
        ("All clean", is_(group="clean")),
    ])
    table("Real noise mixed into speech", [
        ("desk noise 10 dB", lambda c: c["group"] == "noisy" and c["noise"] != "babble" and c["snr"] == 10),
        ("desk noise 0 dB", lambda c: c["group"] == "noisy" and c["noise"] != "babble" and c["snr"] == 0),
        ("babble 10 dB", lambda c: c["group"] == "noisy" and c["noise"] == "babble"),
        ("same clips, clean", lambda c: c["group"] == "clean" and any(
            o.get("base") == c["id"] for o in clips.values() if o["group"] == "noisy")),
    ], "ESC-50 keyboard, vacuum, rain, washing machine, footsteps, clicks, clock, water, wind, knock; babble = 3 other speakers.")
    table("Quiet microphone", [
        ("-30 dB", lambda c: c["group"] == "quiet" and c["atten"] == 30),
        ("-45 dB", lambda c: c["group"] == "quiet" and c["atten"] == 45),
        ("same clips, normal level", lambda c: c["group"] == "clean" and any(
            o.get("base") == c["id"] for o in clips.values() if o["group"] == "quiet")),
    ])
    table("Long dictation (60–230 s)", [
        ("all long takes", is_(group="long")),
    ])

    # catastrophic failures and stability
    p("\n### Stability\n")
    p("| Config | empty output on speech | ≥50% wrong | truncated / partial | long takes ≥30% wrong | repeat runs identical | median latency (clean) | p95 | RTF | peak RSS |")
    p("|---|---|---|---|---|---|---|---|---|---|")
    for cfg in present:
        m = data[cfg]["main"]
        speech = [(clips[i], r) for (i, _), r in m.items() if i in clips and clips[i]["group"] in ("clean", "noisy", "quiet", "long", "hotword")]
        empty = sum(not r["text"].strip() for _, r in speech)
        bad = sum(1 for c, r in speech if (lambda e, n: n and e / n >= .5)(*score(c["reference"], r["text"], c["lang"])))
        trunc = sum(bool(r["truncated"]) or r["recovery_source"] in ("partial", "failed") for _, r in speech)
        longbad = sum(1 for c, r in speech if c["group"] == "long" and (lambda e, n: e / n >= .3)(*score(c["reference"], r["text"], c["lang"])))
        rep = data[cfg].get("repeat", {})
        same = sum(r["text"] == m.get((i, 0), {}).get("text") for (i, _), r in rep.items())
        lat = sorted(r["wall_s"] for c, r in speech if c["group"] == "clean")
        rtf = sum(r["wall_s"] for c, r in speech if c["group"] == "clean") / max(1e-9, sum(r["duration"] for c, r in speech if c["group"] == "clean"))
        rss = max(r["rss_gb"] for r in m.values())
        p(f"| {NAMES[cfg[1]]} {cfg[0]} | {empty}/{len(speech)} | {bad} | {trunc} | {longbad}/{sum(c['group'] == 'long' for c, _ in speech)} | "
          f"{same}/{len(rep)} | {1000 * statistics.median(lat):.0f} ms | {1000 * lat[int(.95 * (len(lat) - 1))]:.0f} ms | {rtf:.3f} | {rss:.2f} GB |")

    # non-speech
    p("\n### Non-speech (should paste nothing)\n")
    p("| Config | ESC-50 real noise pasted | synthetic noise pasted | examples |")
    p("|---|---|---|---|")
    for cfg in present:
        m = data[cfg]["main"]
        ns = [(clips[i], r) for (i, _), r in m.items() if i in clips and clips[i]["group"] == "nonspeech"]
        esc = [(c, r) for c, r in ns if c["id"].startswith("esc-")]
        syn = [(c, r) for c, r in ns if c["id"].startswith("synth-")]
        ex = "; ".join(f"{c['noise']}: {r['text'][:24]}" for c, r in esc if r["text"].strip())[:300]
        p(f"| {NAMES[cfg[1]]} {cfg[0]} | {sum(bool(r['text'].strip()) for _, r in esc)}/{len(esc)} | "
          f"{sum(bool(r['text'].strip()) for _, r in syn)}/{len(syn)} | {ex} |")

    # hotwords on real speech
    p("\n### Hotwords on real speech (ASCEND words people actually said)\n")
    p("| Config | term recall, no hotwords | term recall, with hotwords | target error rate no → with | false insertions in controls | control error rate no → with |")
    p("|---|---|---|---|---|---|")
    for cfg in present:
        m = data[cfg]["main"]
        h0 = h1 = tot = ins = 0
        t0, t1, c0, c1 = [], [], [], []
        for b in hot:
            ph = data[cfg].get(f"hot-{b['batch']}", {})
            if not ph:
                continue
            terms = set(b["terms"])
            for (cid, _), r in ph.items():
                c = clips[cid]
                base = m.get((cid, 0))
                if base is None:
                    continue
                ref_terms = [w for w in re.findall(r"[a-z'\-]+", c["reference"].casefold()) if w in terms]
                hy0 = set(re.findall(r"[a-z'\-]+", base["text"].casefold()))
                hy1 = set(re.findall(r"[a-z'\-]+", r["text"].casefold()))
                if cid in b["targets"]:
                    tot += len(ref_terms)
                    h0 += sum(w in hy0 for w in ref_terms)
                    h1 += sum(w in hy1 for w in ref_terms)
                    t0.append(score(c["reference"], base["text"], c["lang"]))
                    t1.append(score(c["reference"], r["text"], c["lang"]))
                else:
                    ins += len((hy1 & terms) - set(re.findall(r"[a-z'\-]+", c["reference"].casefold())))
                    c0.append(score(c["reference"], base["text"], c["lang"]))
                    c1.append(score(c["reference"], r["text"], c["lang"]))
        if tot:
            p(f"| {NAMES[cfg[1]]} {cfg[0]} | {pct(h0 / tot)} ({h0}/{tot}) | {pct(h1 / tot)} ({h1}/{tot}) | "
              f"{pct(rate(t0))} → {pct(rate(t1))} | {ins} | {pct(rate(c0))} → {pct(rate(c1))} |")

    # paired comparisons
    p("\n### Paired differences (all clean + noisy + quiet speech)\n")
    p("| Comparison | difference in error rate | 95% CI | verdict |")
    p("|---|---|---|---|")
    speech_ids = [i for i, c in clips.items() if c["group"] in ("clean", "noisy", "quiet")]

    def compare(ca, cb, label):
        if ca not in present or cb not in present:
            return
        ma, mb = data[ca]["main"], data[cb]["main"]
        ids = [i for i in speech_ids if (i, 0) in ma and (i, 0) in mb]
        A = [score(clips[i]["reference"], ma[(i, 0)]["text"], clips[i]["lang"]) for i in ids]
        B = [score(clips[i]["reference"], mb[(i, 0)]["text"], clips[i]["lang"]) for i in ids]
        d, lo, hi = paired_diff(A, B)
        verdict = "no significant difference" if lo <= 0 <= hi else (f"{label.split(' vs ')[1]} better" if d > 0 else f"{label.split(' vs ')[0]} better")
        p(f"| {label} | {100 * d:+.2f} pts | {100 * lo:+.2f} … {100 * hi:+.2f} | {verdict} |")
    compare(("1.12.38", "funasr"), ("1.12.38", "qwen"), "Fun-ASR vs Qwen3 (1.12.38)")
    compare(("2.0.1", "funasr"), ("2.0.1", "qwen"), "Fun-ASR vs Qwen3 (2.0.1)")
    compare(("2.0.1", "funasr"), ("1.12.38", "funasr"), "Fun-ASR 2.0.1 vs 1.12.38")
    compare(("2.0.1", "qwen"), ("1.12.38", "qwen"), "Qwen3 2.0.1 vs 1.12.38")

    text = "\n".join(L)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
