# CLI v3 / 命令行 v3

`ThunderTalk --cli <command> …` or `PYTHONPATH=$PWD python -m thundertalk --cli …`.
The packaged binary never starts the GUI, requests permissions, or takes the GUI
instance lock. It can run while the app is open. `status` reports `cli_version: 3`
and `capabilities`. `--help` is available on every command.

打包程序不会打开窗口、弹出权限请求或占用 GUI 单实例锁，可与应用同时运行。
`status` 返回 `cli_version: 3` 和能力列表；各命令均支持 `--help`。

## Commands / 命令

| Command | Inputs, options and outputs / 输入、选项、输出 |
| --- | --- |
| `transcribe INPUT… --out FILE` | One file or URL. `--out-dir DIR` selects batch mode, including one item. / 单文件或网址；`--out-dir` 启用批处理。 |
| `transcribe INPUT… --out-dir DIR` | Mixed files/URLs, sequential, continue on errors; title-based unique filenames. `--format txt\|md\|srt\|vtt\|json`, or `--formats md srt json`. / 文件和网址混合，顺序处理、失败后继续，按标题生成不重名文件。 |
| `notes INPUT --out notes.md` | Media/URL or existing txt/json/md/srt/vtt. Uses the AI Proofread page's configured ready provider/model; `--provider ID --model ID` override this call. The same provider may be local or remote. / 媒体、网址或已有文稿；沿用 AI 校对的服务和模型，也可单次覆盖。 |
| `burn VIDEO --out video.mp4` | Transcribe first; `--subtitles FILE.srt\|vtt\|json` uses existing cues. Default burns pixels with Studio's font renderer; `--soft` muxes a selectable track. MP4/MOV/M4V output; ffmpeg required. / 默认先转写，也可提供字幕；`--soft` 为可选字幕轨，需 ffmpeg。 |
| `translate INPUT --target cmn --out t.txt` | SeamlessM4T speech→text translation, local media or URL. Long audio runs in 30 s units. / 本地媒体或网址的语音翻译，长音频按 30 秒处理。 |
| `translate --text TEXT --source-language eng --target cmn --out t.txt` | Text→text translation; `--text-file FILE` alternative. Use ISO-639-3 codes (`eng`, `cmn`, `jpn`, …). No translated speech generation is offered by the app. / 文字翻译，也可读文件；语言使用三字母代码。应用没有译文语音生成功能。 |
| `speak --text TEXT --out hi.wav` | Or `--text-file FILE`; `.wav`, `.m4a`, `.mp3`. `--engine auto\|best\|apple\|kokoro\|voxcpm2\|indextts`, `--voice ID`, `--language auto\|zh\|en`, `--speed 0.5…2`. / 朗读选项与 v2 相同，并支持保存的克隆声音。 |
| `voices [list] --engine auto` | Built-in `voices` and saved `my_voices`; legacy `voices --engine …` still works. / 内置与“我的声音”；兼容旧语法。 |
| `voices add AUDIO --name NAME [--transcript TEXT]` | Prepare the same 24 kHz reference as Studio (3–20 s, silence/level checks). `--transcript-file FILE` alternative. Returns `my:<id>` and quality warnings. No weights are trained. / 与应用相同的参考音频处理和质量检查；返回保存的声音 ID，不训练权重。 |
| `voices rename my:ID --name NAME` | Rename a saved voice. / 重命名。 |
| `voices edit my:ID --transcript TEXT` | Edit reference text, or `--transcript-file FILE`. Cloned synthesis requires reference text: `speak --voice my:ID --engine voxcpm2 …` (or `indextts`). / 修改参考文稿；克隆朗读需提供文稿。 |
| `voices remove my:ID` | Remove a saved reference. / 删除保存的参考声音。 |
| `history list [--query TEXT --offset 0 --limit 50]` | Search Studio transcript titles/text, paginated; distinct from dictation history. / 分页搜索工作室文稿，区别于听写历史。 |
| `history show ID` | Full transcript and metadata, including notes and speaker names. / 完整文稿、笔记和说话人名。 |
| `history export ID --out FILE [--format FORMAT]` | Any of the five transcript formats; Markdown includes notes. / 五种格式，Markdown 包含笔记。 |
| `history rename ID --title TITLE` | Rename a transcript. / 重命名文稿。 |
| `history edit ID --speaker-name S01=NAME [--notes-file notes.md]` | Repeat `--speaker-name` for more speakers; update saved notes. / 可多次指定说话人名，也可更新笔记。 |
| `history delete ID` | Delete one Studio transcript by its validated ID. / 按 ID 删除文稿。 |
| `models list [--kind asr\|translation\|tts\|music]` | Catalog IDs, readiness, compatibility, download size. / 模型目录、可用状态、兼容性和大小。 |
| `models download ID [--planner]` | Explicit online download, including optional runtime components; progress events. Planner is for music. / 显式联网下载，含所需运行组件；作曲可含规划模型。 |
| `models activate ID` | Persist the ASR, Speak engine/voice or Compose default; already-running GUI picks settings up on restart. The single translation model is usable immediately by `translate`. / 保存默认模型；已打开的 GUI 重启后读取。翻译只有一个固定模型，CLI 可直接使用。 |
| `status` | App/CLI versions, capabilities, models, TTS engines, permissions, ffmpeg. / 版本、能力、模型、权限及 ffmpeg。 |

ASR commands accept `--engine auto|best|apple|MODEL_ID`, `--language auto|zh|en|…`,
and `--speakers` (MOSS). `auto` uses the dictation setting then Apple; `best`
chooses the most accurate downloaded ASR (Qwen3 1.7B first). Inference never
implicitly downloads weights. For TTS, `best` tries IndexTTS, VoxCPM2, Kokoro
(skipped for mixed Chinese/English), then Apple.

ASR 命令支持引擎、语言及 `--speakers`；`auto` 沿用听写设置，`best` 优先已下载的
Qwen3 1.7B。推理不会隐式下载模型；朗读的 `best` 依次尝试 IndexTTS、VoxCPM2、
Kokoro（中英混合时跳过）、Apple。

`transcribe`, `notes` and speech `translate` accept `--start SECONDS`,
`--end SECONDS`, `--max-seconds SECONDS` (ffmpeg required). The download is still
complete; only decoding/inference is limited. Transcript times are relative to
the selected clip, and the result's `start` records the offset. `transcribe`
also accepts `--timestamps` for TXT, repeatable `--speaker-name S01=NAME`,
`--save-history` (opt-in), and `--notes [--provider ID --model ID]` for each input.
If notes fail after ASR, transcripts/history are retained; the result has
`code: notes_failed`, `notes_error`, output paths and exit status 1.
Without `--save-history`, CLI v2's no-history-write behavior is preserved.

笔记失败时仍保留已完成文稿和历史，返回 `notes_failed` 及 `notes_error`。
以上三个媒体命令可设置起止时间或最长秒数（需 ffmpeg）；下载仍为完整音频，字幕时间
相对所选片段。转写还支持 TXT 时间戳、说话人改名、可选保存历史和逐项生成笔记。

## Compose / 作曲

```sh
"$TT" --cli compose --instrumental --style "warm piano, acoustic guitar" --length 10 --model turbo --planner off
"$TT" --cli compose --song --style "Mandarin pop" --lyrics-file song.txt --sung-lyrics sung.json --language zh --length 120 --bpm 100 --takes 2 --seed 42 --model xl --planner on --out-dir ./songs
"$TT" --cli compose lyrics --lyrics-file song.txt --language zh --remember 'ThunderTalk=桑德托克'
"$TT" --cli compose lyrics --forget ThunderTalk
"$TT" --cli compose check ./songs/JOB/take-1.wav
"$TT" --cli compose another ./songs/JOB/take-1.wav --sung-lyrics revised.json
"$TT" --cli compose repaint ./songs/JOB/take-1.wav --start 2 --end 8 --sung-lyrics revised.json
"$TT" --cli compose list --out-dir ./songs
"$TT" --cli compose show ./songs/JOB/take-1.wav
"$TT" --cli compose export ./songs/JOB/take-1.wav --out ./chosen.wav
"$TT" --cli compose delete ./songs/JOB/take-1.wav
```

Lyrics can be inline (`--lyrics`) or a UTF-8 file, with `[Verse]`, `[Chorus]`,
and other section tags. `sung.json` maps whole shown lines to their sung form;
section tags remain intact. `compose lyrics` previews automatic Chinese/English
pronunciation suggestions and manages remembered words. Omitting an override
restores automatic suggestions. Artist mentions produce a nonblocking `warning`
array in stage and result events. Generation uses a unique job directory under
`~/Music/ThunderTalk` by default and preserves WAV + JSON metadata + repaint
latents. Export copies WAV and JSON; repaint needs the original latents beside
the original take. Delete removes that take's three predictable files.

歌词支持内联或 UTF-8 文件及段落标签；`sung.json` 按整行覆盖实际演唱文本。
`compose lyrics` 预览自动发音建议并管理记忆词。艺人名只产生 `warning`，不会阻止生成。
默认保存在 `~/Music/ThunderTalk` 下的独立任务文件夹；重绘需原始隐变量文件。

Studio's hardware tiers and memory admission/watchdog apply: minimum 16 GB,
XL ≥32 GB, planner ≥24 GB, 10–600 s (≤240 s and one take below 24 GB), 1–5 takes,
BPM 30–300, seed 0…4294967295. Default CLI generation is one 30 s take, planner
off; model is the saved Compose choice or Turbo. `--dit-backend auto|mlx|mps`
defaults to Studio's hardware choice. `another` inherits the source style,
lyrics, model and duration with a fresh seed; use `--seed` to reproduce it.
Lyric checks run after the music worker exits and are advisory. Without Qwen ASR,
the take succeeds with `lyric_check.status: unavailable` and `code: music:no_asr`.
Instrumentals report `status: instrumental`. Completed takes are returned even
when a later take fails. SIGINT cancels a job and the worker is terminated/reaped.

硬件分档、内存准入和运行时保护与工作室相同。默认生成一个 30 秒版本、不规划；
另一个版本继承原参数并使用新种子。歌词检查在作曲进程退出后进行，结果仅供参考；
没有 ASR 时仍保留成品。后续失败也会返回已完成版本。SIGINT 取消并清理子进程。

## JSON-lines protocol / JSON 行协议

Stdout is UTF-8 JSON objects, one per line; native/model logs go to stderr.
Normal invocation exits 0 on complete success, 1 on any failure. `--help` is
human-readable. Unknown arguments produce `bad_args` with exit 1.

```json
{"type":"stage","label":"download"}
{"type":"progress","label":"download","value":0.42,"bytes_done":420,"bytes_total":1000}
{"type":"progress","label":"planning","value":0.5,"done":16,"total":32}
{"type":"progress","label":"diffusion","value":0.125,"done":1,"total":8}
{"type":"result","ok":true,"output":"/absolute/path.md","title":"Video title"}
{"type":"result","ok":false,"code":"no_provider","error":"Choose a ready provider…"}
```

`value` is 0…1, or `null` for unknown-size progress; labels identify the phase
and progress can restart between phases/takes. Batch events include zero-based
`item` and original `input`. Each item emits a result, followed by a final summary
(`ok`, `items`, `succeeded`, `failed`; `code: batch_failed` if any failed).
Compose emits a result per zero-based `take` with `metadata` and `lyric_check`,
then a summary with `takes`. Do not assume that the first result is the final
one: consume until process exit. Result objects are additive; callers should
ignore unknown fields. `transcribe` returns output(s), format(s), engine,
duration, processing seconds, entries, title, source URL/duration and start.
No success is reported for a members-only preview served as a complete video.

标准输出仅含 JSON 行，日志写 stderr。`value` 为 0…1 或未知总量时的 `null`。
批处理逐项返回结果，最后汇总；作曲逐版本返回，再汇总。必须读到进程退出，
不要把第一条 result 当作最终结果。字段会扩展，调用方应忽略未知字段。

| Stable `code` | Meaning / 含义 |
| --- | --- |
| `bad_args`, `not_found`, `permission_denied`, `disk_full`, `failed`, `cancelled` | Arguments, paths, OS access/storage, other failure, SIGINT / 参数、路径、权限、磁盘、其他失败、取消。 |
| `no_model`, `tts_missing`, `no_speech`, `decode`, `locale_unsupported`, `unavailable`, `memory`, `model_busy` | Model, decoding, Apple locale/availability or admission / 模型、解码、系统语言或内存准入。 |
| `missing`, `unsupported`, `playlist`, `live`, `private`, `login`, `geo`, `unavailable`, `no_audio`, `blocked`, `network`, `other` | Unchanged `links.LinkError` codes (including Bilibili membership/preview → `login`) / 原有网址错误码，会员预览为 `login`。 |
| `no_provider`, `empty`, `too_long`, `invalid`, `failed` | Notes provider/configuration, empty/oversized input, invalid provider response, provider failure / 笔记服务、输入及响应错误。 |
| `ffmpeg_missing`, `burn_no_video`, `burn_failed`, `bad_transcript` | Subtitle dependency, video, encoding or transcript import / 字幕依赖、视频、编码或文稿导入。 |
| `bad_reference`, `transcript_required` | Unusable clone clip or missing reference text / 克隆参考音频或文稿问题。 |
| `music:memory`, `music:memory_low`, `music:stalled`, `music:load_failed`, `music:failed`, `worker_stopped`, `bad_take` | Same Compose admission/abort causes as the app; memory errors include `free_percent` / 同应用的作曲准入和中止原因。 |
| `batch_failed`, `notes_failed` | At least one item or optional notes failed; inspect `items` / 至少一项失败，查看逐项结果。 |

## Studio capability audit / 工作室功能对照

Inspected `studio_page.py`, every module in `ui/studio/`, Models and AI Proofread,
plus the core translation APIs. All processing and persisted-library operations
are exposed above. The following interactive affordances intentionally remain
in the GUI; their data is available to the CLI caller:

已检查工作室全部模块、模型页、AI 校对页及翻译核心 API。处理与库管理均已提供命令。
以下交互保留在 GUI，相关数据仍可由调用方读取：

| Studio capability | CLI mapping or reason / 命令或未提供的原因 |
| --- | --- |
| Drop/pick/paste files and share links, model/language choice, speakers, timestamps, exports | `transcribe`, flags and five formats. / 输入参数与五种导出格式。 |
| Batch formats/destination, notes per item, history | `transcribe --out-dir --formats --notes --save-history`. / 批量导出、逐项笔记和历史。 |
| Search/open/rename/delete history; rename speakers; save/copy notes | `history` CRUD/edit/export; `notes --out`, `history show` includes notes. / 历史管理及笔记输出。 |
| Burn/selectable subtitles | `burn` and `--soft`, existing or newly transcribed cues. / 烧录或软字幕。 |
| Speak text/language/speed, built-in and My voices, audio formats | `speak`, `voices add/list/rename/edit/remove`. / 朗读及声音库。 |
| Record a cloning reference with the microphone | Import a recording with `voices add`; headless commands do not start capture or request microphone permission. / 导入录音；不在无界面命令里启动麦克风或请求权限。 |
| Compose song/BGM, tagged lyrics, sung text suggestions/overrides/reset/remember, model/planner/length/BPM/language/takes/seed, artist advisory | `compose` and `compose lyrics`. / 作曲及演唱发音管理。 |
| Take lyric check/retry/details, another take, full/line-range repaint, export/delete | `compose check/another/repaint/show/export/delete`; check returns per-line ranges. / 逐行检查结果和时间范围支持调用方重绘。 |
| Engine/model downloads and cancellation | `models list/download/activate`; SIGINT cancels active work. / 模型管理与 SIGINT。 |
| Speech/text translation and Review edits | `translate INPUT` / `--text` / `--text-file`; caller reviews/edits text before translation. The confirmation popup is GUI-only. / 调用方审阅文字，确认弹窗仅在 GUI。 |
| Playback/preview voices or takes, seek/play one lyric line | Output audio and check ranges are returned; caller owns playback. CLI does not open a player/window. / 返回音频与范围，由调用方播放。 |
| Clipboard buttons, reveal/export dialogs, expandable panels, section insertion menu, format checkboxes, select-all | Files/JSON/flags replace these widgets; caller can use `pbcopy`, editors and its own UI. / 用文件、JSON、参数替代控件。 |
| Add/remove/cancel/retry individual live queue rows during a running batch | Batch argv is a fixed job; rerun chosen inputs, or SIGINT the job. No interactive queue protocol. / 批处理输入固定，可重跑选定项或中止整个任务。 |
| Warm-up/idle unload, elapsed/ETA display, persistent last UI choices | Workers unload on exit; stage/progress/timing fields let callers display status. CLI changes defaults only via explicit management. / 进程退出即卸载；调用方显示状态，显式管理才保存默认选择。 |

Neural TTS and translation also use the shared core job lease and check free
memory before loading. Resident GUI heaps still count against Compose admission.
Shared dictation priority and alternate-model slots coordinate CLI v3 with an app
running this version of the code. Older installed apps have no shared-priority
signal; updating/restarting the app is required for cross-process yielding.
Activation updates settings on disk and cannot hot-swap an older GUI's resident
model. No inference uses the benchmark lock automatically: that machine-specific
lock belongs to the test harness, not installed ThunderTalk.

跨进程优先级需应用也运行此版本；旧安装包没有共享信号，需更新并重启。
激活写入磁盘配置，不热切换旧 GUI 的驻留模型。测试机专用的 benchmark 锁不属于产品。

## MCP

`python -m thundertalk.mcp.server` retains existing API tools and adds thin local
CLI wrappers: `transcribe_file` (also URLs), `transcribe_inputs`, `meeting_notes`,
`subtitle_video`, `translate_local`, `compose_music`, `speak_text`,
`list_local_voices`, `manage_local_voice`, `transcript_history`, `manage_models`,
`local_status`. Partial batch/Compose results are returned intact even when `ok` is false.
`studio_command(command, arguments)` exposes every CLI option
as an argv array without shell interpolation. Set `THUNDERTALK_BIN` to the app
binary; otherwise the current Python interpreter is used.

MCP 保留原 API 工具，新增本地 CLI 包装；`studio_command` 可传递所有命令参数，
不经过 shell。`THUNDERTALK_BIN` 可指定打包程序，默认使用当前 Python。
