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
from urllib.parse import parse_qsl, urlparse

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
    """A safe, classified error from a Wanderer API request."""

    def __init__(
        self,
        kind: str,
        *,
        retryable: bool,
        attempts: int,
        status_code: int | None = None,
        operation: str = "upload",
    ) -> None:
        self.kind = kind
        self.retryable = retryable
        self.attempts = attempts
        self.status_code = status_code
        self.operation = operation
        detail = f"Wanderer {operation} failed: {kind}"
        if status_code is not None:
            detail += f" HTTP {status_code}"
        detail += f" after {attempts} attempt(s)"
        super().__init__(detail)


class WandererUploader:
    """Use Wanderer's API to upload and reconcile route metadata."""

    RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}

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
        self.base_url = base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/api/v1/trail/upload"
        self.trails_endpoint = f"{self.base_url}/api/v1/trail"
        self.categories_endpoint = f"{self.base_url}/api/v1/category"
        self.token = token
        self.timeout = (10, timeout)
        self.upload_retries = upload_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.retry_max_backoff_seconds = retry_max_backoff_seconds
        self.session = requests.Session()
        self._categories: list[dict[str, Any]] | None = None

    def upload(self, path: Path, *, name: str | None = None) -> dict[str, Any] | None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        upload_name = name or path.name

        def open_file() -> tuple[dict[str, tuple[str, Any, str]], list[Any]]:
            file_handle = path.open("rb")
            return {"file": (path.name, file_handle, content_type)}, [file_handle]

        response = self._request(
            "put",
            self.endpoint,
            data={"ignoreDuplicates": "true", "name": upload_name},
            files_factory=open_file,
            operation="upload",
        )
        return _json_object(response)

    def list_trails(self) -> list[dict[str, Any]]:
        response = self._request(
            "get",
            self.trails_endpoint,
            params={"perPage": -1},
            operation="trail lookup",
        )
        payload = _json_payload(response)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return [item for item in payload["items"] if isinstance(item, dict)]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def list_categories(self) -> list[dict[str, Any]]:
        if self._categories is not None:
            return self._categories
        response = self._request(
            "get",
            self.categories_endpoint,
            params={"perPage": -1},
            operation="category lookup",
        )
        payload = _json_payload(response)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            self._categories = [item for item in payload["items"] if isinstance(item, dict)]
        elif isinstance(payload, list):
            self._categories = [item for item in payload if isinstance(item, dict)]
        else:
            self._categories = []
        return self._categories

    def category_id(self, category_name: str) -> str | None:
        wanted = _normalize_category_name(category_name)
        for category in self.list_categories():
            category_id = category.get("id")
            if not isinstance(category_id, str) or not category_id:
                continue
            for field in ("name", "short_name", "shortName"):
                value = category.get(field)
                if isinstance(value, str) and _normalize_category_name(value) == wanted:
                    return category_id
        return None

    def update(
        self,
        trail_id: str,
        *,
        name: str,
        category_id: str | None = None,
        completed: bool | None = None,
        description: str | None = None,
        photos: tuple[Path, ...] = (),
    ) -> dict[str, Any] | None:
        if photos:
            data: dict[str, str] = {"id": trail_id, "name": name}
            if category_id is not None:
                data["category"] = category_id
            if completed is not None:
                data["completed"] = str(completed).lower()
            if description is not None:
                data["description"] = description

            def open_photos() -> tuple[list[tuple[str, tuple[str, Any, str]]], list[Any]]:
                handles: list[Any] = []
                files: list[tuple[str, tuple[str, Any, str]]] = []
                try:
                    for photo in photos:
                        handle = photo.open("rb")
                        handles.append(handle)
                        content_type = mimetypes.guess_type(photo.name)[0] or "application/octet-stream"
                        files.append(("photos", (photo.name, handle, content_type)))
                except Exception:
                    for handle in handles:
                        handle.close()
                    raise
                return files, handles

            response = self._request(
                "post",
                f"{self.base_url}/api/v1/trail/form/{trail_id}",
                data=data,
                files_factory=open_photos,
                operation="trail update",
            )
        else:
            data: dict[str, str | bool] = {"name": name}
            if category_id is not None:
                data["category"] = category_id
            if completed is not None:
                data["completed"] = completed
            if description is not None:
                data["description"] = description
            response = self._request(
                "post",
                f"{self.base_url}/api/v1/trail/{trail_id}",
                json_payload=data,
                operation="trail update",
            )
        return _json_object(response)

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: Any = None,
        json_payload: Any = None,
        params: Any = None,
        files_factory: Any = None,
        operation: str,
    ) -> requests.Response:
        for attempt in range(self.upload_retries + 1):
            handles: list[Any] = []
            try:
                request_kwargs: dict[str, Any] = {
                    "headers": {"Authorization": f"Bearer {self.token}"},
                    "timeout": self.timeout,
                }
                if data is not None:
                    request_kwargs["data"] = data
                if json_payload is not None:
                    request_kwargs["json"] = json_payload
                if params is not None:
                    request_kwargs["params"] = params
                if files_factory is not None:
                    request_kwargs["files"], handles = files_factory()
                response = getattr(self.session, method)(url, **request_kwargs)
            except requests.RequestException as exc:
                retryable = isinstance(exc, (requests.Timeout, requests.ConnectionError))
                if retryable and attempt < self.upload_retries:
                    self._wait_before_retry(attempt, kind=exc.__class__.__name__, operation=operation)
                    continue
                raise WandererUploadError(
                    exc.__class__.__name__,
                    retryable=retryable,
                    attempts=attempt + 1,
                    operation=operation,
                ) from exc
            finally:
                for handle in handles:
                    handle.close()

            if 200 <= response.status_code < 300:
                return response

            retryable = response.status_code in self.RETRYABLE_STATUSES
            if retryable and attempt < self.upload_retries:
                self._wait_before_retry(
                    attempt,
                    kind="http_status",
                    status_code=response.status_code,
                    retry_after=(getattr(response, "headers", {}) or {}).get("Retry-After"),
                    operation=operation,
                )
                continue
            # Do not log response text: it can contain route names or server data.
            raise WandererUploadError(
                "http_status",
                retryable=retryable,
                attempts=attempt + 1,
                status_code=response.status_code,
                operation=operation,
            )
        raise AssertionError("request retry loop did not return or raise")

    def _wait_before_retry(
        self,
        attempt: int,
        *,
        kind: str,
        status_code: int | None = None,
        retry_after: str | None = None,
        operation: str = "upload",
    ) -> None:
        delay = self.retry_backoff_seconds * (2**attempt)
        if isinstance(retry_after, str) and retry_after.isdigit():
            delay = max(delay, int(retry_after))
        delay = min(delay, self.retry_max_backoff_seconds)
        LOGGER.warning(
            "Wanderer %s retrying: error=%s status=%s attempt=%d/%d delay=%ds",
            operation,
            kind,
            status_code if status_code is not None else "none",
            attempt + 1,
            self.upload_retries + 1,
            delay,
        )
        if delay > 0:
            time.sleep(delay)


def _json_payload(response: requests.Response) -> dict[str, Any] | list[Any] | None:
    try:
        payload = response.json()
    except (ValueError, requests.RequestException):
        return None
    return payload if isinstance(payload, (dict, list)) else None


def _json_object(response: requests.Response) -> dict[str, Any] | None:
    payload = _json_payload(response)
    return payload if isinstance(payload, dict) else None


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


_ACTIVITY_CATEGORY_BY_TYPE = {
    "hiking": "Hiking",
    "walking": "Walking",
    "running": "Running",
    "trail_run": "Running",
    "trail_running": "Running",
    "cycling": "Biking",
    "biking": "Biking",
    "climbing": "Climbing",
    "rock_climbing": "Climbing",
    "skiing": "Skiing",
    "winter_sports": "Skiing",
    "canoeing": "Canoeing",
    "kayaking": "Canoeing",
    "water_sports": "Canoeing",
    "fitness_equipment": "Other",
    "other": "Other",
}


def _normalize_activity_type(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").casefold()


def _normalize_category_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", " ", value).strip().casefold()


def _activity_type_values(activity: dict[str, Any]) -> list[str]:
    activity_type = activity.get("activityType")
    if not isinstance(activity_type, dict):
        return []
    return [
        value
        for value in (activity_type.get("typeKey"), activity_type.get("parentTypeKey"))
        if isinstance(value, str)
    ]


def _activity_type_label(activity: dict[str, Any]) -> str | None:
    for value in _activity_type_values(activity):
        normalized = _normalize_activity_type(value)
        if normalized:
            return normalized
    return None


def wanderer_category_for_activity(activity: dict[str, Any]) -> str | None:
    """Map Garmin's activity type labels to Wanderer's built-in categories."""
    for value in _activity_type_values(activity):
        category = _ACTIVITY_CATEGORY_BY_TYPE.get(_normalize_activity_type(value))
        if category is not None:
            return category
    return None


def _activity_name(activity: dict[str, Any], activity_id: str) -> str:
    value = activity.get("activityName")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"Garmin activity {activity_id}"


def _activity_description(activity: dict[str, Any]) -> str | None:
    value = activity.get("description")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _activity_has_images(activity: dict[str, Any]) -> bool:
    return activity.get("hasImages") is True


def _trail_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for field in ("id", "trailId", "trail_id"):
        value = payload.get(field)
        if isinstance(value, (str, int)) and str(value):
            return str(value)
    return None


def _trail_file_names(trail: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for field in ("gpx", "filename", "fileName", "file_name", "routeFilename", "name"):
        value = trail.get(field)
        if isinstance(value, str) and value:
            names.add(PurePosixPath(value.replace("\\", "/")).name.casefold())
    return names


def _garmin_legacy_names(activity_id: str, manifest_item: dict[str, Any] | None) -> set[str]:
    names = {f"{activity_id}{suffix}" for suffix in SUPPORTED_SUFFIXES}
    if isinstance(manifest_item, dict):
        filename = manifest_item.get("filename")
        if isinstance(filename, str) and filename:
            names.add(PurePosixPath(filename.replace("\\", "/")).name)
    return {name.casefold() for name in names}


_GARMIN_IMAGE_HOSTS = {
    "connect.garmin.com",
    "connectapi.garmin.com",
    "connect.garmin.cn",
    "connectapi.garmin.cn",
}
_SUPPORTED_PHOTO_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}


def _garmin_photo_request(value: Any) -> tuple[str, list[tuple[str, str]]] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if parsed.netloc:
        try:
            hostname = (parsed.hostname or "").casefold()
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or hostname not in _GARMIN_IMAGE_HOSTS:
            return None
    path = parsed.path
    for prefix in ("/modern/proxy/", "modern/proxy/"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    else:
        path = path.lstrip("/")
    if not path or "\\" in path or any(part == ".." for part in path.split("/")):
        return None
    return path, parse_qsl(parsed.query, keep_blank_values=True)


def _photo_suffix(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.casefold()
    return suffix if suffix in _SUPPORTED_PHOTO_SUFFIXES else ".jpg"


def _photo_archive_name(activity_id: str, image_id: str, suffix: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", image_id).strip("._")[:48] or "image"
    identity = hashlib.sha256(image_id.encode("utf-8")).hexdigest()[:12]
    return f"{activity_id}-photo-{safe_id}-{identity}{suffix}"


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
        self._legacy_trails: list[dict[str, Any]] | None = None

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

    def _legacy_trail(self, activity_id: str, manifest_item: dict[str, Any] | None) -> dict[str, Any] | None:
        if self._legacy_trails is None:
            list_trails = getattr(self.uploader, "list_trails", None)
            if not callable(list_trails):
                self._legacy_trails = []
            else:
                trails = list_trails()
                self._legacy_trails = (
                    [trail for trail in trails if isinstance(trail, dict)]
                    if isinstance(trails, list)
                    else []
                )
        candidates = _garmin_legacy_names(activity_id, manifest_item)
        for trail in self._legacy_trails:
            if _trail_id(trail) and _trail_file_names(trail) & candidates:
                return trail
        return None

    def _category_id(self, category_name: str | None) -> str | None:
        if not category_name:
            return None
        category_id = getattr(self.uploader, "category_id", None)
        if not callable(category_id):
            return None
        try:
            resolved = category_id(category_name)
        except SyncError as exc:
            LOGGER.warning("Wanderer category lookup skipped: error=%s", exc.__class__.__name__)
            return None
        return resolved if isinstance(resolved, str) and resolved else None

    def _download_garmin_photos(
        self,
        client: Any,
        activity_id: str,
        activity: dict[str, Any],
        known_photo_ids: set[str],
    ) -> list[tuple[str, Path]]:
        if not _activity_has_images(activity) or self.config.max_photos_per_activity <= 0:
            return []
        get_details = getattr(client, "get_activity_details", None)
        download = getattr(client, "download", None)
        if not callable(get_details) or not callable(download):
            return []
        try:
            details = get_details(activity_id)
        except Exception as exc:
            LOGGER.warning("Garmin activity photo lookup skipped: error=%s", exc.__class__.__name__)
            return []
        metadata = details.get("metadataDTO") if isinstance(details, dict) else None
        if not isinstance(metadata, dict):
            metadata = details.get("metadata_dto") if isinstance(details, dict) else None
        if not isinstance(metadata, dict):
            metadata = details.get("metadata") if isinstance(details, dict) else None
        images = metadata.get("activityImages") if isinstance(metadata, dict) else None
        if not isinstance(images, list) and isinstance(details, dict):
            images = details.get("activityImages")
        if not isinstance(images, list):
            return []

        photos: list[tuple[str, Path]] = []
        seen = set(known_photo_ids)
        for image in images:
            if len(photos) >= self.config.max_photos_per_activity:
                LOGGER.warning(
                    "Garmin activity photo limit reached: limit=%d",
                    self.config.max_photos_per_activity,
                )
                break
            if not isinstance(image, dict):
                continue
            image_id = image.get("imageId") or image.get("image_id") or image.get("id")
            if not isinstance(image_id, (str, int)) or not str(image_id):
                continue
            image_id = str(image_id)
            if image_id in seen:
                continue
            image_url = next(
                (
                    image.get(field)
                    for field in ("mediumUrl", "url", "smallUrl", "medium_url", "small_url")
                    if isinstance(image.get(field), str) and image.get(field)
                ),
                None,
            )
            request = _garmin_photo_request(image_url)
            if request is None:
                continue
            assert isinstance(image_url, str)
            request_path, params = request
            try:
                content = download(request_path, params=params) if params else download(request_path)
                if not isinstance(content, (bytes, bytearray)) or not content:
                    continue
                if len(content) > self.config.max_file_bytes:
                    raise SyncError("Garmin activity photo exceeds the configured size limit")
                destination = self.config.archive_dir / "garmin" / _photo_archive_name(
                    activity_id,
                    image_id,
                    _photo_suffix(image_url),
                )
                _write_private(destination, bytes(content))
                photos.append((image_id, destination))
                seen.add(image_id)
            except Exception as exc:
                LOGGER.warning("Garmin activity photo skipped: error=%s", exc.__class__.__name__)
        return photos

    def _apply_garmin_metadata(
        self,
        client: Any,
        activity_id: str,
        key: str,
        activity: dict[str, Any],
        trail_id: str | None,
    ) -> str | None:
        manifest_item = self.manifest.record(key)
        if trail_id is None:
            trail = self._legacy_trail(activity_id, manifest_item)
            trail_id = _trail_id(trail)
        if trail_id is None:
            LOGGER.warning("Garmin activity metadata backfill skipped: no matching Wanderer trail")
            return None
        name = _activity_name(activity, activity_id)
        description = _activity_description(activity)
        activity_type = _activity_type_label(activity)
        category_name = wanderer_category_for_activity(activity)
        category_id = self._category_id(category_name)
        raw_photo_ids = (manifest_item or {}).get("photo_ids", [])
        known_photo_ids = (
            [value for value in raw_photo_ids if isinstance(value, str)]
            if isinstance(raw_photo_ids, list)
            else []
        )
        photos = self._download_garmin_photos(client, activity_id, activity, set(known_photo_ids))
        update = getattr(self.uploader, "update", None)
        category_changed = category_name is not None and category_id is not None and (
            not manifest_item or manifest_item.get("category") != category_name
        )
        completed_changed = not manifest_item or manifest_item.get("completed") is not True
        description_changed = description is not None and (
            not manifest_item or manifest_item.get("description") != description
        )
        metadata_changed = (
            not manifest_item
            or manifest_item.get("trail_id") != trail_id
            or manifest_item.get("activity_name") != name
            or manifest_item.get("activity_type") != activity_type
            or category_changed
            or completed_changed
            or description_changed
            or bool(photos)
        )
        if metadata_changed:
            if not callable(update):
                raise SyncError("Wanderer uploader does not support metadata updates")
            update(
                trail_id,
                name=name,
                category_id=category_id,
                completed=True,
                description=description,
                photos=tuple(path for _image_id, path in photos),
            )

        photo_ids = list(known_photo_ids)
        photo_ids.extend(image_id for image_id, _path in photos)
        self.manifest.update_metadata(
            key,
            trail_id=trail_id,
            activity_name=name,
            activity_type=activity_type,
            category=category_name if category_id is not None else None,
            completed=True,
            description=description,
            photo_ids=photo_ids,
        )
        self.manifest.save()
        return trail_id

    def _process_garmin_activity(self, client: Garmin, activity: dict[str, Any]) -> None:
        activity_id = activity.get("activityId") or activity.get("activity_id")
        if activity_id is None or not str(activity_id).isdigit():
            LOGGER.warning("Garmin activity without a numeric ID was skipped")
            return
        activity_id = str(activity_id)
        key = f"garmin:{activity_id}"
        manifest_item = self.manifest.record(key)
        if self.manifest.is_uploaded(key):
            self._apply_garmin_metadata(
                client,
                activity_id,
                key,
                activity,
                _trail_id(manifest_item),
            )
            self.skipped += 1
            return

        legacy_trail = self._legacy_trail(activity_id, manifest_item) if manifest_item is not None else None
        if legacy_trail is not None:
            trail_id = _trail_id(legacy_trail)
            if trail_id is None:
                raise SyncError("Wanderer legacy trail has no ID")
            filename = next(iter(_trail_file_names(legacy_trail)), f"{activity_id}.fit")
            if manifest_item is None:
                self.manifest.mark_linked(
                    key,
                    filename=filename,
                    source="garmin",
                    trail_id=trail_id,
                )
            self._apply_garmin_metadata(client, activity_id, key, activity, trail_id)
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
        self._process_route(
            destination,
            key,
            "garmin",
            client=client,
            activity=activity,
            activity_id=activity_id,
        )

    def _process_route(
        self,
        source: Path,
        key: str,
        source_name: str,
        *,
        client: Any | None = None,
        activity: dict[str, Any] | None = None,
        activity_id: str | None = None,
    ) -> None:
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
            if activity is None:
                response = self.uploader.upload(upload_path)
            else:
                assert activity_id is not None
                response = self.uploader.upload(upload_path, name=_activity_name(activity, activity_id))
            trail_id = _trail_id(response)
            self.manifest.mark_uploaded(
                key,
                digest,
                filename=upload_path.name,
                source=source_name,
                trail_id=trail_id,
            )
            self.manifest.save()
            if activity is not None:
                assert client is not None and activity_id is not None
                if trail_id is None:
                    self._legacy_trails = None
                self._apply_garmin_metadata(client, activity_id, key, activity, trail_id)
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
