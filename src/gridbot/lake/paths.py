"""Lake and raw-corpus locations.

``GRIDBOT_DATA_DIR`` is the repo's single data knob (``gridbot.config``); the raw
corpus is a subdirectory of it, so pointing the whole pipeline at the T7 mount
is one environment variable and no code change. The T7 copy is read-only
evidence: ``GRIDBOT_RAW_DIR`` lets a run read raw files from the drive while
writing derived Parquet to local disk.
"""

from __future__ import annotations

import os
from pathlib import Path

from gridbot import config


def raw_root() -> Path:
    """Directory holding the capture day-file trees (``grid/``, ``pm/``, ``pmus/``)."""
    return Path(os.environ.get("GRIDBOT_RAW_DIR", config.DATA_DIR))


def lake_dir() -> Path:
    """Root of the derived lake. Overridable so derived data can live off the T7."""
    return Path(os.environ.get("GRIDBOT_LAKE_DIR", config.DATA_DIR / "lake"))


def manifest_path() -> Path:
    return lake_dir() / "manifest.json"
