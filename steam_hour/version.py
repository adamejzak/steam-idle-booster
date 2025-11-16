from __future__ import annotations

import re
from typing import Tuple

import requests

CURRENT_VERSION = "1.0.1"
VERSION_CHECK_URL = "https://raw.githubusercontent.com/adamejzak/steam-idle-booster/refs/heads/gui/VERSION"
REPOSITORY_URL = "https://github.com/adamejzak/steam-idle-booster/"

_VERSION_SEPARATOR = re.compile(r"[.\-]")


def fetch_latest_version(timeout: float = 5.0) -> str | None:
    """Fetch the latest available version string from the remote source."""
    try:
        response = requests.get(VERSION_CHECK_URL, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        return None
    latest = response.text.strip()
    return latest or None


def normalize_version(tag: str) -> Tuple[int, ...]:
    parts: list[int] = []
    for chunk in _VERSION_SEPARATOR.split(tag):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


def is_newer_version(latest: str, current: str = CURRENT_VERSION) -> bool:
    """Return True if `latest` version is greater than `current`."""
    return normalize_version(latest) > normalize_version(current)

