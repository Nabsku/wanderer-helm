#!/usr/bin/env python3
"""Credential-free tests for the shared Garmin/Wanderer pipeline."""
from __future__ import annotations

import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import fitdecode
import gpxpy

from wanderer_sync.config import Config, ConfigError
from wanderer_sync.converter import fit_to_gpx
from wanderer_sync.pipeline import Pipeline, SyncError, WandererUploader, _extract_original_activity


class FakeFrame:
    frame_type = fitdecode.FIT_FRAME_DATA
    name = "record"

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get_value(self, name: str, fallback=None):
        return self.values.get(name, fallback)


class FakeReader:
    def __init__(self, _path: str) -> None:
        self.frames = [
            FakeFrame(
                {
                    "position_lat": int(52.5 / 180 * 2**31),
                    "position_long": int(13.4 / 180 * 2**31),
                    "enhanced_altitude": 40.0,
                    "timestamp": None,
                }
            ),
            FakeFrame(
                {
                    "position_lat": int(52.5001 / 180 * 2**31),
                    "position_long": int(13.4001 / 180 * 2**31),
                    "altitude": 41.0,
                    "timestamp": None,
                }
            ),
        ]

    def __enter__(self):
        return iter(self.frames)

    def __exit__(self, *_args):
        return False


class RecordingUploader:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def upload(self, path: Path) -> None:
        self.paths.append(path)


class SyncTests(unittest.TestCase):
    def test_wanderer_uploader_uses_token_api_and_duplicate_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / "route.gpx"
            route.write_text("<gpx version='1.1'/>", encoding="utf-8")
            uploader = WandererUploader("https://wanderer.example", "secret-token", 60)
            response = mock.Mock(status_code=204)
            with mock.patch.object(uploader.session, "put", return_value=response) as put:
                uploader.upload(route)
            self.assertEqual(put.call_args.args[0], "https://wanderer.example/api/v1/trail/upload")
            self.assertEqual(put.call_args.kwargs["headers"], {"Authorization": "Bearer secret-token"})
            self.assertEqual(put.call_args.kwargs["data"]["ignoreDuplicates"], "true")

    def test_fit_conversion_keeps_route_points(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "activity.fit"
            destination = Path(directory) / "activity.gpx"
            source.write_bytes(b"test fixture")
            with mock.patch("wanderer_sync.converter.fitdecode.FitReader", FakeReader):
                count = fit_to_gpx(source, destination)
            self.assertEqual(count, 2)
            parsed = gpxpy.parse(destination.read_text(encoding="utf-8"))
            points = parsed.tracks[0].segments[0].points
            self.assertEqual(len(points), 2)
            self.assertAlmostEqual(points[0].latitude, 52.5, places=3)
            self.assertAlmostEqual(points[0].longitude, 13.4, places=3)

    def test_official_zip_is_uploaded_once_and_manifest_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            with zipfile.ZipFile(inbox / "garmin-export.zip", "w") as archive:
                archive.writestr("DI_CONNECT/activities/route.gpx", "<gpx version='1.1'/>")
            config = Config(
                sources=frozenset({"official"}),
                wanderer_url="http://wanderer",
                wanderer_token="token",
                data_dir=root / "data",
                inbox_dir=inbox,
                archive_dir=root / "data/archive",
                manifest_path=root / "data/state/manifest.json",
                token_store=root / "data/state/tokens.json",
                fit_mode="preserve",
                garmin_email=None,
                garmin_password=None,
                garmin_download_format="original",
                garmin_page_size=1000,
                garmin_max_pages=2000,
                max_file_bytes=1024 * 1024,
                max_zip_members=10,
                max_zip_uncompressed_bytes=1024 * 1024,
                request_timeout_seconds=10,
            )
            first = Pipeline(config)
            first.uploader = RecordingUploader()
            self.assertEqual(first.run(), 1)
            self.assertEqual(len(first.uploader.paths), 1)
            second = Pipeline(config)
            second.uploader = RecordingUploader()
            self.assertEqual(second.run(), 0)
            self.assertEqual(second.skipped, 1)
            state = json.loads(config.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["processed"]), 1)
            self.assertEqual(stat.S_IMODE(config.manifest_path.stat().st_mode), 0o600)

    def test_unsafe_zip_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../../escape.gpx", "data")
            config = Config(
                sources=frozenset({"official"}),
                wanderer_url="http://wanderer",
                wanderer_token="token",
                data_dir=Path(directory) / "data",
                inbox_dir=Path(directory),
                archive_dir=Path(directory) / "archive",
                manifest_path=Path(directory) / "state.json",
                token_store=Path(directory) / "tokens.json",
                fit_mode="preserve",
                garmin_email=None,
                garmin_password=None,
                garmin_download_format="original",
                garmin_page_size=1000,
                garmin_max_pages=2000,
                max_file_bytes=1024,
                max_zip_members=10,
                max_zip_uncompressed_bytes=1024,
                request_timeout_seconds=10,
            )
            pipeline = Pipeline(config)
            pipeline.uploader = RecordingUploader()
            with self.assertRaises(SyncError):
                pipeline._process_zip(path)

    def test_garmin_original_zip_budget_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("activity.fit", b"small")
                archive.writestr("metadata.bin", b"x" * 2048)
            with self.assertRaises(SyncError):
                _extract_original_activity(path.read_bytes(), 1024, 10, 1024)

    def test_garmin_source_requires_credentials(self) -> None:
        with mock.patch.dict("os.environ", {"SYNC_SOURCES": "garmin", "WANDERER_URL": "http://wanderer", "WANDERER_API_TOKEN": "x"}, clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
