# autoliveblog

[![CI](https://github.com/casper88/autoliveblog/actions/workflows/ci.yml/badge.svg)](https://github.com/casper88/autoliveblog/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

AI livestream and video summarizer for YouTube — real-time rolling summaries with on-screen visual analysis, catch-up mode, channel subscriptions, a Telegram bot, and daily digests.

[繁體中文說明](README.zh-TW.md)

![autoliveblog web UI — live watch of Al Jazeera English with rolling topic summary](docs/screenshot-web.png)

## What it does

Point it at a YouTube live stream and it watches for you: every few minutes it listens to the audio, tracks topic changes, and pushes updates to Telegram. When the host refers to something on screen ("look at this chart"), the model itself requests screenshots of that moment and reads the on-screen numbers. Missed the start? One tap catches up from the beginning of the stream, then continues live. When the stream ends, you get a full report.

<img src="docs/screenshot-telegram.png" width="380" alt="Telegram bot pushing a live topic update with inline action buttons">

Every push carries inline buttons, so you never have to remember which job to check or stop.

## Features

- **Live mode** — rolling topic summaries every N minutes, auto-reconnect, stall watchdog, final report on stream end
- **Smart re-look** — the model requests screenshots at specific moments and reads tickers, charts, and captions; important frames are kept and attached to notifications
- **Catch-up mode** (`--from-start`) — summarize from the beginning of a stream, then continue live seamlessly
- **VOD / podcast mode** — subtitles first (free and fast), audio understanding as fallback, map-reduce for long audio
- **Subscriptions** — get notified when a channel goes live; start summarizing with one tap
- **Telegram bot** — inline buttons, instant status, content Q&A, semantic keyword alerts
- **Daily digest** — all of today's summaries merged into one report
- **Cross-video Q&A** — ask questions across your entire summary history
- **Per-channel glossary** — fixes homophone errors in names and tickers, biases speech recognition
- **Dual engine** — Gemini first (free tier), automatic failover to OpenAI on quota, with a cost guard against surprise spending
- **Web UI** — job cards with live timeline, embedded player, history browser, usage and cost meter
- **Self-healing** — login autostart, health-check watchdog, auto-restart with job recovery, weekly yt-dlp auto-update

**Platforms.** The capture pipeline runs on yt-dlp, so any site it supports can be summarized. Platform-specific behaviour (go-live checks, timestamp deep links, the embedded player) lives in `autoliveblog/platforms.py`; YouTube is fully wired, Twitch and Kick are configured, and anything else falls back to a generic adapter that still summarizes but skips deep links. Adding a platform is one entry in that table. Windows-first launchers; the Python core is cross-platform.

## Install

1. Python 3.11+, then:
   ```
   pip install -r requirements.txt
   ```
2. ffmpeg (required for live mode): `winget install Gyan.FFmpeg.Essentials`
3. deno (recommended for yt-dlp YouTube extraction): `winget install DenoLand.Deno`
4. Copy `.env.example` to `.env` and fill in:
   - `GEMINI_API_KEY` (free at aistudio.google.com/apikey)
   - `OPENAI_API_KEY` (optional, fallback engine)
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (optional, via @BotFather)
5. If `python` is not your Python 3.11+ interpreter, set the `AUTOLIVEBLOG_PYTHON` environment variable to the full path of the right one.

## Usage

```
web.bat                        Web UI at http://127.0.0.1:8766 (includes the Telegram bot)
alb.bat "<url>"                One-shot summary (auto-detects live vs. video)
alb.bat "<url>" --smart        Live watch with smart screenshot analysis
alb.bat "<url>" --from-start   Catch up from the beginning of a live stream
alb-bg.bat "<url>"             Same as alb.bat, in a minimized background window
```

Autostart at login (with the self-healing watchdog): put a shortcut to `watchdog.vbs` into the Startup folder (`Win+R` then `shell:startup`).

### Telegram commands

`/watch <url>`, `/now`, `/ask <question>`, `/stop`, `/jobs`, `/history`, `/sub <channel-url>` (go-live notification with a one-tap start button), `/go <id>`, `/subs`, `/pause <id>`, `/resume <id>`, `/unsub <id>`, `/digest`, `/askall <question>`, `/glossary <channel> <terms>`

### Configuration (environment variables, all optional)

| Variable | Default | Description |
|---|---|---|
| `AUTOLIVEBLOG_PROVIDER` | auto | Engine: `auto` (Gemini then OpenAI), `gemini`, or `openai` |
| `AUTOLIVEBLOG_MODEL` | gemini-2.5-flash | Gemini model |
| `AUTOLIVEBLOG_LANG` | 繁體中文 | Output language of the summaries |
| `AUTOLIVEBLOG_CHUNK_SECONDS` | 180 | Live summary interval in seconds |
| `AUTOLIVEBLOG_DIGEST_TIME` | 12:30 | Daily digest time (empty to disable) |
| `AUTOLIVEBLOG_MAX_AUTO_SPEND_USD` | 0.25 | Per-job OpenAI transcription spending cap |
| `AUTOLIVEBLOG_STT_PROVIDER` | openai | `local` uses faster-whisper (free) |
| `AUTOLIVEBLOG_STT_LANG` | (empty) | Transcription language; empty = auto-detect |
| `AUTOLIVEBLOG_SUB_POLL_SECONDS` | 300 | Subscription go-live poll interval |
| `AUTOLIVEBLOG_OBSIDIAN_VAULT` | (empty) | Auto-copy summaries into an Obsidian vault |

Advanced: `AUTOLIVEBLOG_OPENAI_MODEL`, `AUTOLIVEBLOG_OPENAI_STT_MODEL`, `AUTOLIVEBLOG_OUTPUT_DIR` — see `.env.example`.

## Notes

- The Gemini free tier currently allows roughly 20 requests per day; for daily use add an OpenAI key (about $0.19 per hour of live watching) or a paid Gemini tier.
- YouTube rate-limits aggressive request patterns. Global throttling is built in, but avoid subscribing to a large number of channels at once.
- Summaries faithfully reflect what a program says, including investment claims. Keep your own judgment.

## License

MIT
