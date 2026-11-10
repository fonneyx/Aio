# Contributing to Aio

Thanks for helping. Small, focused pull requests are the easiest to review.

## Reporting a broken link

Most "it doesn't download" reports are fixed by a newer yt-dlp, so first try `pip install -U aio-dl`.
If it still fails, open an issue with:

- a **public** link that shows the problem (never one that needs your login),
- the output of `aio-dl -v <link>`,
- `aio-dl --version`.

## Development setup

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/fonneyx/Aio.git && cd Aio
uv sync --all-packages
uv run pytest
uv run ruff check .
uv run aio-server        # web app on http://127.0.0.1:8000
```

The repository is a uv workspace with two packages:

- `core/` is **aio-dl**, the library and CLI. Download logic lives here, so the CLI, web app and bot all behave the same.
- `server/` is **aio-server**: the HTTP API, web app and Telegram bot. It holds no download logic of its own.

## Guidelines

- Tests must pass offline. Stub yt-dlp and gallery-dl as the existing tests do; don't add tests that hit real sites.
- Error messages are shown to end users: say what happened and what to do, in plain words.
- The web app is a single HTML file with no build step. Keep it that way.
- Run `uv run ruff check --fix .` before committing.

## Releasing (maintainers)

1. Bump `__version__` in `core/src/aio_dl/__init__.py` (and `server/aio_server/__init__.py`).
2. Commit, then tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
3. The release workflow publishes aio-dl to PyPI, pushes the Docker image to `ghcr.io/fonneyx/aio`, and creates the GitHub release.
