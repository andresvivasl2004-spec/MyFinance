"""
duplicates.py — remembers which "possible duplicate" groups you've reviewed
and confirmed are genuine repeated transactions (two identical bus fares the
same day, a Bizum sent three times, etc.), so they stop being flagged every
time you open the Anomalies tab.

Stored in dismissed_duplicates.json next to this file, keyed by the same
(date, amount, description, source) tuple storage.find_duplicate_groups()
and storage.group_key() use. Mirrors the investments.py override pattern
already used elsewhere in the app.
"""

import json
from pathlib import Path

DISMISSED_FILE = Path(__file__).resolve().parent.parent / "data" / "dismissed_duplicates.json"


def load_dismissed() -> set[tuple]:
    """All group keys the user has confirmed are not accidental duplicates."""
    if not DISMISSED_FILE.exists():
        return set()
    with open(DISMISSED_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return {tuple(item) for item in raw}


def save_dismissed(keys: set[tuple]) -> None:
    with open(DISMISSED_FILE, "w", encoding="utf-8") as f:
        json.dump([list(k) for k in sorted(keys, key=str)], f, indent=2, ensure_ascii=False)


def dismiss(key: tuple) -> None:
    """Mark one duplicate group as reviewed/genuine — stop flagging it."""
    keys = load_dismissed()
    keys.add(tuple(key))
    save_dismissed(keys)


def dismiss_many(keys) -> None:
    """Dismiss several groups at once (e.g. a bulk 'these are all mine' review)."""
    current = load_dismissed()
    current.update(tuple(k) for k in keys)
    save_dismissed(current)


def undismiss(key: tuple) -> None:
    """Bring a previously-dismissed group back into the flagged list."""
    keys = load_dismissed()
    keys.discard(tuple(key))
    save_dismissed(keys)
