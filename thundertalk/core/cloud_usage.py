"""What the cloud engines cost: ThunderTalk's own meter, plus the provider's
figures where an API offers them.

Every successful cloud request adds its audio seconds (and, for speech, its
characters) to ``~/.thundertalk/cloud_usage.json``, per month and model. The
app, the CLI and the MCP server all write here (a file lock serialises them).
``estimate`` turns that into US dollars with the list prices below; it covers
only what went through ThunderTalk.

``billing`` asks the provider when it can: OpenAI's organization costs (needs
an optional *admin* key, Keychain account ``openai-admin``; ordinary project
keys cannot read spend) and ElevenLabs' subscription quota (works with the
normal key). Gemini and Groq have no spend API for API keys.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# US$ list prices checked 2026-11-11. ASR per audio minute; TTS per audio
# minute (gpt-4o-mini-tts) or per million characters (tts-1/-hd). Models not
# listed are metered but not priced (plan- or token-based billing).
ASR_PER_MIN = {
    "openai:gpt-transcribe": 0.0045,
    "openai:whisper-1": 0.006,
    "groq:whisper-large-v3-turbo": 0.04 / 60,
    "groq:whisper-large-v3": 0.111 / 60,
}
GROQ_MIN_SECONDS = 10.0                 # Groq bills each request as at least 10 s
TTS_PER_MIN = {"openai:gpt-4o-mini-tts": 0.015}
TTS_PER_MCHAR = {"openai:tts-1": 15.0, "openai:tts-1-hd": 30.0}


def _path() -> Path:
    override = os.environ.get("THUNDERTALK_CLOUD_USAGE")      # tests
    return Path(override) if override else Path.home() / ".thundertalk" / "cloud_usage.json"


def _month(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m")


def _load(fh) -> dict:
    fh.seek(0)
    try:
        data = json.loads(fh.read() or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def record(model_id: str, *, kind: str = "asr", seconds: float = 0.0, chars: int = 0) -> None:
    """Add one request to this month's meter. Never raises."""
    try:
        if model_id.startswith("groq:") and kind == "asr":
            seconds = max(seconds, GROQ_MIN_SECONDS)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data = _load(fh)
            month = data.setdefault(_month(), {})
            row = month.setdefault(f"{kind}|{model_id}", {"requests": 0, "seconds": 0.0, "chars": 0})
            row["requests"] += 1
            row["seconds"] = round(row["seconds"] + float(seconds), 2)
            row["chars"] += int(chars)
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(data, ensure_ascii=False, indent=1))
    except (OSError, ValueError, TypeError) as exc:
        print(f"[CloudUsage] not recorded: {type(exc).__name__}")


def month_rows(month: str = "") -> list[dict]:
    """[{"kind", "model", "requests", "seconds", "chars", "usd"|None}] for a month (default: this one)."""
    try:
        with open(_path(), encoding="utf-8") as fh:
            data = json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        data = {}
    rows = []
    for key, row in (data.get(month or _month()) or {}).items():
        kind, _, model = key.partition("|")
        rows.append({"kind": kind, "model": model, "requests": row.get("requests", 0),
                     "seconds": row.get("seconds", 0.0), "chars": row.get("chars", 0),
                     "usd": _price(kind, model, row)})
    return sorted(rows, key=lambda r: (r["model"], r["kind"]))


def _price(kind: str, model: str, row: dict) -> Optional[float]:
    minutes = float(row.get("seconds", 0.0)) / 60.0
    if kind == "asr" and model in ASR_PER_MIN:
        return round(minutes * ASR_PER_MIN[model], 6)
    if kind == "tts" and model in TTS_PER_MIN:
        return round(minutes * TTS_PER_MIN[model], 6)
    if kind == "tts" and model in TTS_PER_MCHAR:
        return round(int(row.get("chars", 0)) / 1e6 * TTS_PER_MCHAR[model], 6)
    return None


def provider_summary(pid: str, month: str = "") -> dict:
    """{"minutes", "chars", "requests", "usd" (priced part), "unpriced": bool} for one provider."""
    rows = [r for r in month_rows(month) if r["model"].split(":", 1)[0] == pid]
    priced = [r["usd"] for r in rows if r["usd"] is not None]
    return {"minutes": round(sum(r["seconds"] for r in rows) / 60.0, 1),
            "chars": sum(r["chars"] for r in rows), "requests": sum(r["requests"] for r in rows),
            "usd": round(sum(priced), 4) if priced else (0.0 if not rows else None),
            "unpriced": any(r["usd"] is None for r in rows)}


# ── provider-reported figures ────────────────────────────────────────────

_BILLING_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
BILLING_TTL = 600.0


def billing(pid: str, *, refresh: bool = False, settings=None) -> Optional[dict]:
    """What the provider itself reports, or None when it has no API for it (or
    no admin key). Cached for 10 minutes. Raises CloudError on a failed call."""
    from thundertalk.core import cloud
    hit = _BILLING_CACHE.get(pid)
    if hit and not refresh and time.monotonic() - hit[0] < BILLING_TTL:
        return hit[1]
    out: Optional[dict] = None
    if pid == "openai" and cloud.get_key("openai-admin"):
        now = datetime.now(timezone.utc)
        start = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())
        base = cloud.config("openai", settings)["base_url"]
        total, page, currency = 0.0, "", "usd"
        for _ in range(4):
            url = f"{base}/organization/costs?start_time={start}&bucket_width=1d&limit=31"
            if page:
                url += f"&page={page}"
            data = cloud._json(cloud._request("openai", url, timeout=20, account="openai-admin"), "openai")
            for bucket in data.get("data") or []:
                for res in (bucket or {}).get("results") or []:
                    amount = (res or {}).get("amount") or {}
                    total += float(amount.get("value") or 0.0)
                    currency = amount.get("currency") or currency
            page = data.get("next_page") or ""
            if not data.get("has_more") or not page:
                break
        out = {"kind": "spend", "month_usd": round(total, 4), "currency": currency,
               "scope": "organization"}
    elif pid == "elevenlabs" and cloud.is_configured("elevenlabs", settings):
        base = cloud.config("elevenlabs", settings)["base_url"]
        data = cloud._json(cloud._request("elevenlabs", base + "/user/subscription", timeout=15), "elevenlabs")
        out = {"kind": "quota", "used": int(data.get("character_count") or 0),
               "limit": int(data.get("character_limit") or 0), "tier": str(data.get("tier") or ""),
               "resets": int(data.get("next_character_count_reset_unix") or 0)}
    _BILLING_CACHE[pid] = (time.monotonic(), out)
    return out
