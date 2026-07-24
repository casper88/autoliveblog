# Security policy

If you find a security issue (for example, a way to leak API keys or reach the local server from outside), please report it privately via GitHub Security Advisories ("Report a vulnerability" on the repo's Security tab) instead of opening a public issue.

Notes on the threat model:

- The web server binds to `127.0.0.1` only and has no authentication; do not expose the port to the network as-is.
- All secrets live in `.env`, which is git-ignored. Never commit it.
- The Telegram bot only responds to chat IDs listed in `TELEGRAM_CHAT_ID`.
