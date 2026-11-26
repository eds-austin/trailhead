"""One-time move of user folders from the app's old name (Portfolio Tracker) to Trailhead.

Runs at startup before the database is opened. A folder is moved only when the old one exists and
the new one does not, so it never overwrites data and is a no-op after the first run.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import platformdirs

log = logging.getLogger(__name__)

LEGACY_APP_DIR_NAME = "PortfolioTracker"
LEGACY_BACKUP_DIR_NAME = "PortfolioTracker Backups"


def legacy_moves() -> list[tuple[Path, Path]]:
    """(old, new) pairs for the data, cache and default backup folders."""
    from .backup import default_backup_dir
    from .db import APP_DIR_NAME, default_data_dir

    data = Path(platformdirs.user_data_dir(LEGACY_APP_DIR_NAME, appauthor=False))
    cache = Path(platformdirs.user_cache_dir(LEGACY_APP_DIR_NAME))
    backups = Path(platformdirs.user_documents_dir()) / LEGACY_BACKUP_DIR_NAME
    return [
        (data, default_data_dir()),
        (cache, Path(platformdirs.user_cache_dir(APP_DIR_NAME))),
        (backups, default_backup_dir()),
    ]


def migrate_legacy_dirs(moves: list[tuple[Path, Path]] | None = None) -> list[Path]:
    """Move each old folder to its new name if only the old one exists. Returns the new paths created."""
    moved = []
    for old, new in moves if moves is not None else legacy_moves():
        if old.is_dir() and not new.exists():
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(new))
            except OSError:
                log.exception("Could not move %s to %s", old, new)
                continue
            log.info("Moved %s to %s", old, new)
            moved.append(new)
    return moved
