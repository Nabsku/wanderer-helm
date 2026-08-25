"""Configuration for the optional Wanderer Garmin synchronizer."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Raised when an environment value is invalid or incomplete."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean")


def _integer(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        bound = f"at least {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ConfigError(f"{name} must be {bound}")
    return parsed


def _url(name: str) -> str:
    value = _required(name).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ConfigError(f"{name} must be an HTTP(S) URL without embedded credentials")
    return value


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    sources: frozenset[str]
    wanderer_url: str
    wanderer_token: str
    data_dir: Path
    inbox_dir: Path
    archive_dir: Path
    manifest_path: Path
    token_store: Path
    fit_mode: str
    garmin_email: str | None
    garmin_password: str | None
    garmin_download_format: str
    garmin_page_size: int
    garmin_max_pages: int
    max_file_bytes: int
    max_zip_members: int
    max_zip_uncompressed_bytes: int
    request_timeout_seconds: int
    upload_retries: int = 3
    retry_backoff_seconds: int = 5
    retry_max_backoff_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Config":
        sources: set[str] = set()
        raw_sources = os.environ.get("SYNC_SOURCES", "").strip()
        if raw_sources:
            for source in raw_sources.split(","):
                normalized = source.strip().lower()
                if normalized not in {"garmin", "official"}:
                    raise ConfigError(f"SYNC_SOURCES contains unsupported source: {source}")
                sources.add(normalized)
        if not sources:
            raise ConfigError("SYNC_SOURCES must enable at least one source")

        fit_mode = os.environ.get("FIT_MODE", "preserve").strip().lower()
        if fit_mode not in {"preserve", "gpx"}:
            raise ConfigError("FIT_MODE must be preserve or gpx")

        download_format = os.environ.get("GARMIN_DOWNLOAD_FORMAT", "original").strip().lower()
        if download_format not in {"original", "gpx"}:
            raise ConfigError("GARMIN_DOWNLOAD_FORMAT must be original or gpx")

        garmin_email = os.environ.get("GARMIN_EMAIL", "").strip() or None
        garmin_password = os.environ.get("GARMIN_PASSWORD", "") or None
        if "garmin" in sources and (not garmin_email or not garmin_password):
            raise ConfigError("GARMIN_EMAIL and GARMIN_PASSWORD are required for the Garmin source")

        data_dir = Path(os.environ.get("SYNC_DATA_DIR", "/data"))
        retry_backoff_seconds = _integer("RETRY_BACKOFF_SECONDS", 5, minimum=0, maximum=300)
        retry_max_backoff_seconds = _integer("RETRY_MAX_BACKOFF_SECONDS", 60, minimum=0, maximum=3600)
        if retry_max_backoff_seconds < retry_backoff_seconds:
            raise ConfigError("RETRY_MAX_BACKOFF_SECONDS must be at least RETRY_BACKOFF_SECONDS")

        return cls(
            sources=frozenset(sources),
            wanderer_url=_url("WANDERER_URL"),
            wanderer_token=_required("WANDERER_API_TOKEN"),
            data_dir=data_dir,
            inbox_dir=Path(os.environ.get("SYNC_INBOX_DIR", str(data_dir / "inbox"))),
            archive_dir=Path(os.environ.get("SYNC_ARCHIVE_DIR", str(data_dir / "archive"))),
            manifest_path=Path(os.environ.get("SYNC_MANIFEST", str(data_dir / "state" / "manifest.json"))),
            token_store=Path(os.environ.get("GARMINTOKENS", str(data_dir / "state" / "garmin_tokens.json"))),
            fit_mode=fit_mode,
            garmin_email=garmin_email,
            garmin_password=garmin_password,
            garmin_download_format=download_format,
            garmin_page_size=_integer("GARMIN_PAGE_SIZE", 1000, minimum=1, maximum=1000),
            garmin_max_pages=_integer("GARMIN_MAX_PAGES", 2000, minimum=1, maximum=10000),
            max_file_bytes=_integer("MAX_FILE_BYTES", 256 * 1024 * 1024, minimum=1),
            max_zip_members=_integer("MAX_ZIP_MEMBERS", 10000, minimum=1),
            max_zip_uncompressed_bytes=_integer(
                "MAX_ZIP_UNCOMPRESSED_BYTES", 4 * 1024 * 1024 * 1024, minimum=1
            ),
            request_timeout_seconds=_integer("REQUEST_TIMEOUT_SECONDS", 120, minimum=1, maximum=3600),
            upload_retries=_integer("UPLOAD_RETRIES", 3, minimum=0, maximum=10),
            retry_backoff_seconds=retry_backoff_seconds,
            retry_max_backoff_seconds=retry_max_backoff_seconds,
        )

    def prepare_directories(self) -> None:
        for directory in {
            self.data_dir,
            self.inbox_dir,
            self.archive_dir,
            self.manifest_path.parent,
            self.token_store.parent,
        }:
            directory.mkdir(parents=True, exist_ok=True)
            try:
                directory.chmod(0o700)
            except OSError:
                pass
