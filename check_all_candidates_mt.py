"""
Check Meta Ad Library for ALL Malta candidates in candidates_mt.csv
Separate from Cyprus pipeline — uses politician_ads_mt.db

Usage:
    python check_all_candidates_mt.py
    python check_all_candidates_mt.py --start 10
    python check_all_candidates_mt.py --parties PN PL
    python check_all_candidates_mt.py --full
"""

import os
import sys
import csv
import json
import sqlite3
import argparse
import re
import requests
import time
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(override=True)

DB_PATH             = "politician_ads_mt.db"
META_AD_LIBRARY_URL = "https://graph.facebook.com/v25.0/ads_archive"
CANDIDATES_FILE     = "candidates_mt.csv"
FETCH_STATE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_state_mt.json")
BLOCKLIST_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_blocklist_mt.json")
DEFAULT_START       = "2025-09-01"
OVERLAP_DAYS        = 7

COUNTRY = "MT"


# ─── Fetch-state helpers ──────────────────────────────────────────────────────

def load_fetch_since(script_key: str) -> str:
    if os.path.exists(FETCH_STATE_FILE):
        try:
            with open(FETCH_STATE_FILE, encoding='utf-8') as f:
                state = json.load(f)
            last_str = state.get(script_key)
            if last_str:
                last_date = date.fromisoformat(last_str)
                since = max(
                    date.fromisoformat(DEFAULT_START),
                    last_date - timedelta(days=OVERLAP_DAYS),
                )
                return since.isoformat()
        except Exception as e:
            print(f"[state] Could not read {FETCH_STATE_FILE}: {e}")
    return DEFAULT_START


def save_fetch_date(script_key: str):
    state = {}
    if os.path.exists(FETCH_STATE_FILE):
        try:
            with open(FETCH_STATE_FILE, encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            pass
    state[script_key] = date.today().isoformat()
    with open(FETCH_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
    print(f"[state] Saved last-run date for '{script_key}': {state[script_key]}")


def load_chunk_pos(script_key: str) -> int:
    """Return the next candidate index to process for chunked runs."""
    if os.path.exists(FETCH_STATE_FILE):
        try:
            with open(FETCH_STATE_FILE, encoding='utf-8') as f:
                return int(json.load(f).get(f"{script_key}_chunk_pos", 0))
        except Exception:
            pass
    return 0


def save_chunk_pos(script_key: str, pos: int):
    """Persist the next chunk start position."""
    state = {}
    if os.path.exists(FETCH_STATE_FILE):
        try:
            with open(FETCH_STATE_FILE, encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            pass
    state[f"{script_key}_chunk_pos"] = pos
    try:
        with open(FETCH_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
        print(f"[state] Chunk position for '{script_key}': {pos}")
    except Exception as e:
        print(f"[state] WARNING: could not save chunk position for '{script_key}': {e}")


# ─── Blocklist ────────────────────────────────────────────────────────────────

def load_blocklist() -> set:
    if os.path.exists(BLOCKLIST_FILE):
        with open(BLOCKLIST_FILE, encoding='utf-8') as f:
            return set(json.load(f).get('pages', {}).keys())
    return set()

PAGE_BLOCKLIST = load_blocklist()


# ─── Party terms ─────────────────────────────────────────────────────────────

PARTY_TERMS = {
    "PN":              ["pn", "nationalist", "partit nazzjonalista"],
    "PL":              ["pl", "labour", "partit laburista", "laburista"],
    "Momentum":        ["momentum"],
    "ADPD":            ["adpd", "alternattiva demokratika"],
    "Imperium Ewropa": ["imperium ewropa", "imperium"],
    "Ahwa Maltin":     ["ahwa maltin", "partit popolari", "people's party"],
    "Independent":     ["independent", "indipendenti", "indipendent"],
}


# ─── Database ─────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS politician_ads (
            ad_archive_id    TEXT PRIMARY KEY,
            politician_query TEXT NOT NULL,
            party            TEXT,
            district         TEXT,
            page_name        TEXT,
            page_id          TEXT,
            bylines          TEXT,
            is_third_party   INTEGER,
            ad_start_date    TEXT,
            ad_stop_date     TEXT,
            impressions_min  INTEGER,
            impressions_max  INTEGER,
            spend_min        INTEGER,
            spend_max        INTEGER,
            currency         TEXT,
            snapshot_url     TEXT,
            checked_at       TEXT NOT NULL,
            source           TEXT DEFAULT 'mt',
            removed          INTEGER DEFAULT 0,
            removed_checked_at TEXT,
            ad_text          TEXT,
            first_seen_at    TEXT
        )
    """)
    # Migrate existing DBs that predate first_seen_at
    cols = {r[1] for r in conn.execute("PRAGMA table_info(politician_ads)").fetchall()}
    if "first_seen_at" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN first_seen_at TEXT")
        print("[db] Added column: first_seen_at")
    if "election_related" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN election_related TEXT")
        print("[db] Added column: election_related")
    conn.commit()
    conn.close()


REMOVAL_TEXT = "this content was removed because it didn't follow our advertising standards"


def _detect_removal(ad: dict) -> tuple[int, str | None]:
    """
    Inspect ad_creative_bodies for the removal notice Meta injects when an ad
    violates Advertising Standards.  Returns (removed_int, checked_at_or_None).

    - bodies present, removal text found  → (1, now)
    - bodies present, no removal text     → (0, now)   ← confirmed active
    - bodies absent (API returned nothing)→ (0, None)  ← unknown; don't stamp
    """
    bodies = ad.get("ad_creative_bodies") or []
    now = datetime.now(timezone.utc).isoformat()
    if bodies:
        is_removed = any(REMOVAL_TEXT in (b or "").lower() for b in bodies)
        return (1, now) if is_removed else (0, now)
    return (0, None)


def upsert_ads(ads: list[dict]) -> int:
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for ad in ads:
        imp   = ad.get("impressions", {})
        spend = ad.get("spend", {})
        try:
            raw_bodies = ad.get("ad_creative_bodies") or []
            titles     = ad.get("ad_creative_link_titles") or []
            bodies_str = " ".join(raw_bodies)
            titles_str = " ".join(titles)
            ad_text = (bodies_str + " " + titles_str).strip()[:1000] or None

            removed_val, removed_checked = _detect_removal(ad)

            conn.execute("""
                INSERT INTO politician_ads
                    (ad_archive_id, politician_query, party, district,
                     page_name, page_id, bylines, is_third_party,
                     ad_start_date, ad_stop_date,
                     impressions_min, impressions_max,
                     spend_min, spend_max, currency,
                     snapshot_url, checked_at, source,
                     removed, removed_checked_at, ad_text,
                     first_seen_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ad_archive_id) DO UPDATE SET
                    impressions_min     = excluded.impressions_min,
                    impressions_max     = excluded.impressions_max,
                    spend_min           = excluded.spend_min,
                    spend_max           = excluded.spend_max,
                    ad_stop_date        = excluded.ad_stop_date,
                    page_name           = excluded.page_name,
                    checked_at          = excluded.checked_at,
                    ad_text             = excluded.ad_text,
                    -- Removal: upgrade to 1 if newly detected; never downgrade 1→0
                    removed             = CASE
                                           WHEN excluded.removed = 1 THEN 1
                                           ELSE politician_ads.removed
                                         END,
                    removed_checked_at  = CASE
                                           WHEN excluded.removed_checked_at IS NOT NULL
                                           THEN excluded.removed_checked_at
                                           ELSE politician_ads.removed_checked_at
                                         END
                    -- first_seen_at intentionally NOT updated
            """, (
                ad.get("id"),
                ad.get("_query"),
                ad.get("_party"),
                ad.get("_district"),
                ad.get("page_name"),
                ad.get("page_id"),
                ad.get("bylines"),
                1 if ad.get("is_third_party") else 0,
                ad.get("ad_delivery_start_time"),
                ad.get("ad_delivery_stop_time"),
                imp.get("lower_bound"),
                imp.get("upper_bound"),
                spend.get("lower_bound"),
                spend.get("upper_bound"),
                ad.get("currency"),
                ad.get("ad_snapshot_url"),
                now,
                'mt',
                removed_val,
                removed_checked,
                ad_text,
                now,   # first_seen_at — set once on first INSERT only
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"    [db] skipped {ad.get('id')}: {e}")
    conn.commit()
    conn.close()
    return inserted


# ─── API ──────────────────────────────────────────────────────────────────────

def _fetch_raw(params: dict) -> list[dict]:
    all_ads = []
    url = META_AD_LIBRARY_URL
    retries = 0
    MAX_RETRIES = 2
    try:
        while url:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                try:
                    err_body = resp.json().get('error', {})
                    err_code = err_body.get('code', 0)
                    err_msg  = err_body.get('message', resp.text[:200])
                except Exception:
                    err_code = 0
                    err_msg  = resp.text[:200]
                # Rate limit: sleep and retry
                if err_code == 613 and retries < MAX_RETRIES:
                    wait = 60 * (2 ** retries)
                    print(f"    [rate limit] sleeping {wait}s then retrying…")
                    time.sleep(wait)
                    retries += 1
                    continue
                print(f"    [!] API error {resp.status_code}: {err_msg}")
                break
            retries = 0
            data = resp.json()
            all_ads.extend(data.get("data", []))
            next_url = data.get("paging", {}).get("next")
            url = next_url
            params = {}
            if next_url:
                time.sleep(0.5)
    except requests.RequestException as e:
        print(f"    [!] Request failed: {e}")
    return all_ads


def _base_params(since_date: str) -> dict:
    return {
        "ad_type": "ALL",
        "ad_reached_countries": json.dumps([COUNTRY]),
        "ad_delivery_date_min": since_date,
        "fields": (
            "id,page_name,page_id,bylines,"
            "ad_delivery_start_time,ad_delivery_stop_time,"
            "impressions,spend,currency,ad_snapshot_url,"
            "ad_creative_bodies,ad_creative_link_titles"
        ),
        "limit": 100,
        "access_token": os.environ.get("META_ACCESS_TOKEN"),
    }


def fetch_ads(name: str, page_ids: list[str], since_date: str) -> tuple[list[dict], list[dict]]:
    base = _base_params(since_date)

    # 1. Page-ID search
    page_id_ads: list[dict] = []
    for pid in page_ids:
        p = dict(base)
        p["search_page_ids"] = json.dumps([pid])
        ads = _fetch_raw(p)
        page_id_ads.extend(ads)
        if pid != page_ids[-1]:
            time.sleep(0.5)

    # 2. Name search
    p = dict(base)
    p["search_terms"] = name
    name_ads = _fetch_raw(p)

    return page_id_ads, name_ads


# ─── Report ───────────────────────────────────────────────────────────────────

def print_report():
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM politician_ads").fetchone()[0]

    print("\n" + "="*60)
    print("REPORT -- MALTA CANDIDATE ADS")
    print("="*60)
    print(f"Total ads found: {total}")

    print("\n─── Per Candidate ───────────────────────────────────────")
    rows = conn.execute("""
        SELECT politician_query, party, district,
               COUNT(*) as total_ads,
               MAX(impressions_max) as max_impressions
        FROM politician_ads
        GROUP BY politician_query
        ORDER BY total_ads DESC
    """).fetchall()

    for r in rows:
        query, party, district, total, imp = r
        name = query.split("|")[0]
        print(f"\n  {name} ({party or '?'})")
        print(f"    Ads: {total}")
        if imp:
            print(f"    Max impressions: {imp:,}")

    conn.close()
    print("\n" + "="*60)
    print(f"Full data saved to: {DB_PATH}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parties", nargs="+", help="Only process these parties")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--full", action="store_true", help="Fetch from DEFAULT_START ignoring saved state")
    parser.add_argument("--chunk-size", type=int, default=0,
                        help="Process N candidates per run, auto-advancing position each run (0=all, default: 0)")
    args = parser.parse_args()

    if not os.path.exists(CANDIDATES_FILE):
        sys.exit(f"Error: {CANDIDATES_FILE} not found.")

    init_db()

    with open(CANDIDATES_FILE, encoding="utf-8") as f:
        candidates = list(csv.DictReader(f))

    if args.parties:
        parties_filter = [p.strip() for p in args.parties]
        candidates = [c for c in candidates if c.get("party", "").strip() in parties_filter]
        print(f"\nFiltering to parties: {', '.join(parties_filter)}")

    # ── Chunk mode: auto-advance through candidate list across runs ───────────
    chunk_complete = False   # set True when this run finishes the final chunk
    total_candidates = len(candidates)
    if args.chunk_size > 0:
        chunk_pos = load_chunk_pos("mt")
        # Guard: if chunk_pos is beyond the list (e.g. list shrank), reset
        if chunk_pos >= total_candidates:
            print(f"[chunk] Position {chunk_pos} beyond list size {total_candidates} — resetting to 0")
            chunk_pos = 0
        chunk_end = chunk_pos + args.chunk_size
        chunk = candidates[chunk_pos:chunk_end]
        if not chunk:
            print("[chunk] Empty chunk — skipping scan this run, position unchanged.")
            save_chunk_pos("mt", chunk_pos)
            return
        next_pos = chunk_end if chunk_end < len(candidates) else 0
        print(f"\n[chunk] Position {chunk_pos}–{chunk_pos + len(chunk) - 1} of {len(candidates)} "
              f"(next run starts at {next_pos if next_pos else 0})")
        candidates = chunk
        if next_pos == 0:
            chunk_complete = True   # full cycle done — advance since_date
    elif args.start > 1:
        candidates = candidates[args.start - 1:]
        print(f"\nStarting from candidate #{args.start}")

    since_date = DEFAULT_START if args.full else load_fetch_since("mt")
    print(f"\nChecking {len(candidates)} Malta candidates (country: {COUNTRY})")
    print(f"Fetching ads from: {since_date}{'  [--full mode]' if args.full else '  (incremental)'}\n")

    for i, c in enumerate(candidates, 1):
        name     = c.get("name", "").strip()
        page_id  = c.get("page_id", "").strip()
        party    = c.get("party", "").strip()
        district = c.get("district", "").strip()

        if not name:
            continue

        page_ids = [p.strip() for p in page_id.split(",") if p.strip()]

        print(f"[{i}/{len(candidates)}] {name} ({party})"
              + (f"  [{len(page_ids)} page IDs]" if page_ids else ""))

        page_id_ads, name_ads = fetch_ads(name, page_ids, since_date)

        # Relevance filter
        name_parts  = [p for p in name.lower().split() if len(p) > 3]
        party_terms = PARTY_TERMS.get(party, [party.lower()] if party else [])

        def ad_is_relevant(ad):
            if str(ad.get("page_id") or "") in PAGE_BLOCKLIST:
                return False
            page   = (ad.get("page_name") or "").lower()
            bodies = " ".join(ad.get("ad_creative_bodies") or []).lower()
            titles = " ".join(ad.get("ad_creative_link_titles") or []).lower()
            text   = bodies + " " + titles
            if any(p in page for p in name_parts):
                return True
            all_name_in_text = (
                all(re.search(r'(?<!\w)' + re.escape(p) + r'(?!\w)', text)
                    for p in name_parts)
                if name_parts else False
            )
            party_in_text = any(t in text for t in party_terms) if party_terms else True
            return all_name_in_text and party_in_text

        before   = len(name_ads)
        name_ads = [ad for ad in name_ads if ad_is_relevant(ad)]
        filtered = before - len(name_ads)
        if filtered:
            print(f"    (filtered {filtered} irrelevant ads)")

        # Also filter page_id_ads through blocklist
        page_id_ads = [ad for ad in page_id_ads
                       if str(ad.get("page_id") or "") not in PAGE_BLOCKLIST]

        # Merge & deduplicate
        seen: set = set()
        all_ads:  list = []
        for ad in page_id_ads + name_ads:
            aid = ad.get("id")
            if aid and aid not in seen:
                seen.add(aid)
                all_ads.append(ad)

        for ad in all_ads:
            ad["_query"]    = f"{name}|{party}|{district}"
            ad["_party"]    = party
            ad["_district"] = district
            ad["is_third_party"] = 0

        saved = upsert_ads(all_ads)
        print(f"    page_id: {len(page_id_ads)} ads  |  name: {len(name_ads)} ads"
              f"  →  {len(all_ads)} unique  ({saved} saved to DB)")

        if i < len(candidates):
            time.sleep(20)

    # ── Save state ────────────────────────────────────────────────────────────
    if args.chunk_size > 0:
        save_chunk_pos("mt", next_pos)
        if chunk_complete:
            save_fetch_date("mt")
            print("[state] Full cycle complete — since_date advanced.")
        else:
            print(f"[state] Partial cycle — since_date unchanged until all {total_candidates} done.")
    else:
        save_fetch_date("mt")

    print_report()


if __name__ == "__main__":
    main()
