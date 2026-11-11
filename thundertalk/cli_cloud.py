"""``ThunderTalk --cli cloud …``: cloud API engines (bring your own key).

    cloud list                         providers, key present (never the key), models
    cloud set-key PROVIDER             reads the key from stdin, stores it in the Keychain
    cloud remove-key PROVIDER
    cloud set PROVIDER [--base-url URL] [--asr-model M] [--tts-model M] [--voice V]
    cloud test PROVIDER                one cheap authenticated call
"""

from __future__ import annotations

import sys


def add_parser(sub) -> None:
    from thundertalk.core.cloud import PROVIDERS
    p = sub.add_parser("cloud", help="cloud ASR/TTS providers and API keys (opt-in)")
    p.add_argument("action", choices=("list", "set-key", "remove-key", "set", "test"))
    p.add_argument("provider", nargs="?", choices=tuple(PROVIDERS))
    p.add_argument("--base-url")
    p.add_argument("--asr-model")
    p.add_argument("--tts-model")
    p.add_argument("--voice")


def cmd_cloud(a, out) -> dict:
    from thundertalk.cli import CliError
    from thundertalk.core import cloud
    if a.action == "list":
        return {"providers": cloud.summary()}
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
    if a.action == "remove-key":
        removed = cloud.remove_key(pid)
        return {"provider": pid, "removed": removed, "key": cloud.key_source(pid) or "none"}
    if a.action == "set":
        cfg = cloud.save_config(pid, base_url=a.base_url, asr_model=a.asr_model,
                                tts_model=a.tts_model, voice=a.voice)
        return {"provider": pid, **cfg, "ready": cloud.is_configured(pid)}
    out.stage("test")
    return cloud.test(pid)
