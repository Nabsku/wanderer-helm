#!/usr/bin/env python3
"""Credential-free tests for the shared Garmin/Wanderer pipeline."""
from __future__ import annotations

import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import fitdecode
import gpxpy
import requests

from wanderer_sync.config import Config, ConfigError
from wanderer_sync.converter import fit_to_gpx
from wanderer_sync.manifest import Manifest
from wanderer_sync.pipeline import (
    Pipeline,
    SyncError,
    WandererUploader,
    _extract_original_activity,
    wanderer_category_for_activity,
)


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


class GarminRecordingUploader:
    def __init__(self, trails: list[dict[str, Any]] | None = None) -> None:
        self.uploads: list[tuple[Path, str | None]] = []
        self.updates: list[dict[str, Any]] = []
        self.trails: list[dict[str, Any]] = trails or []

    def upload(self, path: Path, *, name: str | None = None) -> dict[str, str]:
        self.uploads.append((path, name))
        return {"id": "trail00000042"}

    def update(
        self,
        trail_id: str,
        *,
        name: str,
        category_id: str | None = None,
        photos: tuple[Path, ...] = (),
    ) -> dict[str, str]:
        self.updates.append(
            {
                "trail_id": trail_id,
                "name": name,
                "category_id": category_id,
                "photos": photos,
            }
        )
        return {"id": trail_id}

    def category_id(self, category_name: str) -> str | None:
        return {
            "Hiking": "category-hiking",
            "Walking": "category-walking",
            "Running": "category-running",
            "Climbing": "category-climbing",
            "Skiing": "category-skiing",
            "Canoeing": "category-canoeing",
            "Biking": "category-biking",
            "Other": "category-other",
        }.get(category_name)

    def list_trails(self) -> list[dict[str, object]]:
        return self.trails


class FakeGarminClient:
    def __init__(self, route: bytes = b"<gpx version='1.1'/>", details: dict[str, object] | None = None) -> None:
        self.route = route
        self.details = details or {}
        self.downloaded_paths: list[str] = []
        self.download_activity_calls = 0

    def download_activity(self, _activity_id: str, *, dl_fmt: object) -> bytes:
        self.download_activity_calls += 1
        return self.route

    def get_activity_details(self, _activity_id: str) -> dict[str, object]:
        return self.details

    def download(self, path: str, **_kwargs: object) -> bytes:
        self.downloaded_paths.append(path)
        return b"fake photo bytes"


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

    def test_wanderer_uploader_retries_transient_http_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / "route.gpx"
            route.write_text("<gpx version='1.1'/>", encoding="utf-8")
            uploader = WandererUploader(
                "https://wanderer.example",
                "secret-token",
                10,
                upload_retries=2,
                retry_backoff_seconds=1,
                retry_max_backoff_seconds=2,
            )
            responses = [
                mock.Mock(status_code=502, headers={}),
                mock.Mock(status_code=503, headers={}),
                mock.Mock(status_code=204, headers={}),
            ]
            with (
                mock.patch.object(uploader.session, "put", side_effect=responses) as put,
                mock.patch("wanderer_sync.pipeline.time.sleep") as sleep,
            ):
                uploader.upload(route)
            self.assertEqual(put.call_count, 3)
            sleep.assert_has_calls([mock.call(1), mock.call(2)])

    def test_wanderer_uploader_retries_network_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / "route.gpx"
            route.write_text("<gpx version='1.1'/>", encoding="utf-8")
            uploader = WandererUploader(
                "https://wanderer.example",
                "secret-token",
                10,
                upload_retries=1,
                retry_backoff_seconds=1,
                retry_max_backoff_seconds=1,
            )
            response = mock.Mock(status_code=204, headers={})
            with (
                mock.patch.object(
                    uploader.session,
                    "put",
                    side_effect=[requests.ReadTimeout(), response],
                ) as put,
                mock.patch("wanderer_sync.pipeline.time.sleep") as sleep,
            ):
                uploader.upload(route)
            self.assertEqual(put.call_count, 2)
            sleep.assert_called_once_with(1)

    def test_wanderer_uploader_does_not_retry_permanent_http_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / "route.gpx"
            route.write_text("<gpx version='1.1'/>", encoding="utf-8")
            uploader = WandererUploader(
                "https://wanderer.example",
                "secret-token",
                10,
                upload_retries=3,
                retry_backoff_seconds=1,
                retry_max_backoff_seconds=1,
            )
            response = mock.Mock(status_code=401, headers={})
            with mock.patch.object(uploader.session, "put", return_value=response) as put:
                with self.assertRaises(SyncError) as raised:
                    uploader.upload(route)
            self.assertEqual(put.call_count, 1)
            self.assertIn("HTTP 401", str(raised.exception))

    def test_wanderer_uploader_uses_activity_name_and_returns_trail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / "42.gpx"
            route.write_text("<gpx version='1.1'/>", encoding="utf-8")
            uploader = WandererUploader("https://wanderer.example", "secret-token", 10)
            response = mock.Mock(status_code=201, headers={})
            response.json.return_value = {"id": "trail00000042"}
            with mock.patch.object(uploader.session, "put", return_value=response) as put:
                trail = uploader.upload(route, name="Morning Run")
            self.assertEqual(trail, {"id": "trail00000042"})
            self.assertEqual(put.call_args.kwargs["data"]["name"], "Morning Run")

    def test_wanderer_uploader_updates_trail_and_photos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "photo.jpg"
            photo.write_bytes(b"photo")
            uploader = WandererUploader("https://wanderer.example", "secret-token", 10)
            response = mock.Mock(status_code=200, headers={})
            response.json.return_value = {"id": "trail00000042"}
            with mock.patch.object(uploader.session, "post", return_value=response) as post:
                trail = uploader.update(
                    "trail00000042",
                    name="Morning Run",
                    category_id="category-running",
                    photos=(photo,),
                )
            self.assertEqual(trail, {"id": "trail00000042"})
            self.assertEqual(
                post.call_args.args[0],
                "https://wanderer.example/api/v1/trail/form/trail00000042",
            )
            self.assertEqual(post.call_args.kwargs["data"], {
                "id": "trail00000042",
                "name": "Morning Run",
                "category": "category-running",
            })
            files = post.call_args.kwargs["files"]
            self.assertEqual(files[0][0], "photos")
            self.assertEqual(files[0][1][0], "photo.jpg")
            self.assertTrue(files[0][1][1].closed)

    def test_wanderer_uploader_lists_trails_and_categories(self) -> None:
        with tempfile.TemporaryDirectory():
            response = mock.Mock(status_code=200, headers={})
            response.json.return_value = {
                "items": [{"id": "trail00000042", "name": "route.gpx"}]
            }
            categories = mock.Mock(status_code=200, headers={})
            categories.json.return_value = {
                "items": [{"id": "category-running", "name": "Running"}]
            }
            uploader = WandererUploader("https://wanderer.example", "token", 10)
            with mock.patch.object(uploader.session, "get", side_effect=[response, categories]) as get:
                self.assertEqual(uploader.list_trails()[0]["name"], "route.gpx")
                self.assertEqual(uploader.category_id("running"), "category-running")
            self.assertEqual(get.call_args_list[0].kwargs["params"], {"perPage": -1})
            self.assertEqual(get.call_args_list[1].kwargs["params"], {"perPage": -1})

    def test_garmin_activity_types_match_wanderer_categories(self) -> None:
        cases = {
            "walking": "Walking",
            "hiking": "Hiking",
            "running": "Running",
            "trail_run": "Running",
            "trail_running": "Running",
            "cycling": "Biking",
            "biking": "Biking",
            "climbing": "Climbing",
            "rock_climbing": "Climbing",
            "skiing": "Skiing",
            "winter_sports": "Skiing",
            "kayaking": "Canoeing",
            "canoeing": "Canoeing",
            "water_sports": "Canoeing",
            "fitness_equipment": "Other",
            "other": "Other",
        }
        for type_key, category in cases.items():
            with self.subTest(type_key=type_key):
                activity = {"activityType": {"typeKey": type_key}}
                self.assertEqual(wanderer_category_for_activity(activity), category)
        self.assertEqual(
            wanderer_category_for_activity({"activityType": {"typeKey": "unknown_sport"}}),
            None,
        )
        self.assertEqual(
            wanderer_category_for_activity(
                {"activityType": {"typeKey": "generic", "parentTypeKey": "trail_running"}}
            ),
            "Running",
        )

    def test_garmin_activity_uses_name_and_category(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._garmin_config(root)
            pipeline = Pipeline(config)
            uploader = GarminRecordingUploader()
            pipeline.uploader = uploader
            pipeline._process_garmin_activity(
                FakeGarminClient(),
                {
                    "activityId": "42",
                    "activityName": "Morning Run",
                    "activityType": {"typeKey": "trail_run"},
                    "hasImages": False,
                },
            )
            self.assertEqual(uploader.uploads[0][1], "Morning Run")
            self.assertEqual(len(uploader.updates), 1)
            self.assertEqual(uploader.updates[0]["name"], "Morning Run")
            self.assertEqual(uploader.updates[0]["category_id"], "category-running")
            item = pipeline.manifest.processed["garmin:42"]
            self.assertEqual(item["activity_name"], "Morning Run")
            self.assertEqual(item["activity_type"], "trail_run")
            self.assertEqual(item["category"], "Running")
            self.assertEqual(item["trail_id"], "trail00000042")

    def test_existing_garmin_activity_is_backfilled_without_redownload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._garmin_config(root)
            manifest = Manifest(config.manifest_path)
            manifest.mark_uploaded("garmin:42", "old-digest", filename="42.fit", source="garmin")
            manifest.save()
            pipeline = Pipeline(config)
            uploader = GarminRecordingUploader([{"id": "existing-trail", "gpx": "42.fit"}])
            pipeline.uploader = uploader
            client = FakeGarminClient(
                details={
                    "metadataDTO": {
                        "activityImages": [
                            {
                                "imageId": "legacy-photo",
                                "mediumUrl": "https://connect.garmin.com/modern/proxy/image-service/42/legacy.jpg",
                            }
                        ]
                    }
                }
            )
            pipeline._process_garmin_activity(
                client,
                {
                    "activityId": "42",
                    "activityName": "Old Morning Run",
                    "activityType": {"typeKey": "hiking"},
                    "hasImages": True,
                },
            )
            self.assertEqual(client.download_activity_calls, 0)
            self.assertEqual(client.downloaded_paths, ["image-service/42/legacy.jpg"])
            self.assertEqual(uploader.uploads, [])
            self.assertEqual(uploader.updates[0]["trail_id"], "existing-trail")
            self.assertEqual(uploader.updates[0]["name"], "Old Morning Run")
            self.assertEqual(uploader.updates[0]["category_id"], "category-hiking")
            self.assertEqual(
                len(uploader.updates[0]["photos"]),
                1,
            )
            self.assertEqual(
                pipeline.manifest.processed["garmin:42"]["photo_ids"],
                ["legacy-photo"],
            )
            self.assertEqual(pipeline.manifest.processed["garmin:42"]["trail_id"], "existing-trail")

    def test_garmin_activity_photos_are_downloaded_and_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._garmin_config(root)
            pipeline = Pipeline(config)
            uploader = GarminRecordingUploader()
            pipeline.uploader = uploader
            client = FakeGarminClient(
                details={
                    "metadataDTO": {
                        "activityImages": [
                            {
                                "imageId": "photo-1",
                                "mediumUrl": "https://connect.garmin.com/modern/proxy/image-service/42/photo-1.jpg",
                            },
                            {
                                "imageId": "external-photo",
                                "mediumUrl": "https://images.example.invalid/photo.jpg",
                            },
                        ]
                    }
                }
            )
            pipeline._process_garmin_activity(
                client,
                {
                    "activityId": "42",
                    "activityName": "Photo Hike",
                    "activityType": {"typeKey": "hiking"},
                    "hasImages": True,
                },
            )
            self.assertEqual(client.downloaded_paths, ["image-service/42/photo-1.jpg"])
            photos = uploader.updates[0]["photos"]
            self.assertEqual(len(photos), 1)
            self.assertTrue(photos[0].exists())
            self.assertEqual(pipeline.manifest.processed["garmin:42"]["photo_ids"], ["photo-1"])

    @staticmethod
    def _garmin_config(root: Path) -> Config:
        return Config(
            sources=frozenset({"garmin"}),
            wanderer_url="http://wanderer",
            wanderer_token="token",
            data_dir=root / "data",
            inbox_dir=root / "data/inbox",
            archive_dir=root / "data/archive",
            manifest_path=root / "data/state/manifest.json",
            token_store=root / "data/state/tokens.json",
            fit_mode="preserve",
            garmin_email="email",
            garmin_password="password",
            garmin_download_format="gpx",
            garmin_page_size=1000,
            garmin_max_pages=2000,
            max_file_bytes=1024 * 1024,
            max_zip_members=10,
            max_zip_uncompressed_bytes=1024 * 1024,
            request_timeout_seconds=10,
            max_photos_per_activity=20,
        )

    def test_config_reads_retry_controls(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "SYNC_SOURCES": "official",
                "WANDERER_URL": "http://wanderer",
                "WANDERER_API_TOKEN": "token",
                "UPLOAD_RETRIES": "4",
                "RETRY_BACKOFF_SECONDS": "3",
                "RETRY_MAX_BACKOFF_SECONDS": "20",
                "MAX_PHOTOS_PER_ACTIVITY": "7",
            },
            clear=True,
        ):
            config = Config.from_env()
        self.assertEqual(config.upload_retries, 4)
        self.assertEqual(config.retry_backoff_seconds, 3)
        self.assertEqual(config.retry_max_backoff_seconds, 20)
        self.assertEqual(config.max_photos_per_activity, 7)

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
