"""
private_data.py — Your own funds and merchants, kept out of the code.

The names of the funds you hold and the places you shop say a lot about you, so
they live in data/private_data.json, which is gitignored. Copy
private_data.example.json to private_data.json and fill in your own. Without
the file the app still works; it just recognises fewer transactions.
"""

import json
from functools import lru_cache
from pathlib import Path

PRIVATE_FILE = Path(__file__).resolve().parent.parent / "data" / "private_data.json"


@lru_cache(maxsize=1)
def load() -> dict:
    """{"funds": {name: {"keywords", "account", "asset_class"}},
    "merchants": {keyword: category}, "grocery_keywords": {shop: [keyword]}}.
    Read once; restart the app after editing the file."""
    data = {}
    if PRIVATE_FILE.exists():
        with open(PRIVATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    return {k: data.get(k, {}) for k in ("funds", "merchants", "grocery_keywords")}
