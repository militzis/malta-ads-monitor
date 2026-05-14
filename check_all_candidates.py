"""
Check Meta Ad Library for ALL candidates in candidates.csv
Generates a full sponsorship report.

Usage:
    python check_all_candidates.py
    python check_all_candidates.py --country CY
"""

import os
import sys
import csv
import json
import sqlite3
import argparse
import requests
import time
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv

# Force UTF-8 output so Greek names print correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(override=True)

DB_PATH          = "politician_ads.db"
META_AD_LIBRARY_URL = "https://graph.facebook.com/v25.0/ads_archive"
CANDIDATES_FILE  = "candidates.csv"
FETCH_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_state.json")
BLOCKLIST_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_blocklist.json")
DEFAULT_START    = "2025-09-01"   # hard floor — never fetch before this date
OVERLAP_DAYS     = 7              # re-fetch last N days to catch updated impressions/spend

def load_blocklist() -> set:
    if os.path.exists(BLOCKLIST_FILE):
        with open(BLOCKLIST_FILE, encoding='utf-8') as f:
            return set(json.load(f).get('pages', {}).keys())
    return set()

PAGE_BLOCKLIST = load_blocklist()


# ─── Fetch-state helpers ─────────────────────────────────────────────────────

def load_fetch_since(script_key: str) -> str:
    """Return the date to pass as ad_delivery_date_min.
    On first run returns DEFAULT_START.
    On subsequent runs returns (last_run - OVERLAP_DAYS) so updated ad stats
    are refreshed, but we don't re-pull the entire history.
    """
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
    """Persist today's date as the last-run date for this script."""
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
    with open(FETCH_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
    print(f"[state] Chunk position for '{script_key}': {pos}")

# Search terms used to match party mentions in ad text
# Multiple terms per party — any match counts as relevant
PARTY_TERMS = {
    "ΔΗΣΥ":       ["δησυ", "δημοκρατικός συναγερμός", "disy"],
    "ΑΚΕΛ":       ["ακελ", "akel"],
    "ΔΗΚΟ":       ["δηκο", "δημοκρατικό κόμμα", "diko"],
    "ΕΔΕΚ":       ["εδεκ", "edek", "σοσιαλδημοκράτες"],
    "ΕΛΑΜ":       ["ελαμ", "εθνικό λαϊκό μέτωπο", "elam"],
    "ΑΜΔΗ":       ["αμδη", "άμεση δημοκρατία", "αδ"],
    "ΒΟΛΤ":       ["βολτ", "volt"],
    "ΔΕΚ":        ["δεκ", "δημοκρατικό εθνικό κίνημα"],
    "ΟΙΚΟΛΟΓΟΙ":  ["οικολόγοι", "πράσινοι", "οικολογικό", "κίνημα οικολόγων-συνεργασία πολιτών"],
    "ΑΛΜΑ":       ["αλμα"],
    "ΔΗΠΑ":       ["δηπα", "δημοκρατική παράταξη"],
    "ΣΗΚΟΥ ΠΑΝΩ": ["σήκου πάνω"],
}


# ─── Database ────────────────────────────────────────────────────────────────

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
            source           TEXT DEFAULT 'greek',
            removed          INTEGER DEFAULT 0,
            removed_checked_at TEXT,
            ad_text          TEXT
        )
    """)
    # Add columns to existing DBs that pre-date this change
    cols = [r[1] for r in conn.execute("PRAGMA table_info(politician_ads)").fetchall()]
    if 'source' not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN source TEXT DEFAULT 'greek'")
    if 'removed' not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN removed INTEGER DEFAULT 0")
    if 'removed_checked_at' not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN removed_checked_at TEXT")
    if 'ad_text' not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN ad_text TEXT")
    if 'first_seen_at' not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN first_seen_at TEXT")
    if 'election_related' not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN election_related TEXT")
    conn.commit()
    conn.close()


def upsert_ads(ads: list[dict]) -> int:
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for ad in ads:
        imp = ad.get("impressions", {})
        spend = ad.get("spend", {})
        try:
            # Build ad_text from creative fields (stored for AI classification)
            bodies = " ".join(ad.get("ad_creative_bodies") or [])
            titles = " ".join(ad.get("ad_creative_link_titles") or [])
            ad_text = (bodies + " " + titles).strip()[:1000] or None

            conn.execute("""
                INSERT INTO politician_ads
                    (ad_archive_id, politician_query, party, district,
                     page_name, page_id, bylines, is_third_party,
                     ad_start_date, ad_stop_date,
                     impressions_min, impressions_max,
                     spend_min, spend_max, currency,
                     snapshot_url, checked_at, source,
                     removed, removed_checked_at, ad_text)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ad_archive_id) DO UPDATE SET
                    impressions_min     = excluded.impressions_min,
                    impressions_max     = excluded.impressions_max,
                    spend_min           = excluded.spend_min,
                    spend_max           = excluded.spend_max,
                    ad_stop_date        = excluded.ad_stop_date,
                    page_name           = excluded.page_name,
                    checked_at          = excluded.checked_at,
                    ad_text             = excluded.ad_text
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
                'greek',
                0,    # removed (only for new rows)
                None, # removed_checked_at (only for new rows)
                ad_text,
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"    [db] skipped ad {ad.get('id')}: {e}")
    conn.commit()
    conn.close()
    return inserted


# ─── API ─────────────────────────────────────────────────────────────────────

def _fetch_raw(params: dict) -> list[dict]:
    """Paginate through a single API query and return all ads."""
    all_ads = []
    url = META_AD_LIBRARY_URL
    try:
        while url:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                print(f"    [!] API error {resp.status_code}: {resp.json().get('error', {}).get('message')}")
                break
            data = resp.json()
            all_ads.extend(data.get("data", []))
            next_url = data.get("paging", {}).get("next")
            url      = next_url
            params   = {}   # next_url already contains all params
            if next_url:
                time.sleep(0.5)
    except requests.RequestException as e:
        print(f"    [!] Request failed: {e}")
    return all_ads


def _base_params(country: str, since_date: str) -> dict:
    return {
        "ad_type": "ALL",
        "ad_reached_countries": json.dumps([country]),
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


def fetch_ads(name: str, page_ids: list[str], country: str,
              since_date: str = DEFAULT_START) -> tuple[list[dict], list[dict]]:
    """
    Fetch ads for a candidate using both strategies:
      1. Direct page-ID lookup for each known page (precise).
      2. Name search (catches third-party / party ads).

    Returns (page_id_ads, name_ads) — kept separate so callers know the source.
    Deduplication by ad_archive_id is handled by the caller.

    NOTE: POLITICAL_AND_ISSUE_ADS is blocked for all EU countries (Meta error #100).
    ad_type=ALL works for Cyprus and returns impressions/spend data.
    """
    base = _base_params(country, since_date)

    # 1. Page-ID search (one call per page ID)
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
    third_party = conn.execute(
        "SELECT COUNT(*) FROM politician_ads WHERE is_third_party = 1"
    ).fetchone()[0]

    print("\n" + "="*60)
    print("FULL REPORT -- CANDIDATE AD SPONSORSHIP")
    print("="*60)
    print(f"Total ads found:          {total}")
    print(f"(Note: 'Paid for by' data unavailable via Meta API for EU countries)")

    print("\n─── Per Candidate ───────────────────────────────────────")
    rows = conn.execute("""
        SELECT politician_query, party, district,
               COUNT(*) as total_ads,
               SUM(is_third_party) as third_party_ads,
               MAX(impressions_max) as max_impressions
        FROM politician_ads
        GROUP BY politician_query
        ORDER BY total_ads DESC
    """).fetchall()

    for r in rows:
        query, party, district, total, tp, imp = r
        # Strip the |party|district suffix added to make keys unique
        name = query.split("|")[0]
        print(f"\n  {name} ({party or '?'} — {district or '?'})")
        print(f"    Ads: {total}")
        if imp:
            print(f"    Max impressions: {imp:,}")

    print("\n─── Top Funders (bylines) ───────────────────────────────")
    funders = conn.execute("""
        SELECT bylines, COUNT(*) as ads, politician_query
        FROM politician_ads
        WHERE bylines IS NOT NULL AND bylines != ''
        GROUP BY bylines
        ORDER BY ads DESC
        LIMIT 10
    """).fetchall()

    for funder, count, candidate in funders:
        print(f"  {funder:<40} {count} ads  ({candidate})")

    conn.close()
    print("\n" + "="*60)
    print(f"Full data saved to: {DB_PATH}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", default="CY", help="Two-letter country code (default: CY)")
    parser.add_argument("--parties", nargs="+", help="Only process these parties (e.g. --parties ΑΛΜΑ ΔΗΠΑ 'ΣΗΚΟΥ ΠΑΝΩ')")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and fetch everything from DEFAULT_START")
    parser.add_argument("--candidates-file", default=CANDIDATES_FILE, help="Path to candidates CSV (default: candidates.csv)")
    parser.add_argument("--start", type=int, default=1, help="Start from candidate number N (default: 1, manual override)")
    parser.add_argument("--chunk-size", type=int, default=0,
                        help="Process N candidates per run, auto-advancing position each run (0=all, default: 0)")
    args = parser.parse_args()

    candidates_file = args.candidates_file
    if not os.path.exists(candidates_file):
        sys.exit(f"Error: {candidates_file} not found. Create it first.")

    init_db()

    with open(candidates_file, encoding="utf-8") as f:
        candidates = list(csv.DictReader(f))

    # Filter by party if --parties was given
    if args.parties:
        parties_filter = [p.strip() for p in args.parties]
        candidates = [c for c in candidates if c.get("party", "").strip() in parties_filter]
        print(f"\nFiltering to parties: {', '.join(parties_filter)}")

    # ── Chunk mode: auto-advance through candidate list across runs ───────────
    chunk_complete = False   # set True when this run finishes the final chunk
    total_candidates = len(candidates)
    if args.chunk_size > 0:
        chunk_pos = load_chunk_pos("greek")
        # Guard: if chunk_pos is beyond the list (e.g. list shrank), reset
        if chunk_pos >= total_candidates:
            print(f"[chunk] Position {chunk_pos} beyond list size {total_candidates} — resetting to 0")
            chunk_pos = 0
        chunk_end = chunk_pos + args.chunk_size
        chunk = candidates[chunk_pos:chunk_end]
        if not chunk:
            print("[chunk] Empty chunk — skipping scan this run, position unchanged.")
            save_chunk_pos("greek", chunk_pos)
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

    # ── Incremental: only fetch ads newer than the last run ───────────────────
    # In chunk mode, since_date is fixed for the whole cycle (only updated when
    # the cycle completes), so all chunks query the same date window.
    since_date = DEFAULT_START if args.full else load_fetch_since("greek")
    print(f"\nChecking {len(candidates)} candidates (country: {args.country})")
    print(f"Fetching ads from: {since_date}{'  [--full mode]' if args.full else '  (incremental)'}\n")

    for i, c in enumerate(candidates, 1):
        name     = c.get("name", "").strip()
        page_id  = c.get("page_id", "").strip()
        party    = c.get("party", "").strip()
        district = c.get("district", "").strip()

        if not name:
            continue

        # Support comma-separated page IDs (e.g. "123456,789012")
        page_ids = [p.strip() for p in page_id.split(",") if p.strip()]

        print(f"[{i}/{len(candidates)}] {name} ({party})"
              + (f"  [{len(page_ids)} page IDs]" if page_ids else ""))

        page_id_ads, name_ads = fetch_ads(name, page_ids, args.country, since_date)

        # Filter name-search results — keep only relevant ads:
        #   1. page_name contains part of the candidate's name, OR
        #   2. ad text mentions candidate's name AND party (both required)
        name_parts  = [p for p in name.lower().split() if len(p) > 3]
        party_terms = PARTY_TERMS.get(party, [party.lower()] if party else [])

        def ad_is_relevant(ad):
            if str(ad.get("page_id") or "") in PAGE_BLOCKLIST:
                return False
            page   = (ad.get("page_name") or "").lower()
            bodies = " ".join(ad.get("ad_creative_bodies") or []).lower()
            titles = " ".join(ad.get("ad_creative_link_titles") or []).lower()
            text   = bodies + " " + titles
            # Page name contains candidate name → keep
            if any(p in page for p in name_parts):
                return True
            # Ad text must contain ALL name parts AND a party term
            # (requiring all parts avoids false matches on common first names)
            all_name_in_text  = all(p in text for p in name_parts) if name_parts else False
            party_in_text = any(t in text for t in party_terms)
            return all_name_in_text and party_in_text

        before   = len(name_ads)
        name_ads = [ad for ad in name_ads if ad_is_relevant(ad)]
        filtered = before - len(name_ads)
        if filtered:
            print(f"    (name search: filtered {filtered} irrelevant ads)")

        # Merge, deduplicate by ad_archive_id (page-ID ads take priority)
        seen: set = set()
        all_ads:  list = []
        for ad in page_id_ads + name_ads:
            aid = ad.get("id")
            if aid and aid not in seen:
                seen.add(aid)
                all_ads.append(ad)

        # Annotate
        for ad in all_ads:
            ad["_query"]    = f"{name}|{party}|{district}"
            ad["_party"]    = party
            ad["_district"] = district
            ad["is_third_party"] = 0  # bylines blocked for EU via API

        saved = upsert_ads(all_ads)
        print(f"    page_id: {len(page_id_ads)} ads  |  name: {len(name_ads)} ads"
              f"  →  {len(all_ads)} unique  ({saved} saved to DB)")

        # Meta allows ~200 req/hour; name search = ~2 calls/candidate → 10s safe
        if i < len(candidates):
            time.sleep(10)

    # ── Save state ────────────────────────────────────────────────────────────
    if args.chunk_size > 0:
        save_chunk_pos("greek", next_pos)
        if chunk_complete:
            save_fetch_date("greek")
            print("[state] Full cycle complete — since_date advanced.")
        else:
            print(f"[state] Partial cycle — since_date unchanged until all {total_candidates} done.")
    else:
        save_fetch_date("greek")

    print_report()


if __name__ == "__main__":
    main()
