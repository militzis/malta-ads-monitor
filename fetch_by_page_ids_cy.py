"""
fetch_by_page_ids_cy.py — Fast Cyprus spend/impression refresh via page IDs.

Extracts every unique page_id already in politician_ads.db and re-fetches
all ads for each page since DEFAULT_START.  Uses the fixed ON CONFLICT upsert
so `removed` / `removed_checked_at` are never overwritten.

Much faster than check_all_candidates.py (no name search, no relevance
filtering, 3s sleep instead of 20s).

798 page IDs × ~3s ≈ 40 minutes.

Usage:
    python fetch_by_page_ids_cy.py                      # default: last 14 days (fast daily run)
    python fetch_by_page_ids_cy.py --full               # full history from 2025-10-01
    python fetch_by_page_ids_cy.py --since 2026-01-01   # custom start date
    python fetch_by_page_ids_cy.py --sleep 2            # sleep between pages
"""

import os, sys, sqlite3, json, time, argparse, requests
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(override=True)

BASE            = os.path.dirname(os.path.abspath(__file__))
DB_PATH         = os.path.join(BASE, "politician_ads.db")
BLOCKLIST_FILE  = os.path.join(BASE, "page_blocklist.json")
META_URL        = "https://graph.facebook.com/v25.0/ads_archive"
FULL_START      = "2025-10-01"                                    # used with --full
DEFAULT_START   = str(date.today() - timedelta(days=14))          # daily default: rolling 14-day window


def load_blocklist() -> set:
    if os.path.exists(BLOCKLIST_FILE):
        with open(BLOCKLIST_FILE, encoding='utf-8') as f:
            data = json.load(f)
            pages = data.get('pages', data) if isinstance(data, dict) else {}
            return set(str(k) for k in pages.keys())
    return set()


# ── DB helpers ────────────────────────────────────────────────────────────────

def migrate_db(conn) -> None:
    """Add columns used by this script if they don't exist yet."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(politician_ads)").fetchall()}
    if "first_seen_at" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN first_seen_at TEXT")
        print("[db] Added column: first_seen_at")
    if "election_related" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN election_related TEXT")
        print("[db] Added column: election_related")
    conn.commit()


def load_page_ids(conn, blocklist: set) -> list[dict]:
    """Return unique page_ids that have YES or UNCERTAIN ads — skipping blocked pages."""
    rows = conn.execute("""
        SELECT
            page_id,
            MAX(page_name)        AS page_name,
            MAX(politician_query) AS politician_query,
            MAX(party)            AS party,
            MAX(district)         AS district,
            MAX(source)           AS source,
            COUNT(*)              AS ads
        FROM politician_ads
        WHERE page_id IS NOT NULL AND page_id != ''
          AND election_related IN ('YES', 'UNCERTAIN')
        GROUP BY page_id
        ORDER BY ads DESC
    """).fetchall()
    pages = [dict(zip(
        ['page_id','page_name','politician_query','party','district','source','ads'], r
    )) for r in rows]
    # Skip pages already confirmed irrelevant
    return [p for p in pages if p['page_id'] not in blocklist]


REMOVAL_TEXT = "this content was removed because it didn't follow our advertising standards"


def _detect_removal(ad: dict) -> tuple[int, str | None]:
    """
    Inspect ad_creative_bodies for the removal notice Meta injects when an ad
    violates Advertising Standards.  Returns (removed_int, checked_at_or_None).

    - bodies present, removal text found  → (1, now)
    - bodies present, no removal text     → (0, now)   ← ad is confirmed active
    - bodies absent (API returned nothing)→ (0, None)  ← unknown; don't stamp
    """
    bodies = ad.get("ad_creative_bodies") or []
    now = datetime.now(timezone.utc).isoformat()
    if bodies:
        is_removed = any(REMOVAL_TEXT in (b or "").lower() for b in bodies)
        return (1, now) if is_removed else (0, now)
    return (0, None)


def upsert_ads(conn, ads: list[dict], source: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    for ad in ads:
        imp   = ad.get("impressions", {})
        spend = ad.get("spend", {})
        raw_bodies = ad.get("ad_creative_bodies") or []
        titles     = ad.get("ad_creative_link_titles") or []
        bodies_str = " ".join(raw_bodies)
        titles_str = " ".join(titles)
        ad_text = (bodies_str + " " + titles_str).strip()[:1000] or None

        removed_val, removed_checked = _detect_removal(ad)

        try:
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
                    impressions_min      = excluded.impressions_min,
                    impressions_max      = excluded.impressions_max,
                    spend_min            = excluded.spend_min,
                    spend_max            = excluded.spend_max,
                    ad_stop_date         = excluded.ad_stop_date,
                    page_name            = excluded.page_name,
                    checked_at           = excluded.checked_at,
                    -- Removal: upgrade to 1 if newly detected; never downgrade 1→0
                    removed              = CASE
                                            WHEN excluded.removed = 1 THEN 1
                                            ELSE politician_ads.removed
                                          END,
                    removed_checked_at   = CASE
                                            WHEN excluded.removed_checked_at IS NOT NULL
                                            THEN excluded.removed_checked_at
                                            ELSE politician_ads.removed_checked_at
                                          END
                    -- first_seen_at intentionally NOT updated: set once on first insert
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
                source,
                removed_val, removed_checked,
                ad_text,
                now,   # first_seen_at — only written on first INSERT
            ))
            saved += 1
        except sqlite3.Error as e:
            print(f"    [db] skipped {ad.get('id')}: {e}")
    conn.commit()
    return saved


# ── API ───────────────────────────────────────────────────────────────────────

def fetch_page(page_id: str, since_date: str, token: str) -> list[dict]:
    params = {
        "ad_type":              "ALL",
        "ad_reached_countries": json.dumps(["CY"]),
        "search_page_ids":      json.dumps([page_id]),
        "ad_delivery_date_min": since_date,
        "fields": (
            "id,page_name,page_id,bylines,"
            "ad_delivery_start_time,ad_delivery_stop_time,"
            "impressions,spend,currency,ad_snapshot_url,"
            "ad_creative_bodies,ad_creative_link_titles"
        ),
        "limit":        100,
        "access_token": token,
    }
    ads = []
    url = META_URL
    try:
        while url:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                try:
                    err = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    err = resp.text[:200]
                print(f"    [!] API {resp.status_code}: {err}")
                break
            data = resp.json()
            ads.extend(data.get("data", []))
            url    = data.get("paging", {}).get("next")
            params = {}
            if url:
                time.sleep(0.3)
    except requests.RequestException as e:
        print(f"    [!] Request failed: {e}")
    return ads


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None,
                        help=f"Fetch ads from this date (default: rolling 14-day window)")
    parser.add_argument("--full",  action="store_true",
                        help=f"Full history fetch from {FULL_START} (slow, use occasionally)")
    parser.add_argument("--sleep", type=float, default=3.0,
                        help="Sleep between page requests in seconds (default: 3)")
    args = parser.parse_args()

    if args.full:
        since = FULL_START
    elif args.since:
        since = args.since
    else:
        since = DEFAULT_START  # rolling 14-day window

    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        sys.exit("ERROR: META_ACCESS_TOKEN not set in .env")

    conn = sqlite3.connect(DB_PATH)
    migrate_db(conn)
    blocklist = load_blocklist()
    pages = load_page_ids(conn, blocklist)
    print(f"\nCyprus page-ID re-fetch — {since} onwards{'  [FULL]' if args.full else '  [14-day window]'}")
    print(f"Blocked pages  : {len(blocklist):,} (skipped)")
    print(f"Pages to fetch : {len(pages):,}")
    print(f"Sleep interval : {args.sleep}s")
    print(f"Estimated time : ~{len(pages) * args.sleep / 60:.0f} min")
    print("─" * 60)

    total_new = 0

    for i, page in enumerate(pages, 1):
        pid    = page["page_id"]
        pname  = (page["page_name"] or "")[:40]
        query  = page["politician_query"] or ""
        cand   = query.split("|")[0][:30]
        source = page["source"] or "greek"

        ads = fetch_page(pid, since, token)

        for ad in ads:
            ad["_query"]    = query
            ad["_party"]    = page["party"]
            ad["_district"] = page["district"]

        saved = upsert_ads(conn, ads, source)
        total_new += saved

        print(f"[{i:>4}/{len(pages)}] {len(ads):>4} ads  {pname:<40} {cand}")

        if i < len(pages):
            time.sleep(args.sleep)

    conn.close()

    print("\n── Summary " + "─" * 45)
    print(f"  Pages fetched : {len(pages):,}")
    print(f"  Ads saved     : {total_new:,}  (new + spend-updated)")
    print(f"\nDone. Run check_removed_ads_cy.py next to check for removals.")


if __name__ == "__main__":
    main()
