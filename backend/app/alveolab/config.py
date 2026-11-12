"""Module switch. One env var, read each time so tests can flip it."""

import os


def modules_enabled() -> bool:
    return os.environ.get("ALVEOLAB_MODULES", "1").strip().lower() not in ("0", "false", "no", "off")
