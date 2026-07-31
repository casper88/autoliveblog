# Changelog

## Unreleased

- The web UI, Telegram bot, CLI and summary headings are now English by default, with a Traditional Chinese translation selected by `AUTOLIVEBLOG_UI_LANG=zh-TW`. All user-facing text moved into a message catalog at `autoliveblog/i18n.py`.
- **Behaviour change:** `AUTOLIVEBLOG_LANG` (the language summaries are written in) used to default to 繁體中文 for everyone. It now follows `AUTOLIVEBLOG_UI_LANG`, so an installation that sets neither variable produces English summaries. Set `AUTOLIVEBLOG_LANG=繁體中文` to keep the old behaviour.
- Fixed: prompts handed the model a Chinese Markdown template, which it copied verbatim — English summaries came back with Chinese section headings. The daily digest also assumed a finance framing regardless of the source material.
- Fixed: `/watch <url> catchup` in the Telegram bot, which the English help text documented but the parser did not accept.
- `.env.example` is in English and no longer suggests a Gemini model that has been retired for new projects.

## 0.1.0 — 2026-07-24

First public release.

- Live mode: rolling topic summaries, smart frame re-look, stall watchdog, auto-reconnect, final report
- Catch-up mode (`--from-start`)
- VOD / podcast mode with subtitle-first strategy and long-audio map-reduce
- Subscriptions with go-live notification and one-tap start
- Telegram bot with inline buttons, `/now`, `/ask`, semantic keyword alerts
- Daily digest and cross-video Q&A
- Per-channel glossary with speech-recognition biasing
- Dual engine (Gemini / OpenAI) with auto-failover and spending cap
- Web UI with live timeline, embedded player, history browser, usage meter
- Self-healing watchdog: autostart, health checks, job recovery, weekly yt-dlp update
