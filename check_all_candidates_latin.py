"""
Check Meta Ad Library for ALL candidates using LATIN (English) transliterated names.
Searches each candidate's name converted to Latin characters (e.g. Νικολάου → Nikolaou).
Uses a separate database: politician_ads_latin.db

Usage:
    python check_all_candidates_latin.py
    python check_all_candidates_latin.py --country CY --parties ΔΗΣΥ ΑΚΕΛ
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(override=True)

DB_PATH             = "politician_ads.db"       # shared combined DB
META_AD_LIBRARY_URL = "https://graph.facebook.com/v25.0/ads_archive"
CANDIDATES_FILE     = "candidates.csv"
FETCH_STATE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_state.json")
BLOCKLIST_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_blocklist.json")
DEFAULT_START       = "2025-09-01"   # hard floor — never fetch before this date
OVERLAP_DAYS        = 7              # re-fetch last N days to catch updated impressions/spend

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
    On subsequent runs returns (last_run - OVERLAP_DAYS).
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

# ─── Transliteration ──────────────────────────────────────────────────────────

# Digraphs must be checked before single characters
TRANSLIT_DIGRAPHS = {
    # Unaccented
    'ου':'ou', 'αυ':'av', 'ευ':'ev', 'οι':'oi',
    'αι':'ai', 'ει':'ei', 'υι':'yi', 'γγ':'ng',
    'γκ':'gk', 'μπ':'mp', 'ντ':'nt', 'τζ':'tz',
    # Accented variants (τόνος σε δεύτερο γράμμα)
    'ού':'ou', 'αύ':'av', 'εύ':'ev', 'οί':'oi',
    'αί':'ai', 'εί':'ei',
    # Accented variants (τόνος σε πρώτο γράμμα)
    'όυ':'ou', 'άυ':'av', 'έυ':'ev',
}
TRANSLIT = {
    'α':'a',  'β':'v',  'γ':'g',  'δ':'d',  'ε':'e',  'ζ':'z',
    'η':'i',  'θ':'th', 'ι':'i',  'κ':'k',  'λ':'l',  'μ':'m',
    'ν':'n',  'ξ':'x',  'ο':'o',  'π':'p',  'ρ':'r',  'σ':'s',
    'ς':'s',  'τ':'t',  'υ':'y',  'φ':'f',  'χ':'ch', 'ψ':'ps',
    'ω':'o',  'ά':'a',  'έ':'e',  'ή':'i',  'ί':'i',  'ό':'o',
    'ύ':'y',  'ώ':'o',  'ϊ':'i',  'ϋ':'y',  'ΐ':'i',  'ΰ':'y',
}

def translit(text: str) -> str:
    """Convert Greek text to Latin characters (handles digraphs)."""
    text = text.lower()
    result = ""
    i = 0
    while i < len(text):
        # Try digraph first
        two = text[i:i+2]
        if two in TRANSLIT_DIGRAPHS:
            result += TRANSLIT_DIGRAPHS[two]
            i += 2
        else:
            result += TRANSLIT.get(text[i], text[i])
            i += 1
    return result

def name_to_latin(name: str) -> str:
    """Convert a full Greek candidate name to Latin (e.g. 'Νικολάου Ανδρέας' → 'Nikolaou Andreas')."""
    parts = name.strip().split()
    latin_parts = []
    for p in parts:
        latin = translit(p)
        # Capitalize first letter
        latin_parts.append(latin.capitalize())
    return " ".join(latin_parts)


# ─── Party terms (Latin versions for relevance filter) ───────────────────────

PARTY_TERMS_LATIN = {
    "ΔΗΣΥ":            ["disy", "dimokratikos synagermos"],
    "ΑΚΕΛ":            ["akel"],
    "ΔΗΚΟ":            ["diko"],
    "ΕΔΕΚ":            ["edek"],
    "ΕΛΑΜ":            ["elam"],
    "ΑΜΔΗ":            ["amdi", "amea dimokratia"],
    "ΒΟΛΤ":            ["volt"],
    "ΔΕΚ":             ["dek"],
    "ΟΙΚΟΛΟΓΟΙ":       ["oikologoi", "greens"],
    "ΑΛΜΑ":            ["alma"],
    "ΔΗΠΑ":            ["dipa"],
    "ΣΗΚΟΥ ΠΑΝΩ":      ["sikou pano"],
    # New parties added 2026-05-07
    "ΑΓΡΟΝΟΜΟΣ":       ["agronomos"],
    "ΑΚΡΟ":            ["akro"],
    "ΔΑ":              ["dimokratiki allagi"],
    "ΕΝΕΡΓΟΙ ΠΟΛΙΤΕΣ": ["energoi polites", "kynigon"],
    "ΛΑΕ":             ["laikos agonas"],
    "ΛΑΚΕΔΑΙΜΟΝΙΟΙ":   ["lakedaimonioi"],
    "ΠΡΑΣΙΝΟΙ":        ["prasino komma", "prasini"],
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
    conn.commit()
    conn.close()


def upsert_ads(ads: list[dict]) -> int:
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for ad in ads:
        imp   = ad.get("impressions", {})
        spend = ad.get("spend", {})
        try:
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
                    checked_at          = excluded.checked_at
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
                'latin',
                0,    # removed (only for new rows)
                None, # removed_checked_at (only for new rows)
                ad_text,
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"    [db] skipped {ad.get('id')}: {e}")
    conn.commit()
    conn.close()
    return inserted


# ─── API ─────────────────────────────────────────────────────────────────────

def fetch_ads(latin_name: str, country: str, since_date: str = DEFAULT_START) -> list[dict]:
    token = os.environ.get("META_ACCESS_TOKEN")
    params = {
        "ad_type": "ALL",
        "ad_reached_countries": json.dumps([country]),
        "ad_delivery_date_min": since_date,
        "search_terms": latin_name,
        "fields": (
            "id,page_name,page_id,bylines,"
            "ad_delivery_start_time,ad_delivery_stop_time,"
            "impressions,spend,currency,ad_snapshot_url,"
            "ad_creative_bodies,ad_creative_link_titles"
        ),
        "limit": 100,
        "access_token": token,
    }

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
            url = next_url
            params = {}
            if next_url:
                time.sleep(0.5)
    except requests.RequestException as e:
        print(f"    [!] Request failed: {e}")
    return all_ads


# ─── Report ───────────────────────────────────────────────────────────────────

def print_report():
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM politician_ads").fetchone()[0]

    print("\n" + "="*60)
    print("REPORT -- LATIN NAME SEARCH")
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
        print(f"\n  {name} ({party or '?'} — {district or '?'})")
        print(f"    Ads: {total}")
        if imp:
            print(f"    Max impressions: {imp:,}")

    conn.close()
    print("\n" + "="*60)
    print(f"Full data saved to: {DB_PATH}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", default="CY")
    parser.add_argument("--parties", nargs="+", help="Only process these parties")
    parser.add_argument("--start", type=int, default=1, help="Start from candidate number N (default: 1, manual override)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and fetch everything from DEFAULT_START")
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
    chunk_complete = False
    if args.chunk_size > 0:
        chunk_pos = load_chunk_pos("latin")
        chunk_end = chunk_pos + args.chunk_size
        chunk = candidates[chunk_pos:chunk_end]
        next_pos = chunk_end if chunk_end < len(candidates) else 0
        print(f"\n[chunk] Position {chunk_pos}–{chunk_pos + len(chunk) - 1} of {len(candidates)} "
              f"(next run starts at {next_pos if next_pos else 0})")
        candidates = chunk
        if next_pos == 0:
            chunk_complete = True
    elif args.start > 1:
        candidates = candidates[args.start - 1:]
        print(f"\nStarting from candidate #{args.start}")

    # ── Incremental: only fetch ads newer than the last run ───────────────────
    since_date = DEFAULT_START if args.full else load_fetch_since("latin")
    print(f"\nChecking {len(candidates)} candidates with LATIN names (country: {args.country})")
    print(f"Fetching ads from: {since_date}{'  [--full mode]' if args.full else '  (incremental)'}\n")

    for i, c in enumerate(candidates, 1):
        name     = c.get("name", "").strip()
        party    = c.get("party", "").strip()
        district = c.get("district", "").strip()

        if not name:
            continue

        latin_name = name_to_latin(name)
        print(f"[{i}/{len(candidates)}] {name} → '{latin_name}' ({party})")

        ads = fetch_ads(latin_name, args.country, since_date)

        # Relevance filter:
        # Keep ad if page name contains Latin name parts OR
        # ad text contains Latin name AND party term
        latin_parts  = [p for p in latin_name.lower().split() if len(p) > 3]
        party_terms  = PARTY_TERMS_LATIN.get(party, [party.lower()] if party else [])

        def ad_is_relevant(ad):
            if str(ad.get("page_id") or "") in PAGE_BLOCKLIST:
                return False
            page   = (ad.get("page_name") or "").lower()
            bodies = " ".join(ad.get("ad_creative_bodies") or []).lower()
            titles = " ".join(ad.get("ad_creative_link_titles") or []).lower()
            text   = bodies + " " + titles

            # 1. Page name contains candidate's Latin name
            if any(p in page for p in latin_parts):
                return True
            # 2. Ad text contains name AND party term
            name_in_text  = any(p in text for p in latin_parts)
            party_in_text = any(t in text for t in party_terms)
            if name_in_text and party_in_text:
                return True
            return False

        before = len(ads)
        ads = [ad for ad in ads if ad_is_relevant(ad)]
        filtered = before - len(ads)
        if filtered:
            print(f"    (filtered {filtered} irrelevant ads)")

        for ad in ads:
            ad["_query"]    = f"{name}|{party}|{district}"
            ad["_party"]    = party
            ad["_district"] = district
            ad["is_third_party"] = 0

        saved = upsert_ads(ads)
        print(f"    OK {len(ads)} ads -- {saved} saved to DB")

        # Meta allows ~200 req/hour; name search = ~2 calls/candidate → 10s safe
        if i < len(candidates):
            time.sleep(10)

    # ── Save state ────────────────────────────────────────────────────────────
    if args.chunk_size > 0:
        save_chunk_pos("latin", next_pos)
        if chunk_complete:
            save_fetch_date("latin")
            print("[state] Full cycle complete — since_date advanced.")
        else:
            print(f"[state] Partial cycle — since_date unchanged until all candidates done.")
    else:
        save_fetch_date("latin")

    print_report()


if __name__ == "__main__":
    main()
