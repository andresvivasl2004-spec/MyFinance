"""
jsonfile.py — Read a settings file without letting a damaged one stop the app.

A file that can't be read is copied to <name>.corrupt first, so falling back to
the defaults (and saving over it later) never destroys what was in it.
"""

import json
import shutil
from pathlib import Path


def read_json(path: Path, default):
    """The parsed file, or `default` if it is missing, damaged or of another type."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        data = None
    if isinstance(data, type(default)):
        return data
    try:
        shutil.copy2(path, path.with_name(path.name + ".corrupt"))
    except OSError:
        pass
    return default
