"""Shared Garmin Connect and official-export ingestion pipeline."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import logging
import mimetypes
import os
import re
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import requests
from garminconnect import Garmin

from .config import Config
from .converter import ConversionError, fit_to_gpx
from .manifest import Manifest

LOGGER = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {".fit", ".gpx", ".tcx", ".kml", ".kmz"}


class SyncError(RuntimeError):
    """Raised when a source or upload cannot be completed safely."""


class WandererUploadError(SyncError):
    """A safe, classified error from the Wanderer upload endpoint."""

    def __init__(
        self,
        kind: str,
        *,
        retryable: bool,
        attempts: int,
        status_code: int | None = None,
    ) -> None:
        self.kind = kind
        self.retryable = retryable
        self.attempts = attempts
        self.status_code = status_code
        detail = f"Wanderer upload failed: {kind}"
        if status_code is not None:
            detail += f" HTTP {status_code}"
        detail += f" after {attempts} attempt(s)"
        super().__init__(detail)


class WandererUploader:
    """Upload one route file through Wanderer's token-authenticated API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int,
        *,
        upload_retries: int = 3,
        retry_backoff_seconds: int = 5,
        retry_max_backoff_seconds: int = 60,
    ) -> None:
        self.endpoint = f"{base_url}/api/v1/trail/upload"
        self.token = token
        self.timeout = (10, timeout)
        self.upload_retries = upload_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.retry_max_backoff_seconds = retry_max_backoff_seconds
        self.session = requests.Session()

    def upload(self, path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
        for attempt in range(self.upload_retries + 1):
            try:
                with path.open("rb") as file_handle:
                    response = self.session.put(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.token}"},
                        data={"ignoreDuplicates": "true", "name": path.name},
                        files={"file": (path.name, file_handle, content_type)},
                        timeout=self.timeout,
                    )
            except requests.RequestException as exc:
                retryable = isinstance(exc, (requests.Timeout, requests.ConnectionError))
                if retryable and attempt < self.upload_retries:
                    self._wait_before_retry(attempt, kind=exc.__class__.__name__)
                    continue
                raise WandererUploadError(
                    exc.__class__.__name__,
                    retryable=retryable,
                    attempts=attempt + 1,
                ) from exc

            if 200 <= response.status_code < 300:
                return

            retryable = response.status_code in retryable_statuses
            if retryable and attempt < self.upload_retries:
                self._wait_before_retry(
                    attempt,
                    kind="http_status",
                    status_code=response.status_code,
                    retry_after=response.headers.get("Retry-After"),
                )
                continue
            # Do not log response text: it can contain route names or server data.
            raise WandererUploadError(
                "http_status",
                retryable=retryable,
                attempts=attempt + 1,
                status_code=response.status_code,
            )

    def _wait_before_retry(
        self,
        attempt: int,
        *,
        kind: str,
        status_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        delay = self.retry_backoff_seconds * (2**attempt)
        if retry_after and retry_after.isdigit():
            delay = max(delay, int(retry_after))
        delay = min(delay, self.retry_max_backoff_seconds)
        LOGGER.warning(
            "Wanderer upload retrying: error=%s status=%s attempt=%d/%d delay=%ds",
            kind,
            status_code if status_code is not None else "none",
            attempt + 1,
            self.upload_retries + 1,
            delay,
        )
        if delay > 0:
            time.sleep(delay)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_name(value: str, fallback: str = "route") -> str:
    value = value.replace("\\", "/")
    name = PurePosixPath(value).name
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip(".")
    return name[:160] or fallback


def _logical_suffix(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".gz"):
        lower = lower[:-3]
    return Path(lower).suffix


def _supported(name: str) -> bool:
    return _logical_suffix(name) in SUPPORTED_SUFFIXES


def _unsafe_member(item: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(item.filename.replace("\\", "/"))
    mode = (item.external_attr >> 16) & 0xFFFF
    first_part = path.parts[0] if path.parts else ""
    return (
        path.is_absolute()
        or ".." in path.parts
        or item.filename.startswith("/")
        or ":" in first_part
        or stat.S_ISLNK(mode)
    )


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    try:
        with temporary.open("wb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_limited(source: Any, destination: Path, limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    try:
        with temporary.open("wb") as output:
            os.chmod(temporary, 0o600)
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > limit:
                    raise SyncError(f"input file exceeds the {limit} byte limit")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest(), size


def _extract_original_activity(
    content: bytes, max_bytes: int, max_members: int, max_uncompressed_bytes: int
) -> tuple[str, bytes]:
    """Return a supported route member from Garmin's original ZIP response."""
    if not content.startswith(b"PK"):
        raise SyncError("Garmin original activity response was not a ZIP archive")
    with tempfile.TemporaryDirectory(prefix="wanderer-garmin-") as temporary:
        archive = Path(temporary) / "activity.zip"
        archive.write_bytes(content)
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            if len(entries) > max_members:
                raise SyncError("Garmin original activity archive exceeds the member limit")
            total_uncompressed = sum(item.file_size for item in entries if not item.is_dir())
            if total_uncompressed > max_uncompressed_bytes:
                raise SyncError("Garmin original activity archive exceeds the uncompressed-size limit")
            if any(_unsafe_member(item) for item in entries):
                raise SyncError("Garmin original activity archive contains an unsafe path")
            candidates = [
                item
                for item in entries
                if not item.is_dir() and _supported(item.filename)
            ]
            if not candidates:
                raise SyncError("Garmin original activity archive contains no supported route file")
            candidates.sort(key=lambda item: (0 if _logical_suffix(item.filename) == ".fit" else 1, item.filename))
            item = candidates[0]
            if item.file_size > max_bytes:
                raise SyncError("Garmin original activity file exceeds the configured size limit")
            with zipped.open(item) as handle:
                content = handle.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise SyncError("Garmin original activity file exceeds the configured size limit")
            return _logical_suffix(item.filename), content


def _activity_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("activityList", "activities"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class Pipeline:
    """Run both configured sources through one idempotent upload path."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.config.prepare_directories()
        self.manifest = Manifest(config.manifest_path)
        self.lock_path = config.manifest_path.with_name("sync.lock")
        self.uploader = WandererUploader(
            config.wanderer_url,
            config.wanderer_token,
            config.request_timeout_seconds,
            upload_retries=config.upload_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
            retry_max_backoff_seconds=config.retry_max_backoff_seconds,
        )
        self.work_dir = config.data_dir / "work"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.work_dir.chmod(0o700)
        except OSError:
            pass
        self.uploaded = 0
        self.skipped = 0
        self.failures: list[str] = []

    def run(self) -> int:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SyncError("another synchronizer run is already active") from exc
            try:
                return self._run_locked()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _run_locked(self) -> int:
        if "official" in self.config.sources:
            self._run_official_exports()
        if "garmin" in self.config.sources:
            self._run_garmin_connect()
        self.manifest.save()
        if self.failures:
            raise SyncError(f"{len(self.failures)} route(s) failed; successful routes were retained in the manifest")
        LOGGER.info("sync complete: uploaded=%d skipped=%d", self.uploaded, self.skipped)
        return self.uploaded

    def _run_official_exports(self) -> None:
        if not self.config.inbox_dir.exists():
            LOGGER.info("official export source: inbox is empty")
            return
        inputs = sorted(
            path
            for path in self.config.inbox_dir.rglob("*")
            if not path.is_symlink()
            and path.is_file()
            and (_supported(path.name) or path.suffix.lower() == ".zip")
        )
        LOGGER.info("official export source: found %d input file(s)", len(inputs))
        for path in inputs:
            try:
                if path.suffix.lower() == ".zip":
                    self._process_zip(path)
                else:
                    digest = _sha256(path)
                    self._process_route(path, f"official:file:{digest}", "official")
            except Exception as exc:  # keep processing independent export members
                LOGGER.error("official export item failed: %s", exc.__class__.__name__)
                self.failures.append(str(path))

    def _process_zip(self, path: Path) -> None:
        zip_digest = _sha256(path)
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > self.config.max_zip_members:
                    raise SyncError("official export ZIP exceeds the member limit")
                if any(_unsafe_member(item) for item in entries):
                    raise SyncError("official export ZIP contains an unsafe path")
                members = [item for item in entries if not item.is_dir() and _supported(item.filename)]
                total_uncompressed = sum(item.file_size for item in entries if not item.is_dir())
                if total_uncompressed > self.config.max_zip_uncompressed_bytes:
                    raise SyncError("official export ZIP exceeds the uncompressed-size limit")
                for item in members:
                    key = f"official:zip:{zip_digest}:{item.filename}"
                    if self.manifest.is_uploaded(key):
                        self.skipped += 1
                        continue
                    member_digest = hashlib.sha256(item.filename.encode("utf-8")).hexdigest()[:12]
                    archive_name = f"{zip_digest[:16]}-{member_digest}-{_safe_name(item.filename)}"
                    destination = self.config.archive_dir / "official" / archive_name
                    with archive.open(item) as source:
                        _copy_limited(source, destination, self.config.max_file_bytes)
                    self._process_route(destination, key, "official")
        except zipfile.BadZipFile as exc:
            raise SyncError(f"official export is not a valid ZIP: {path.name}") from exc

    def _run_garmin_connect(self) -> None:
        assert self.config.garmin_email is not None
        assert self.config.garmin_password is not None
        LOGGER.info("Garmin Connect source: logging in with persisted token state")
        client = Garmin(self.config.garmin_email, self.config.garmin_password, return_on_mfa=True)
        mfa_status, _ = client.login(str(self.config.token_store))
        if mfa_status:
            raise SyncError(
                "Garmin Connect requires MFA; bootstrap the token store with a trusted interactive workflow"
            )
        total = client.count_activities()
        LOGGER.info("Garmin Connect source: account reports %d activities", total)
        start = 0
        for page_number in range(self.config.garmin_max_pages):
            if total and start >= total:
                return
            payload = client.get_activities(start=start, limit=self.config.garmin_page_size)
            activities = _activity_list(payload)
            if not activities:
                return
            for activity in activities:
                try:
                    self._process_garmin_activity(client, activity)
                except Exception as exc:
                    LOGGER.error("Garmin activity failed: error=%s", exc.__class__.__name__)
                    self.failures.append("garmin")
            start += len(activities)
            if len(activities) < self.config.garmin_page_size:
                return
        raise SyncError("Garmin activity pagination reached GARMIN_MAX_PAGES before completion")

    def _process_garmin_activity(self, client: Garmin, activity: dict[str, Any]) -> None:
        activity_id = activity.get("activityId") or activity.get("activity_id")
        if activity_id is None or not str(activity_id).isdigit():
            LOGGER.warning("Garmin activity without a numeric ID was skipped")
            return
        key = f"garmin:{activity_id}"
        if self.manifest.is_uploaded(key):
            self.skipped += 1
            return
        if self.config.garmin_download_format == "gpx":
            content = client.download_activity(str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.GPX)
            suffix = ".gpx"
        else:
            content = client.download_activity(str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
            suffix, content = _extract_original_activity(
                content,
                self.config.max_file_bytes,
                self.config.max_zip_members,
                self.config.max_zip_uncompressed_bytes,
            )
        if len(content) > self.config.max_file_bytes:
            raise SyncError(f"Garmin activity {activity_id} exceeds the configured size limit")
        destination = self.config.archive_dir / "garmin" / f"{activity_id}{suffix}"
        _write_private(destination, content)
        self._process_route(destination, key, "garmin")

    def _process_route(self, source: Path, key: str, source_name: str) -> None:
        digest = _sha256(source)
        if self.manifest.has_uploaded(key, digest):
            self.skipped += 1
            return
        logical_suffix = _logical_suffix(source.name)
        upload_path = source
        temporary_paths: list[Path] = []
        try:
            if source.name.lower().endswith(".gz"):
                temporary = self.work_dir / f"{digest}.uncompressed{logical_suffix}"
                temporary_paths.append(temporary)
                with gzip.open(source, "rb") as input_handle:
                    _copy_limited(input_handle, temporary, self.config.max_file_bytes)
                upload_path = temporary
            if logical_suffix == ".fit" and self.config.fit_mode == "gpx":
                temporary = self.work_dir / f"{digest}.gpx"
                temporary_paths.append(temporary)
                fit_to_gpx(upload_path, temporary)
                upload_path = temporary
            self.uploader.upload(upload_path)
            self.manifest.mark_uploaded(key, digest, filename=upload_path.name, source=source_name)
            self.manifest.save()
            self.uploaded += 1
        except (ConversionError, SyncError, OSError) as exc:
            if isinstance(exc, WandererUploadError):
                LOGGER.error(
                    "route processing failed: error=%s status=%s attempts=%d retryable=%s",
                    exc.kind,
                    exc.status_code if exc.status_code is not None else "none",
                    exc.attempts,
                    exc.retryable,
                )
            else:
                LOGGER.error("route processing failed: error=%s", exc.__class__.__name__)
            self.failures.append(str(source))
        finally:
            for temporary in temporary_paths:
                temporary.unlink(missing_ok=True)
