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

## User-facing text

All text a user sees lives in `autoliveblog/i18n.py`, never inline in the code:

```python
from .i18n import t
t("bot.watching", title=job.title, job=job.id)
```

- English (`EN`) is the source of truth; add the key to `ZH_TW` as well. A key missing from a locale falls back to English, but a key missing from `EN` renders as the bare key name.
- Use named placeholders (`{title}`), never positional ones, and keep the same set of them in every locale.
- `tests/test_i18n.py` checks every `t()` call site against the catalog, so a forgotten key fails CI rather than reaching a user.
- Anything under `engine.*` must not contain the words the engine failover matches on (`quota`, `billing`, `429`, `UNAVAILABLE`, ...). Those markers have to come from the provider's own error text via `{err}`; a catalog string containing one would fake an engine state. There is a test for this too.
- Code comments and docstrings stay Traditional Chinese — they are not part of the translation layer.

## Pull requests

- Keep PRs focused on one change.
- Make sure `pytest` and `python -m compileall autoliveblog` pass (CI runs both on Linux and Windows).
- Comments in the codebase are mostly Traditional Chinese; either language is fine for new code and PR descriptions.
- Never commit secrets, personal data, or files listed in `.gitignore` (`.env`, `summaries/`, `subscriptions.json`, ...).

## Reporting issues

Use the issue templates. For anything YouTube-related, include the yt-dlp version (`yt-dlp --version`) — most extraction breakage is fixed by upgrading yt-dlp first.
