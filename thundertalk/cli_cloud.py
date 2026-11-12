"""``ThunderTalk --cli cloud …``: cloud API engines (bring your own key).

    cloud list                         providers, key present (never the key), models
    cloud set-key PROVIDER             reads the key from stdin, stores it in the Keychain
    cloud remove-key PROVIDER
    cloud set PROVIDER [--base-url URL] [--asr-model M] [--tts-model M] [--voice V]
    cloud test PROVIDER                one cheap authenticated call
    cloud usage [PROVIDER] [--month YYYY-MM]
                                       this month's metered minutes/characters and list-price
                                       estimate; plus the provider's own figures where an API
                                       has them (OpenAI spend with an admin key, ElevenLabs quota)

``set-key openai-admin`` stores an optional OpenAI admin key, used only to
read the organization's spend. Models must be ones the provider offers
(``cloud list`` shows them); an OpenAI-compatible server lists its own on ``test``.
"""

from __future__ import annotations

import sys


def add_parser(sub) -> None:
    from thundertalk.core.cloud import PROVIDERS
    p = sub.add_parser("cloud", help="cloud ASR/TTS providers and API keys (opt-in)")
    from thundertalk.core.cloud import ADMIN_ACCOUNTS
    p.add_argument("action", choices=("list", "set-key", "remove-key", "set", "test", "usage"))
    p.add_argument("provider", nargs="?", choices=(*PROVIDERS, *ADMIN_ACCOUNTS))
    p.add_argument("--month", help="YYYY-MM (usage; default this month)")
    p.add_argument("--base-url")
    p.add_argument("--asr-model")
    p.add_argument("--tts-model")
    p.add_argument("--voice")


def cmd_cloud(a, out) -> dict:
    from thundertalk.cli import CliError
    from thundertalk.core import cloud
    if a.action == "list":
        return {"providers": cloud.summary()}
    if a.action == "usage":
        return _usage(a, out)
    if not a.provider:
        raise CliError("bad_args", f"cloud {a.action} needs a provider: {', '.join(cloud.PROVIDERS)}")
    pid = a.provider
    if a.action == "set-key":
        if sys.stdin is None or sys.stdin.isatty():
            print(f"Paste the {cloud.PROVIDERS[pid].name} API key, then press Return:", file=sys.stderr)
        key = (sys.stdin.readline() if sys.stdin is not None else "").strip()
        if not key:
            raise CliError("bad_args", "No key on stdin. Example: pbpaste | thundertalk cloud set-key " + pid)
        cloud.set_key(pid, key)
        return {"provider": pid, "key": cloud.key_source(pid) or "none", "ready": cloud.is_configured(pid)}
    if pid in cloud.ADMIN_ACCOUNTS and a.action not in ("remove-key",):
        raise CliError("bad_args", f"{pid} only takes set-key or remove-key")
    if a.action == "remove-key":
        removed = cloud.remove_key(pid)
        return {"provider": pid, "removed": removed, "key": cloud.key_source(pid) or "none"}
    if a.action == "set":
        for kind, value in (("asr", a.asr_model), ("tts", a.tts_model)):
            choices = cloud.model_choices(pid, kind)
            if value and pid != "custom" and value not in choices:
                raise CliError("bad_args", f"{cloud.PROVIDERS[pid].name} {kind.upper()} models: "
                               + (", ".join(choices) or "none"))
        if a.voice and cloud.PROVIDERS[pid].voices and a.voice not in cloud.PROVIDERS[pid].voices:
            raise CliError("bad_args", f"{cloud.PROVIDERS[pid].name} voices: " + ", ".join(cloud.PROVIDERS[pid].voices))
        cfg = cloud.save_config(pid, base_url=a.base_url, asr_model=a.asr_model,
                                tts_model=a.tts_model, voice=a.voice)
        return {"provider": pid, **cfg, "ready": cloud.is_configured(pid)}
    out.stage("test")
    return cloud.test(pid)


def _usage(a, out) -> dict:
    from thundertalk.cli import CliError
    from thundertalk.core import cloud, cloud_usage
    if a.month and not __import__("re").fullmatch(r"\d{4}-\d{2}", a.month):
        raise CliError("bad_args", "--month is YYYY-MM")
    pids = [a.provider] if a.provider in cloud.PROVIDERS else list(cloud.PROVIDERS)
    rows = [r for r in cloud_usage.month_rows(a.month or "") if r["model"].split(":", 1)[0] in pids]
    providers = []
    for pid in pids:
        entry = {"provider": pid, **cloud_usage.provider_summary(pid, a.month or "")}
        if not a.month:
            try:
                b = cloud_usage.billing(pid, refresh=True)
            except cloud.CloudError as exc:
                b = {"error": exc.code, "message": str(exc)[:200]}
            if b is not None:
                entry["reported"] = b
        if entry["requests"] or "reported" in entry or a.provider:
            providers.append(entry)
    return {"month": a.month or cloud_usage._month(), "providers": providers, "rows": rows,
            "note": "usd is a list-price estimate of requests made through ThunderTalk; "
                    "'reported' comes from the provider"}
