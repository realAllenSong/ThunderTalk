# UI/UX overhaul + repo audit (2026-09-28)

Scope: the desktop app's front end ("Voltage" design system, first-run flow,
live status) plus every real defect found while reading and running the code.
Nothing here was taken on faith — each finding below was reproduced or
measured, and each fix has a test or a recorded measurement.

## What changed for the user

| Area | Before | After |
|---|---|---|
| First run | Window opens on an empty Home that says "Ready to go" even with no model and no permissions | 4-step setup: welcome → permissions (polled live) → one-click download of the recommended model with real progress → try-it box |
| Status | Nowhere to see "can I dictate right now?" | Sidebar status card + Home hero, both driven by one `AppState` (ready / loading / error / setup needed / listening / transcribing) |
| Home | 3 stats, plain list | Live hero, 4 stats (words, speaking time, est. time saved, sessions), search, copy, per-entry delete, expand/collapse long text |
| Models | Silent multi-GB downloads, no cancel, button stays clickable | Per-row state machine: byte progress, Cancel, no double-start, large-download confirm, sizes that are true |
| Settings | Rows stale after a language switch | Every row re-labels live; keycap hotkey capture; Esc cancels; unsafe hotkeys rejected |
| Overlay | Sine-wave fake meter, no hotkey hint | Flat ink bar: real scrolling level meter, timer, hotkey chip; no fade/slide |
| Tray | Full-colour app icon, static menu | Monochrome template glyph (orange while recording), status line, Start/Stop item |
| Feedback | Modal dialogs / nothing | Non-blocking toasts |

## Visual direction: "Paper & Ink" (replaced the first pass, "Voltage")

The first pass (dark aurora backdrop, glass cards, orange gradient pills,
breathing glows, shimmer text, sliding highlight, count-up numbers) read as
generic AI-product styling, and its motion was decorative. It was replaced
wholesale with a quiet editorial look:

- **Surface:** warm paper canvas (`#FBFBFA`), bone sidebar (`#F3F1EC`), white
  flat cards with a 1px hairline, radius ≤ 10. No gradients, no glass, no glow.
- **Type:** serif headings and figures (Charter → Georgia; CJK falls through
  to Songti), system UI for body, monospace for keys, timestamps and captions.
  Text is never pure black (`#1F1E1B`).
- **Colour:** ink `#111` for primary actions and "on" toggles; orange
  (`#D9480F`) only for the live dot, the active-nav tick and focus; meaning is
  carried by muted pastel tags (green / blue / amber / red / orange).
- **Shape:** rectangular buttons (5–6px), keys drawn as physical keycaps,
  ruled lists instead of cards-inside-cards, ledger-style figures on Home,
  a centred column capped at 860px.
- **Motion policy:** only motion that carries information — the live input
  level, a spinner while something is loading, an indeterminate sweep while a
  download connects, the toggle's short slide. Page transitions, fades,
  pulses, count-ups and the sliding nav highlight were removed.
- **Theme is one file** (`ui/theme.py`); components read tokens rather than
  literals, so another palette is an edit there. The palette is light-only
  (`force_light()` pins Qt's colour scheme so a dark-mode Mac doesn't invert
  unstyled widgets).
- Not changed: the app icon (`assets/icon.png`, neon blue bolt on navy) still
  belongs to the previous look; the in-app mark is a flat ink bolt tile.

Colour rule: **ink = action**, **orange = live/focus (small doses)**,
**pastels = meaning**.

## Defects found and fixed

1. **12 stylesheets never parsed** (a `}}` inside a continuation line that was
   not an f-string) — sidebar border, inactive nav hover, About buttons and
   progress bar, Lab text edits, Models "Activate" / translator buttons, review
   popup combo + Replace button. `app.py` installed a handler that *dropped*
   every "Could not parse stylesheet" warning, which is why nobody saw it.
   Two more in `StyledDialog` (the confirm dialogs' Cancel/Accept buttons)
   were found by the new regression test. → `tests/test_qss_valid.py`
   builds the whole UI headlessly and fails on any such warning; the handler
   now only drops known-benign noise.
2. **`is_downloaded()` was `True` for every MLX model.** A fresh install showed
   "Activate"; the multi-GB download then happened invisibly inside
   `mlx_qwen3_asr.load_model()` with no progress. Now it checks the real HF
   cache (a dangling symlink from a half-written blob counts as *not*
   downloaded — a test caught my first version getting that wrong).
3. **Model sizes shown to users were wrong**, measured 2026-09-28:

   | Model | Listed | Actual download |
   |---|---|---|
   | Qwen3-ASR-0.6B MLX | 1200 MB | 1881 MB |
   | Qwen3-ASR-1.7B MLX | 3400 MB | 4703 MB |
   | MOSS-Transcribe-Diarize | 1700 MB | 1833 MB |
   | SenseVoice-Small | 241 MB | **1048 MB** → now the int8-only archive, 163 MB |
   | SeamlessM4T v2 | 8600 MB | **29,883 MB** → now filtered to safetensors, 9,258 MB |

   SeamlessM4T pulled ~20 GB of fairseq `.pt` checkpoints the transformers
   loader never reads. SenseVoice's `_find()` picks the first `*model*.onnx`,
   and the int8-only archive contains exactly the file it already selected.
4. **Downloads had no progress and no cancel** (curl output was captured,
   progress jumped 5 → 80 → 100), a cancelled/failed attempt left
   `models/<id>/` behind and the next click was short-circuited by
   `target.exists()` ("already downloaded" with nothing there), and the button
   stayed clickable so a second worker could start. Now: real byte progress,
   cancel (curl: ~3 s worst case in tests; HF: 1.3 s — it was 205 s with the
   Xet backend, whose progress callbacks are far too sparse, so Xet is turned
   off for these downloads; measured 35 MB/s on the test connection), cleanup
   on failure, one worker per model.
5. **Microphone permission check was dead code.** `pyobjc-framework-AVFoundation`
   is neither declared nor bundled, so `check_microphone()` returned
   "authorized" for everyone and a denied mic surfaced as "No speech
   detected". Now read through the ObjC runtime via ctypes (verified to return
   a real status; the *denied* branch itself could not be provoked in CI).
6. **Startup froze the window**: the last model was loaded synchronously on
   the UI thread. Now loaded on the same worker as a manual Activate.
7. **Overlay race**: `QTimer.singleShot(1500, hide)` from a finished result
   could hide the *next* recording's overlay. Single cancellable timer now
   (`tests/test_overlay.py`).
8. **Hotkey foot-guns**: pressing Esc while capturing *bound Esc*; a bare
   letter was accepted (and would start recording on every keystroke).
   Esc cancels; combos without a modifier (except F-keys / lone modifiers)
   are rejected with an explanation.
9. **Hard-coded strings**: overlay errors were English-only; the permission
   dialog was Chinese-only (and unreachable, see 5). All go through i18n.
10. **Hardware probe on the UI thread** (`system_profiler`, ~1 s) — moved to a
    worker.
11. Stale test (`_MAX_ENTRIES`, removed deliberately in b8a0bdd) → rewritten
    to assert the current behaviour.
12. Grammar bug "1 variants available".

New in `HistoryStore`: `remove(id)` as an append-only **tombstone** line (the
entry's own line is never rewritten). Caveat: a downgrade to an older build
would treat those lines as skipped and show the deleted entries again.

## Not done / needs a human

- **Not exercised here:** a real hotkey → record → transcribe → paste round
  trip (needs a microphone and the user's permissions), and the
  permission-*denied* branch. Both are unit-covered only at the seams.
- Accessibility granted *after* launch: NSEvent global monitors should start
  delivering without a restart, but that is macOS behaviour I could not
  confirm here — if it doesn't, onboarding step 2 should say "restart".
- "Hold to record" stays hidden (was already marked unstable in code).
- Cancel-recording-with-Esc is not implemented (needs a hotkey-listener change).
- Lab page: header/palette unified, internals untouched (1.9k lines).
- READMEs: only the three MLX size cells that were measured wrong were changed.
