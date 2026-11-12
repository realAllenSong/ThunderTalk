"""Built-in vocabulary for the AI era, and safe spelling fixes for it.

``BUILTIN_TERMS`` are product, company and tool names people dictate every day
but recognizers often mangle (Claude Code, ChatGPT, MCP…). They are appended
after the user's own hotwords wherever the engine has room for a longer list
(cloud engines and Apple Speech; see ``asr.AsrEngine._hotword_list``), so the
user's words always come first.

``normalize`` only fixes how an already-recognized term is written: case and
the space inside compound brand names ("chat gpt" → "ChatGPT", "github" →
"GitHub"). It never turns one word into a different word, so it is safe on
every engine's output.
"""

from __future__ import annotations

import re

BUILTIN_TERMS: tuple[str, ...] = (
    # AI companies, models and assistants
    "Claude", "Claude Code", "Anthropic", "OpenAI", "ChatGPT", "Codex", "GPT", "Gemini",
    "DeepMind", "DeepSeek", "Qwen", "Kimi", "Grok", "xAI", "Mistral", "Llama", "Perplexity",
    "Midjourney", "Sora", "Whisper", "ElevenLabs", "Groq", "Hugging Face", "Ollama",
    "NVIDIA", "CUDA", "MLX", "PyTorch", "LangChain",
    # developer tools
    "Cursor", "Copilot", "GitHub", "VS Code", "Xcode", "Homebrew", "Docker", "Kubernetes",
    "Vercel", "Supabase", "Cloudflare", "Next.js", "Node.js", "TypeScript", "JavaScript",
    "Python", "React", "SwiftUI", "macOS", "iOS", "PyInstaller",
    # acronyms
    "MCP", "CLI", "API", "SDK", "LLM", "RAG", "JSON", "YAML", "TTS", "ASR", "UI", "PR",
    # apps
    "Notion", "Figma", "Slack", "Discord", "Telegram", "Obsidian", "Markdown", "ThunderTalk",
    # Chinese products
    "豆包", "通义千问", "文心一言", "智谱", "小红书", "抖音", "哔哩哔哩",
)

# (pattern, replacement). Whole words only; case-insensitive unless noted.
# "\b" would not split "用ios" (CJK counts as a word character), so the
# boundaries are "not an ASCII letter or digit".
# Identifiers, paths, URLs and emails ("qwen3-asr", "github.com", "a/ios/b")
# are left alone too.
_B0, _B1 = r"(?<![A-Za-z0-9_\-./@])", r"(?![A-Za-z0-9_\-/@]|\.[A-Za-z0-9])"
_FIXES: list[tuple[re.Pattern, str]] = [
    (re.compile(_B0 + p.removeprefix(r"\b").removesuffix(r"\b") + _B1, re.IGNORECASE), r) for p, r in (
    (r"\bchat\s?gpt\b", "ChatGPT"),
    (r"\bopen\s?ai\b", "OpenAI"),
    (r"\bgit\s?hub\b", "GitHub"),
    (r"\bhugging\s?face\b", "Hugging Face"),
    (r"\beleven\s?labs\b", "ElevenLabs"),
    (r"\bdeep\s?seek\b", "DeepSeek"),
    (r"\bdeep\s?mind\b", "DeepMind"),
    (r"\bpy\s?torch\b", "PyTorch"),
    (r"\blang\s?chain\b", "LangChain"),
    (r"\btype\s?script\b", "TypeScript"),
    (r"\bjava\s?script\b", "JavaScript"),
    (r"\bnext\.?js\b", "Next.js"),
    (r"\bnode\.?js\b", "Node.js"),
    (r"\bmac\s?os\b", "macOS"),
    (r"\bvs\s?code\b", "VS Code"),
    (r"\bclaude\s+code\b", "Claude Code"),
    (r"\bthunder\s?talk\b", "ThunderTalk"),
    (r"\bswift\s?ui\b", "SwiftUI"),
    (r"\bxcode\b", "Xcode"),
    (r"\bios\b", "iOS"),
    (r"\banthropic\b", "Anthropic"),
    (r"\bnvidia\b", "NVIDIA"),
    (r"\bsupabase\b", "Supabase"),
    (r"\bcloudflare\b", "Cloudflare"),
    (r"\bollama\b", "Ollama"),
    (r"\bqwen\b", "Qwen"),
)]
# Lower-case acronyms become upper case only when they stand alone (so "api"
# → "API" but "rapid" and "qwen3-asr" are untouched). Mixed-case input is
# left alone ("Api" may be a name).
_ACRONYMS = ("mcp", "cli", "api", "sdk", "llm", "json", "yaml", "cuda", "mlx", "gpt", "tts", "asr")
_ACRONYM_RE = re.compile(_B0 + "(" + "|".join(_ACRONYMS) + ")" + _B1)


def normalize(text: str) -> str:
    """Fix the spelling of known terms in recognized text (case and spacing only)."""
    if not text or not any(c.isascii() and c.isalpha() for c in text):
        return text
    for pat, rep in _FIXES:
        text = pat.sub(rep, text)
    return _ACRONYM_RE.sub(lambda m: m.group(1).upper(), text)


def merged(user_words: list[str], include_builtin: bool = True) -> list[str]:
    """User words first, then built-in terms not already present (case-insensitive)."""
    out, seen = [], set()
    for w in list(user_words or []) + (list(BUILTIN_TERMS) if include_builtin else []):
        w = (w or "").strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out
