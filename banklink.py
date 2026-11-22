"""
banklink.py — Pull transactions straight from the bank, instead of downloading
an export and uploading it by hand.

What this talks to
------------------
GoCardless Bank Account Data (formerly Nordigen), a licensed PSD2 account
information provider with a free tier that covers BBVA and most Spanish banks.
It is the bank's own open-banking interface, not screen-scraping: nothing here
ever sees, stores or transmits your online-banking password.

How authentication actually works, and why it is safe
-----------------------------------------------------
Two different secrets get confused here, so to be explicit:

  * Your BANK password is never involved. When you link an account you are sent
    to your own bank's website, you log in there, and the bank hands back a
    token. This module never sees that exchange.

  * Your GOCARDLESS API keys (a secret id and secret key) are yours, created in
    your own GoCardless account. They are read from a local file that you
    create — see SECRETS_FILE below — and are never written into this code, into
    the transaction file, or anywhere they could end up in a git repository.

Access is granted per-consent and expires: PSD2 caps it, typically at 90 days.
When it lapses the bank asks you to authorise again. That is the regulation
working, not a bug, and `connections()` reports how long each one has left so it
isn't a surprise.

Where synced transactions go
----------------------------
Straight into the SAME review flow as an uploaded file — parsed, categorised,
shown to you, and only saved when you accept them. They do not get written to
gastos_global.xlsx behind your back. Duplicate detection already keys on
(date, amount, description, source), so syncing the same period twice adds
nothing, which makes re-syncing a safe thing to do rather than a risk.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = "https://bankaccountdata.gocardless.com/api/v2"

# Your own API keys live here, outside the code. The file is a small JSON:
#     {"secret_id": "...", "secret_key": "..."}
# Environment variables GOCARDLESS_SECRET_ID / GOCARDLESS_SECRET_KEY take
# precedence if set, which is the tidier option on a shared machine.
SECRETS_FILE = Path(__file__).parent / "gocardless_secrets.json"

# Which banks are linked, and the tokens that came back. Also outside the code,
# and also not something to commit.
LINKS_FILE = Path(__file__).parent / "bank_links.json"

# How much history to ask the bank for on a first link. Most banks cap this at
# 90 days whatever you ask for; the app's duplicate detection means asking for
# more than you need costs nothing but a slower first sync.
DEFAULT_HISTORY_DAYS = 90

REQUEST_TIMEOUT = 30


class BankLinkError(RuntimeError):
    """Anything that went wrong talking to the bank, phrased for a human."""


# ── credentials ───────────────────────────────────────────────────────────────

def load_secrets() -> dict:
    """Your GoCardless API keys, from the environment or the local file."""
    env_id = os.environ.get("GOCARDLESS_SECRET_ID")
    env_key = os.environ.get("GOCARDLESS_SECRET_KEY")
    if env_id and env_key:
        return {"secret_id": env_id, "secret_key": env_key}
    if SECRETS_FILE.exists():
        try:
            with open(SECRETS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("secret_id") and raw.get("secret_key"):
                return {"secret_id": str(raw["secret_id"]),
                        "secret_key": str(raw["secret_key"])}
        except (json.JSONDecodeError, OSError) as exc:
            raise BankLinkError(
                f"{SECRETS_FILE.name} exists but could not be read ({exc}).") from exc
    return {}


def has_secrets() -> bool:
    try:
        return bool(load_secrets())
    except BankLinkError:
        return False


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _request(method: str, path: str, token: str | None = None,
             payload: dict | None = None) -> dict:
    """One API call. `requests` is imported lazily so that merely importing this
    module never breaks the app on a machine that hasn't installed it."""
    try:
        import requests
    except ImportError as exc:                       # pragma: no cover
        raise BankLinkError(
            "The `requests` package is needed to talk to the bank. "
            "Install it with:  pip install requests") from exc

    url = f"{BASE}{path}"
    headers = {"accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        resp = requests.request(method, url, headers=headers, json=payload,
                                timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        raise BankLinkError(f"Could not reach the bank service: {exc}") from exc

    if resp.status_code == 401:
        raise BankLinkError(
            "The bank service rejected your API keys. Check secret_id and "
            "secret_key in " + SECRETS_FILE.name + ".")
    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json()
            detail = (body.get("detail") or body.get("summary")
                      or json.dumps(body)[:200])
        except Exception:
            detail = resp.text[:200]
        raise BankLinkError(f"Bank service returned {resp.status_code}: {detail}")
    if not resp.content:
        return {}
    return resp.json()


def get_token() -> str:
    s = load_secrets()
    if not s:
        raise BankLinkError(
            "No API keys found. Create a free account at "
            "bankaccountdata.gocardless.com, then save your keys as "
            f"{SECRETS_FILE.name} next to this file: "
            '{"secret_id": "...", "secret_key": "..."}')
    data = _request("POST", "/token/new/", payload=s)
    tok = data.get("access")
    if not tok:
        raise BankLinkError("The bank service did not return an access token.")
    return tok


# ── linking a bank ────────────────────────────────────────────────────────────

def institutions(country: str = "es", token: str | None = None) -> list[dict]:
    """Banks available in a country, as [{"id", "name", "logo"}, ...]."""
    token = token or get_token()
    data = _request("GET", f"/institutions/?country={country}", token=token)
    out = [{"id": i.get("id"), "name": i.get("name"),
            "logo": i.get("logo"),
            "days": i.get("transaction_total_days")} for i in data or []]
    return sorted(out, key=lambda i: (i["name"] or "").lower())


def start_link(institution_id: str, redirect: str = "https://localhost",
               history_days: int = DEFAULT_HISTORY_DAYS,
               reference: str | None = None, token: str | None = None) -> dict:
    """
    Begin linking one bank. Returns {"id", "link"} — open `link` in a browser.

    That link goes to the BANK, not to this app: you log in there and approve
    the access. Nothing in this process passes a banking credential through this
    code.
    """
    token = token or get_token()
    agreement = _request("POST", "/agreements/enduser/", token=token, payload={
        "institution_id": institution_id,
        "max_historical_days": history_days,
        "access_valid_for_days": 90,
        "access_scope": ["balances", "details", "transactions"],
    })
    req = _request("POST", "/requisitions/", token=token, payload={
        "redirect": redirect,
        "institution_id": institution_id,
        "agreement": agreement.get("id"),
        "reference": reference or f"finanzas-{datetime.now():%Y%m%d%H%M%S}",
        "user_language": "EN",
    })
    if not req.get("link"):
        raise BankLinkError("The bank service did not return an authorisation link.")
    return {"id": req.get("id"), "link": req.get("link"),
            "institution_id": institution_id,
            "agreement": agreement.get("id"),
            "created": datetime.now().isoformat(timespec="seconds")}


# ── stored links ──────────────────────────────────────────────────────────────

def load_links() -> list[dict]:
    if not LINKS_FILE.exists():
        return []
    try:
        with open(LINKS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_links(links: list[dict]) -> None:
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, indent=2, ensure_ascii=False)


def remember_link(link: dict, source_name: str) -> None:
    """Store a requisition against the account label it should import as, so
    synced rows land under the same Source you already use ("BBVA")."""
    links = [l for l in load_links() if l.get("id") != link.get("id")]
    links.append({**link, "source": source_name})
    save_links(links)


def forget_link(requisition_id: str) -> None:
    save_links([l for l in load_links() if l.get("id") != requisition_id])


def connections(token: str | None = None) -> list[dict]:
    """Every stored link with its current state and how long the consent lasts.

    A link whose consent has lapsed is reported, not hidden: silently syncing
    nothing is how you end up trusting a feed that stopped working weeks ago.
    """
    links = load_links()
    if not links:
        return []
    token = token or get_token()
    out = []
    for l in links:
        row = {**l, "accounts": [], "status": "unknown", "expires_in": None,
               "error": None}
        try:
            req = _request("GET", f"/requisitions/{l['id']}/", token=token)
            row["status"] = req.get("status")
            row["accounts"] = req.get("accounts") or []
            agr = l.get("agreement")
            if agr:
                a = _request("GET", f"/agreements/enduser/{agr}/", token=token)
                accepted = a.get("accepted")
                valid = a.get("access_valid_for_days")
                if accepted and valid:
                    when = datetime.fromisoformat(str(accepted).replace("Z", "+00:00"))
                    left = (when.date() + timedelta(days=int(valid))) - date.today()
                    row["expires_in"] = left.days
        except BankLinkError as exc:
            row["error"] = str(exc)
        out.append(row)
    return out


# ── fetching ──────────────────────────────────────────────────────────────────

def _pick(d: dict, *names, default=""):
    for n in names:
        v = d.get(n)
        if v not in (None, ""):
            return v
    return default


def normalise(tx: dict) -> dict | None:
    """
    One bank transaction in the shape the rest of this app speaks.

    Banks disagree about field names — some send `bookingDate`, some
    `valueDate`; the counterparty may be in `creditorName`, `debtorName` or only
    inside the free-text `remittanceInformationUnstructured`. Everything is
    tried in turn rather than assuming one bank's spelling, because the first
    bank you test against is never the only one you end up using.
    """
    amt = tx.get("transactionAmount") or {}
    try:
        amount = float(amt.get("amount"))
    except (TypeError, ValueError):
        return None
    when = str(_pick(tx, "bookingDate", "valueDate", "bookingDateTime",
                     "valueDateTime"))[:10]
    if len(when) != 10:
        return None

    unstructured = tx.get("remittanceInformationUnstructured")
    if not unstructured:
        arr = tx.get("remittanceInformationUnstructuredArray")
        if isinstance(arr, list) and arr:
            unstructured = " ".join(str(x) for x in arr)
    party = _pick(tx, "creditorName", "debtorName")
    desc = " ".join(str(x).strip() for x in (party, unstructured) if x).strip()
    if not desc:
        desc = str(_pick(tx, "additionalInformation",
                         "proprietaryBankTransactionCode", default="Transaction"))
    return {"date": when, "amount": amount, "description": " ".join(desc.split())}


def fetch_transactions(account_id: str, date_from: str | None = None,
                       token: str | None = None) -> list[dict]:
    """Booked transactions for one account, normalised, oldest first.

    Only BOOKED transactions. Pending ones change amount or vanish before they
    settle, and a pending row imported today becomes a near-duplicate of the
    booked row tomorrow — different enough to slip past duplicate detection,
    which is exactly the kind of quiet corruption this app is built to avoid.
    """
    token = token or get_token()
    path = f"/accounts/{account_id}/transactions/"
    if date_from:
        path += f"?date_from={date_from}"
    data = _request("GET", path, token=token)
    booked = ((data.get("transactions") or {}).get("booked")) or []
    out = [n for n in (normalise(t) for t in booked) if n]
    out.sort(key=lambda r: r["date"])
    return out


def sync(requisition_id: str, date_from: str | None = None,
         token: str | None = None) -> dict:
    """
    Everything one linked bank currently has, across all of its accounts.

    Returns {"source", "rows", "accounts", "errors"}. Per-account failures are
    collected rather than raised: one account being unavailable should not throw
    away the transactions the others returned.
    """
    token = token or get_token()
    link = next((l for l in load_links() if l.get("id") == requisition_id), None)
    if link is None:
        raise BankLinkError("That bank link is not stored on this machine.")
    req = _request("GET", f"/requisitions/{requisition_id}/", token=token)
    accounts = req.get("accounts") or []
    if not accounts:
        raise BankLinkError(
            "This link has no accounts yet — the bank authorisation may not "
            "have been completed. Open the link again and finish signing in.")
    rows, errors = [], []
    for acc in accounts:
        try:
            rows.extend(fetch_transactions(acc, date_from, token=token))
        except BankLinkError as exc:
            errors.append(f"{acc}: {exc}")
    rows.sort(key=lambda r: r["date"])
    return {"source": link.get("source") or "Bank", "rows": rows,
            "accounts": accounts, "errors": errors}


def to_csv(rows: list[dict], path: str | Path) -> Path:
    """
    Write fetched rows as a plain CSV the existing importer can read.

    Deliberately routed through a file and the normal parser rather than
    injected straight into the review table. The upload path is the one that is
    already tested, already handles the column detection, and already feeds the
    categorise-and-review flow — a second, parallel way into the transaction
    file is exactly how the two paths drift apart and one of them quietly stops
    matching the other.
    """
    import csv
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Amount", "Description"])
        for r in rows:
            w.writerow([r["date"], f"{r['amount']:.2f}", r["description"]])
    return path
