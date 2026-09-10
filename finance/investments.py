"""
investments.py — User-editable investment fund classification.

processor.py auto-detects each fund's asset class from a hardcoded guess
(FUND_ASSET_CLASS), but that guess can be wrong or simply unknown (e.g.
BBVA's export never names the actual fund). This module lets you override
the asset class per fund from the UI — and, for Fixed income funds, record
whether the interest is Fixed or Variable — and persists it so it sticks
across app restarts and future imports.

Stored in data/investment_classification.json.
"""

import json
from pathlib import Path

CLASSIFICATION_FILE = Path(__file__).resolve().parent.parent / "data" / "investment_classification.json"

ASSET_CLASSES  = ["Equity", "Fixed income", "Commodities", "Unspecified"]
INTEREST_TYPES = ["Fixed", "Variable"]


def load_overrides() -> dict:
    """Return {fund_name: {"asset_class": str, "interest_type": str|None}}."""
    if CLASSIFICATION_FILE.exists():
        with open(CLASSIFICATION_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_overrides(overrides: dict) -> None:
    with open(CLASSIFICATION_FILE, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)


def set_classification(fund_name: str, asset_class: str, interest_type: str | None = None) -> None:
    """Set (or update) the asset class for a fund — and, when it's Fixed
    income, its interest type (Fixed/Variable). Persists immediately."""
    overrides = load_overrides()
    entry = {"asset_class": asset_class}
    if asset_class == "Fixed income" and interest_type:
        entry["interest_type"] = interest_type
    overrides[fund_name] = entry
    save_overrides(overrides)


def clear_classification(fund_name: str) -> None:
    """Remove a fund's override, reverting it back to the auto-detected default."""
    overrides = load_overrides()
    if fund_name in overrides:
        del overrides[fund_name]
        save_overrides(overrides)


def get_classification(fund_name: str, default_asset_class: str = "Unspecified") -> tuple[str, str | None]:
    """Return (asset_class, interest_type) for a fund: the user's saved
    override if one exists, otherwise the given auto-detected default
    (with no interest type, since that can only ever come from the user)."""
    entry = load_overrides().get(fund_name)
    if entry:
        return entry.get("asset_class", default_asset_class), entry.get("interest_type")
    return default_asset_class, None
