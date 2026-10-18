import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str
    port: int
    secret_key: str | None
    disable_scheduler: bool

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'jobradar.db'}"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@lru_cache
def get_settings() -> Settings:
    _load_dotenv(Path.cwd() / ".env")
    data_dir = Path(os.environ.get("JOBRADAR_DATA_DIR", "./data")).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(exist_ok=True)
    return Settings(
        data_dir=data_dir,
        host=os.environ.get("JOBRADAR_HOST", "127.0.0.1"),
        port=int(os.environ.get("JOBRADAR_PORT", "8765")),
        secret_key=os.environ.get("JOBRADAR_SECRET_KEY") or None,
        disable_scheduler=os.environ.get("JOBRADAR_DISABLE_SCHEDULER", "0") in ("1", "true", "yes"),
    )
