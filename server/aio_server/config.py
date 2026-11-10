"""Runtime settings, read from AIO_* environment variables (and server/.env, which git ignores)."""

import os
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
# In a source checkout files are staged in server/storage; an installed package keeps them in the home folder.
DEFAULT_STORAGE = SERVER_DIR / "storage" if (SERVER_DIR / "pyproject.toml").is_file() else Path.home() / ".aio" / "storage"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    # Where finished files are staged until they expire.
    storage_dir: Path = DEFAULT_STORAGE
    file_ttl_hours: float = 24.0
    # Number of jobs processed in parallel.
    workers: int = 2
    # Directory or binary path for ffmpeg. Empty: use PATH, else the bundled static build.
    ffmpeg: str = ""
    # Netscape cookies.txt, needed for sites that require a login (Instagram, etc.).
    cookies_file: str = ""
    # Outbound proxy for extraction, e.g. http://user:pass@host:port
    proxy: str = ""
    # Telegram bot token from @BotFather. Keep it in server/.env, never in code.
    telegram_token: str = ""
    # "user:password" to require HTTP Basic auth. Set it whenever the server is reachable beyond localhost.
    auth: str = ""
    # Telegram user IDs allowed to use the bot, comma-separated. Empty: anyone who finds it.
    telegram_users: frozenset[int] = frozenset()

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file(SERVER_DIR / ".env")
        env = os.environ.get
        return cls(
            host=env("AIO_HOST", cls.host),
            port=int(env("AIO_PORT", cls.port)),
            storage_dir=Path(env("AIO_STORAGE_DIR", str(cls.storage_dir))),
            file_ttl_hours=float(env("AIO_FILE_TTL_HOURS", cls.file_ttl_hours)),
            workers=int(env("AIO_WORKERS", cls.workers)),
            ffmpeg=env("AIO_FFMPEG", cls.ffmpeg),
            cookies_file=env("AIO_COOKIES_FILE", cls.cookies_file),
            proxy=env("AIO_PROXY", cls.proxy),
            telegram_token=env("AIO_TELEGRAM_TOKEN", cls.telegram_token),
            auth=env("AIO_AUTH", cls.auth),
            telegram_users=frozenset(int(u) for u in env("AIO_TELEGRAM_USERS", "").replace(" ", "").split(",") if u),
        )

    @property
    def file_ttl_seconds(self) -> float:
        return self.file_ttl_hours * 3600


def load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines into the environment. Variables already set win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
