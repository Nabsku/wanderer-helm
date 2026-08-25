"""FIT-to-GPX conversion with only route-bearing fields."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitdecode
import gpxpy.gpx


class ConversionError(ValueError):
    """Raised when a FIT file cannot produce a usable track."""


def _value(frame: Any, name: str, fallback: Any = None) -> Any:
    try:
        return frame.get_value(name, fallback=fallback)
    except (KeyError, TypeError, ValueError):
        return fallback


def _degrees(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # FIT position fields are signed semicircles. fitdecode returns the decoded
    # integer because the FIT profile expresses the unit as semicircles.
    result = number * 180.0 / 2**31
    if not -180.0 <= result <= 180.0:
        return None
    return result


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def fit_to_gpx(source: Path, destination: Path) -> int:
    """Convert FIT record messages to a compact GPX 1.1 track.

    The original FIT file remains untouched. The result intentionally keeps
    only route-critical fields: coordinates, elevation, and timestamps. FIT
    remains the preferred upload format when Wanderer is used directly because
    it preserves more device data.
    """
    gpx = gpxpy.gpx.GPX()
    gpx.version = "1.1"
    gpx.creator = "wanderer-garmin-sync"
    track = gpxpy.gpx.GPXTrack(name=source.stem)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    gpx.tracks.append(track)

    count = 0
    try:
        with fitdecode.FitReader(str(source)) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA or frame.name != "record":
                    continue
                latitude = _degrees(_value(frame, "position_lat"))
                longitude = _degrees(_value(frame, "position_long"))
                if latitude is None or longitude is None:
                    continue
                elevation = _value(frame, "enhanced_altitude")
                if elevation is None:
                    elevation = _value(frame, "altitude")
                timestamp = _timestamp(_value(frame, "timestamp"))
                point = gpxpy.gpx.GPXTrackPoint(
                    latitude,
                    longitude,
                    elevation=float(elevation) if elevation is not None else None,
                    time=timestamp,
                )
                segment.points.append(point)
                count += 1
    except Exception as exc:  # fitdecode exposes several parser exception types
        raise ConversionError(f"failed to parse FIT file {source.name}: {exc}") from exc

    if count == 0:
        raise ConversionError(f"FIT file {source.name} contains no usable GPS records")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(gpx.to_xml(version="1.1"), encoding="utf-8")
    destination.chmod(0o600)
    return count
