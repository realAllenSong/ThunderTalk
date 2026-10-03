"""AI proofreading strings, English and Chinese."""
CLEANUP = {
    "nav.proofread": {"en": "AI Proofread", "zh": "AI 校对"},
    "cleanup.title": {"en": "AI proofreading", "zh": "AI 校对"},
    "cleanup.subtitle": {
        "en": "Fixes words the recognizer misheard — names, model and product names, jargon, typos — using the context, your hotwords and the model's knowledge. Minimal edits; never translates or rewrites.",
        "zh": "修正识别错的词——人名、模型与产品名、专业术语、同音错字——参考上下文、你的热词和模型自身知识。只做最小改动，不翻译、不改写。"},
    "cleanup.enable": {"en": "Proofread dictation", "zh": "校对听写文字"},
    "cleanup.description": {
        "en": "The raw text is pasted at once; the corrected text replaces it only if you haven't typed or clicked since.",
        "zh": "原文立即粘贴；在你没有输入或点击时，才替换为校对后的文字。"},
    "cleanup.privacy": {
        "en": "Privacy: cloud CLIs (Codex, Claude, Cursor, Gemini, Grok) send the dictated text to that provider with your account. Ollama and LM Studio run on this Mac; Cherry Studio and custom APIs may forward text to cloud models. No model is downloaded.",
        "zh": "隐私：云端 CLI（Codex、Claude、Cursor、Gemini、Grok）会用你的账号把听写文字发送给对应服务。Ollama 和 LM Studio 在本机运行；Cherry Studio 与自定义 API 可能转发给云端模型。不会下载模型。"},
    "cleanup.providers": {"en": "Provider", "zh": "服务"},
    "cleanup.providers_hint": {
        "en": "Detected automatically and kept up to date while this page is open.",
        "zh": "自动检测，本页打开期间持续更新。"},
    "cleanup.detecting": {"en": "Looking for providers…", "zh": "正在查找服务…"},
    "cleanup.none": {
        "en": "No provider is ready yet. Log in to a CLI or start a local model app below; it is picked up automatically.",
        "zh": "暂无就绪的服务。请登录下方任一 CLI，或启动本机模型应用，会被自动识别。"},
    "cleanup.choose": {"en": "Use", "zh": "使用"},
    "cleanup.status.ready": {"en": "Ready", "zh": "已就绪"},
    "cleanup.status.login": {"en": "Needs login", "zh": "需要登录"},
    "cleanup.status.unverified": {"en": "Not verified", "zh": "未验证"},
    "cleanup.status.not_running": {"en": "Not running", "zh": "未运行"},
    "cleanup.status.not_installed": {"en": "Not installed", "zh": "未安装"},
    "cleanup.status.needs_key": {"en": "Needs API key", "zh": "需要 API 密钥"},
    "cleanup.status.no_models": {"en": "No models", "zh": "没有模型"},
    "cleanup.status.unsupported": {"en": "Update needed", "zh": "需要更新"},
    "cleanup.status.unavailable": {"en": "Not responding", "zh": "无响应"},
    "cleanup.status.checking": {"en": "Verifying…", "zh": "正在验证…"},
    "cleanup.hint.ready_cli": {"en": "Logged in · {n} models", "zh": "已登录 · {n} 个模型"},
    "cleanup.hint.ready_server": {"en": "Running on port {port} · {n} models", "zh": "运行于端口 {port} · {n} 个模型"},
    "cleanup.hint.ready_custom": {"en": "{url} · {n} models", "zh": "{url} · {n} 个模型"},
    "cleanup.hint.unverified": {
        "en": "Saved credentials found. Verify sends one tiny test request.",
        "zh": "发现已保存的凭据。「验证」会发送一个很小的测试请求。"},
    "cleanup.hint.verify_failed": {
        "en": "Verification failed. Log in again, then verify.", "zh": "验证失败。请重新登录后再验证。"},
    "cleanup.hint.login": {
        "en": "Run the login command in Terminal; this page notices within seconds.",
        "zh": "在终端运行登录命令；本页会在几秒内自动识别。"},
    "cleanup.hint.install_cli": {"en": "Not found on this Mac.", "zh": "本机未找到。"},
    "cleanup.hint.not_running": {
        "en": "Not running — open {app} and start its local server (port {port}). It appears here automatically.",
        "zh": "未运行——请打开 {app} 并启动其本地服务（端口 {port}），启动后自动出现。"},
    "cleanup.hint.not_running.ollama": {
        "en": "Not running — open Ollama or run “ollama serve” (port 11434). It appears here automatically.",
        "zh": "未运行——请打开 Ollama 或运行「ollama serve」（端口 11434），启动后自动出现。"},
    "cleanup.hint.not_running.custom": {"en": "Can't reach {url}.", "zh": "无法连接 {url}。"},
    "cleanup.hint.not_installed_app": {
        "en": "Install {app}, start its local server (port {port}); it appears here automatically.",
        "zh": "安装 {app} 并启动其本地服务（端口 {port}），启动后自动出现。"},
    "cleanup.hint.needs_key": {
        "en": "The server is running but needs its API key.", "zh": "服务正在运行，但需要 API 密钥。"},
    "cleanup.hint.no_models": {
        "en": "Running, but no model is loaded. Load or download one in {app}.",
        "zh": "正在运行，但没有已加载的模型。请在 {app} 中加载或下载模型。"},
    "cleanup.hint.unsupported": {
        "en": "This CLI version lacks required safety flags. Update it.",
        "zh": "此 CLI 版本缺少必要的安全参数，请更新。"},
    "cleanup.hint.unavailable": {
        "en": "Didn't answer in time; retrying automatically.", "zh": "未及时响应，将自动重试。"},
    "cleanup.action.verify": {"en": "Verify", "zh": "验证"},
    "cleanup.action.login": {"en": "How to log in", "zh": "如何登录"},
    "cleanup.action.install": {"en": "How to install", "zh": "如何安装"},
    "cleanup.action.open": {"en": "Open {app}", "zh": "打开 {app}"},
    "cleanup.action.key": {"en": "Enter key", "zh": "输入密钥"},
    "cleanup.action.copy": {"en": "Copy", "zh": "复制"},
    "cleanup.action.copied": {"en": "Copied", "zh": "已复制"},
    "cleanup.command_hint": {"en": "Run in Terminal:", "zh": "在终端运行："},
    "cleanup.key_prompt": {"en": "{app} API server key:", "zh": "{app} API 服务密钥："},
    "cleanup.model": {"en": "Model", "zh": "模型"},
    "cleanup.model_hint": {
        "en": "A fast, inexpensive model is chosen by default; proofreading needs little reasoning.",
        "zh": "默认选择快速、便宜的模型；校对不需要深度推理。"},
    "cleanup.model_none": {"en": "Choose a ready provider first.", "zh": "请先选择已就绪的服务。"},
    "cleanup.model_other": {"en": "Other…", "zh": "其他…"},
    "cleanup.model_other_placeholder": {"en": "Exact model ID", "zh": "准确的模型 ID"},
    "cleanup.model_check": {"en": "Check", "zh": "检查"},
    "cleanup.models.cli": {"en": "Models listed by {name}.", "zh": "{name} 提供的模型列表。"},
    "cleanup.models.server": {"en": "Models installed in {name}.", "zh": "{name} 中已有的模型。"},
    "cleanup.models.curated": {
        "en": "{name} has no model list; the selected model is checked with a tiny request.",
        "zh": "{name} 没有模型列表；所选模型会用一个很小的请求检查。"},
    "cleanup.check.running": {"en": "Checking {model}…", "zh": "正在检查 {model}…"},
    "cleanup.check.ok": {"en": "{model} works · answered in {s:.1f} s", "zh": "{model} 可用 · {s:.1f} 秒响应"},
    "cleanup.check.failed": {
        "en": "{model} didn't work. Choose another model.", "zh": "{model} 不可用，请选择其他模型。"},
    "cleanup.custom": {"en": "Custom OpenAI-compatible API", "zh": "自定义 OpenAI 兼容 API"},
    "cleanup.url": {"en": "Base URL", "zh": "API 地址"},
    "cleanup.url_hint": {"en": "Including /v1. Its models are fetched automatically.", "zh": "包含 /v1，会自动获取其模型列表。"},
    "cleanup.key": {"en": "API key", "zh": "API 密钥"},
    "cleanup.key_hint": {"en": "Optional. Stored only in your local settings file.", "zh": "可选。仅保存在本机设置文件中。"},
    "cleanup.progress": {"en": "Proofreading…", "zh": "正在校对…"},
}
