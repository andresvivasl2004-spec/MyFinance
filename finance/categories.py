"""
categories.py — Persistent, user-editable category list.

Categories are stored in data/categories.json at the project root.
If the file doesn't exist, the default list is used and written on first save.
"""

import json
from pathlib import Path

CATEGORIES_FILE = Path(__file__).resolve().parent.parent / "data" / "categories.json"

DEFAULT_CATEGORIES = [
    "Groceries",
    "Dining & Food",
    "Transport",
    "Gas",
    "Health & Beauty",
    "Gym",
    "Entertainment",
    "Shopping",
    "Education",
    "Rent",
    "Electricity",
    "Water",
    "Phone",
    "Social & Gifts",
    "Fees & Tax",
    "Investments",
    "Income",
    "Interest",
    "Self-transfer",
    "Personal Care",
    "Other",
]


def load_categories() -> list[str]:
    """Return the current category list (from file, or defaults)."""
    if CATEGORIES_FILE.exists():
        with open(CATEGORIES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_CATEGORIES.copy()


def save_categories(cats: list[str]) -> None:
    """Persist the category list, preserving order."""
    # Deduplicate while keeping order
    seen, unique = set(), []
    for c in cats:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)


def add_category(name: str) -> list[str]:
    """Add a category if it doesn't already exist. Returns updated list."""
    cats = load_categories()
    name = name.strip()
    if name and name not in cats:
        cats.append(name)
        save_categories(cats)
    return load_categories()


def delete_category(name: str) -> list[str]:
    """Remove a category. Returns updated list."""
    cats = [c for c in load_categories() if c != name]
    save_categories(cats)
    return cats


def rename_category(old: str, new: str) -> list[str]:
    """Rename a category. Returns updated list."""
    cats = [new.strip() if c == old else c for c in load_categories()]
    save_categories(cats)
    return cats


def reset_to_defaults() -> list[str]:
    """Restore the default category list."""
    save_categories(DEFAULT_CATEGORIES.copy())
    return DEFAULT_CATEGORIES.copy()
