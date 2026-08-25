"""Container entrypoint for the Wanderer Garmin synchronizer."""
from __future__ import annotations

import logging
import sys

from .config import Config, ConfigError
from .pipeline import Pipeline, SyncError


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("wanderer_sync")


def main() -> int:
    try:
        config = Config.from_env()
        Pipeline(config).run()
    except ConfigError as exc:
        LOGGER.error("configuration error: %s", exc)
        return 2
    except SyncError as exc:
        LOGGER.error("sync failed: %s", exc)
        return 1
    except Exception as exc:
        # Keep unexpected errors visible without dumping credentials or server
        # response bodies into the job log.
        LOGGER.exception("unexpected sync failure: %s", exc.__class__.__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
