# Contributing

Thanks for your interest in autoliveblog!

## Development setup

```bash
git clone https://github.com/casper88/autoliveblog
cd autoliveblog
pip install -r requirements.txt
pip install pytest
cp .env.example .env   # fill in at least GEMINI_API_KEY
```

Run the web UI with `python -m uvicorn autoliveblog.web.server:app --port 8766`, or the CLI with `python -m autoliveblog "<url>"`.

## Running tests

```bash
pytest -q
```

Tests cover the pure functions (subtitle parsing, glossary matching, cost math). Please add tests for any new pure logic.

## Pull requests

- Keep PRs focused on one change.
- Make sure `pytest` and `python -m compileall autoliveblog` pass (CI runs both on Linux and Windows).
- Comments in the codebase are mostly Traditional Chinese; either language is fine for new code and PR descriptions.
- Never commit secrets, personal data, or files listed in `.gitignore` (`.env`, `summaries/`, `subscriptions.json`, ...).

## Reporting issues

Use the issue templates. For anything YouTube-related, include the yt-dlp version (`yt-dlp --version`) — most extraction breakage is fixed by upgrading yt-dlp first.
