"""
check_removed_ads_api.py — API-based removal detection for CY + MT.

Replaces the Playwright-based check_removed_ads_cy.py / check_removed_ads_mt.py.

Meta's Ads Library API returns the removal notice directly inside ad_creative_bodies
for ads that were taken down for violating Advertising Standards — no browser needed.
This eliminates bot-detection failures on GitHub Actions.

Strategy:
  1. Load unchecked / stale-active ads from both DBs.
  2. Group by page_id.
  3. For each page: query the API with ad_creative_bodies.
  4. Detect removal; update DB (never downgrade removed=1→0).

Usage:
    python check_removed_ads_api.py                 # CY + MT, unchecked + stale
    python check_removed_ads_api.py --cy            # CY only
    python check_removed_ads_api.py --mt            # MT only
    python check_removed_ads_api.py --all           # re-check all active ads
    python check_removed_ads_api.py --limit 1000    # cap total ads checked
    python check_removed_ads_api.py --recheck-days 7
    python check_removed_ads_api.py --sleep 2       # seconds between page requests
"""

import os, sys, sqlite3, json, time, argparse, requests
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(override=True)

BASE      = os.path.dirname(os.path.abspath(__file__))
CY_DB     = os.path.join(BASE, "politician_ads.db")
MT_DB     = os.path.join(BASE, "politician_ads_mt.db")
META_URL  = "https://graph.facebook.com/v25.0/ads_archive"

REMOVAL_TEXT = "this content was removed because it didn't follow our advertising standards"


# ── DB ────────────────────────────────────────────────────────────────────────

def load_unchecked(conn, only_unchecked: bool, recheck_days: float,
                   since: str, limit: int) -> list[dict]:
    """
    Return ads that need a removal check.

    only_unchecked=True  → never-checked + stale active (removed=0 checked > recheck_days ago)
    only_unchecked=False → all active ads (--all mode)
    """
    today_str = str(date.today())

    er_filter = "AND election_related IN ('YES', 'UNCERTAIN')"

    if only_unchecked:
        if recheck_days > 0:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=recheck_days)
            ).isoformat()
            time_clause = f"""(
                removed_checked_at IS NULL
                OR (
                    removed = 0
                    AND removed_checked_at < '{cutoff}'
                    AND (ad_stop_date IS NULL OR ad_stop_date = '' OR ad_stop_date >= '{today_str}')
                )
            )"""
        else:
            time_clause = "removed_checked_at IS NULL"

        sql = f"""
            SELECT ad_archive_id, page_id, ad_start_date
            FROM politician_ads
            WHERE {time_clause}
              {er_filter}
            ORDER BY ad_start_date DESC
        """
    else:
        # --all: re-check everything that's still active
        sql = f"""
            SELECT ad_archive_id, page_id, ad_start_date
            FROM politician_ads
            WHERE (ad_stop_date IS NULL OR ad_stop_date = '' OR ad_stop_date >= '{today_str}')
              {er_filter}
            ORDER BY ad_start_date DESC
        """

    rows = conn.execute(sql).fetchall()
    ads = [{'ad_archive_id': r[0], 'page_id': str(r[1] or ''), 'ad_start_date': r[2] or ''}
           for r in rows]

    if since:
        ads = [a for a in ads if a['ad_start_date'] >= since]

    if limit:
        ads = ads[:limit]

    return ads


def save_results(conn, results: list[tuple]) -> None:
    """
    Bulk-save [(ad_archive_id, removed_int, now_str), ...].
    Policy: removed=1 is never downgraded to 0.
    """
    for ad_id, removed, ts in results:
        if removed == 1:
            conn.execute(
                "UPDATE politician_ads SET removed=1, removed_checked_at=? WHERE ad_archive_id=?",
                (ts, ad_id)
            )
        else:
            conn.execute(
                "UPDATE politician_ads SET removed=0, removed_checked_at=? "
                "WHERE ad_archive_id=? AND (removed IS NULL OR removed = 0)",
                (ts, ad_id)
            )
    conn.commit()


# ── API ───────────────────────────────────────────────────────────────────────

RATE_LIMIT_CODE = 613      # Meta API error code for rate limiting
RATE_LIMIT_SLEEP = 60     # seconds to wait on rate limit before retry
MAX_RETRIES      = 3


def fetch_page_ads(page_id: str, since_date: str, country: str, token: str) -> dict:
    """
    Query the Ads Library API for all ads on page_id since since_date.
    Returns {ad_archive_id: is_removed (bool)}.

    Handles rate limiting (#613) with exponential back-off (up to MAX_RETRIES).
    Returns {} on persistent failure so the caller marks those ads as skipped.
    """
    params = {
        "ad_type":              "ALL",
        "ad_reached_countries": json.dumps([country]),
        "search_page_ids":      json.dumps([page_id]),
        "ad_delivery_date_min": since_date or "2025-01-01",
        "fields":               "id,ad_creative_bodies",
        "limit":                100,
        "access_token":         token,
    }
    results = {}
    url = META_URL
    retries = 0
    try:
        while url:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                err_body  = resp.json().get("error", {})
                err_code  = err_body.get("code", 0)
                err_msg   = err_body.get("message", "?")
                if err_code == RATE_LIMIT_CODE and retries < MAX_RETRIES:
                    wait = RATE_LIMIT_SLEEP * (2 ** retries)
                    print(f"      [rate limit] sleeping {wait}s then retrying page {page_id}…")
                    time.sleep(wait)
                    retries += 1
                    # don't advance url/params — retry the same request
                    continue
                print(f"      [!] API {resp.status_code}: {err_msg}")
                break
            retries = 0   # reset on success
            data = resp.json()
            for ad in data.get("data", []):
                bodies = ad.get("ad_creative_bodies") or []
                if bodies:
                    is_removed = any(REMOVAL_TEXT in (b or "").lower() for b in bodies)
                    results[ad["id"]] = is_removed
                # ads without bodies: not conclusive, skip (leave unchecked)
            url    = data.get("paging", {}).get("next")
            params = {}
            if url:
                time.sleep(0.3)
    except requests.RequestException as e:
        print(f"      [!] Request failed: {e}")
    return results


# ── Core ─────────────────────────────────────────────────────────────────────

def process_db(db_path: str, country: str, args, token: str) -> dict:
    """Check one DB (CY or MT). Returns summary counts."""
    label = country
    print(f"\n{'═'*60}")
    print(f"  {label} — {db_path}")
    print(f"{'═'*60}")

    conn = sqlite3.connect(db_path)
    ads  = load_unchecked(conn, only_unchecked=not args.all,
                          recheck_days=args.recheck_days,
                          since=args.since, limit=args.limit)

    if not ads:
        print(f"  Nothing to check.")
        conn.close()
        return {'total': 0, 'removed': 0, 'active': 0, 'skipped': 0}

    print(f"  Ads to check : {len(ads):,}")

    # Group by page_id to batch API calls
    by_page: dict[str, list] = defaultdict(list)
    no_page: list = []
    for a in ads:
        pid = a['page_id']
        if pid:
            by_page[pid].append(a)
        else:
            no_page.append(a)

    print(f"  Pages        : {len(by_page):,}  ({len(no_page)} ads missing page_id — skipped)")
    print(f"  Sleep        : {args.sleep}s between pages")
    print("─" * 60)

    now = datetime.now(timezone.utc).isoformat()
    total_removed = 0
    total_active  = 0
    total_skipped = 0
    batch: list[tuple] = []
    BATCH_SIZE = 100
    page_count = 0

    for page_id, page_ads in by_page.items():
        # Use the oldest ad_start_date in this group as the since_date
        dates     = [a['ad_start_date'] for a in page_ads if a['ad_start_date']]
        since_str = min(dates) if dates else "2025-01-01"

        api_results = fetch_page_ads(page_id, since_str, country, token)
        page_count += 1

        for a in page_ads:
            ad_id = a['ad_archive_id']
            if ad_id not in api_results:
                # API didn't return this ad — could be pagination gap or very old
                total_skipped += 1
                continue
            is_removed = api_results[ad_id]
            if is_removed:
                total_removed += 1
                batch.append((ad_id, 1, now))
                print(f"  ⚠ REMOVED  {ad_id}  (page {page_id})")
            else:
                total_active += 1
                batch.append((ad_id, 0, now))

        if len(batch) >= BATCH_SIZE:
            save_results(conn, batch)
            batch.clear()

        if page_count < len(by_page) and args.sleep > 0:
            time.sleep(args.sleep)

    if batch:
        save_results(conn, batch)

    conn.close()

    total_checked = total_removed + total_active
    print(f"\n  {label} Summary:")
    print(f"    Checked  : {total_checked:,}")
    print(f"    Active   : {total_active:,}")
    print(f"    REMOVED  : {total_removed:,}")
    print(f"    Skipped  : {total_skipped:,}  (ad not returned by API for that page)")

    return {'total': total_checked, 'removed': total_removed,
            'active': total_active, 'skipped': total_skipped}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="API-based removal detection for CY + MT (no browser required)"
    )
    parser.add_argument('--cy',           action='store_true', help='CY only')
    parser.add_argument('--mt',           action='store_true', help='MT only')
    parser.add_argument('--all',          action='store_true',
                        help='Re-check all active ads (default: unchecked + stale)')
    parser.add_argument('--since',        default='',
                        help='Only check ads with start_date >= DATE')
    parser.add_argument('--limit',        type=int, default=0,
                        help='Max ads per DB (0 = no limit)')
    parser.add_argument('--recheck-days', type=float, default=7,
                        help='Re-check active ads not checked in N days (default: 7, 0=disable)')
    parser.add_argument('--sleep',        type=float, default=1.5,
                        help='Sleep between page API requests in seconds (default: 1.5)')
    args = parser.parse_args()

    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        sys.exit("ERROR: META_ACCESS_TOKEN not set in environment or .env")

    run_cy = not args.mt   # run CY unless --mt-only
    run_mt = not args.cy   # run MT unless --cy-only

    totals = {'total': 0, 'removed': 0, 'active': 0, 'skipped': 0}

    if run_cy:
        r = process_db(CY_DB, "CY", args, token)
        for k in totals:
            totals[k] += r[k]

    if run_mt:
        r = process_db(MT_DB, "MT", args, token)
        for k in totals:
            totals[k] += r[k]

    if run_cy and run_mt:
        print(f"\n{'═'*60}")
        print(f"  COMBINED TOTAL")
        print(f"    Checked  : {totals['total']:,}")
        print(f"    Active   : {totals['active']:,}")
        print(f"    REMOVED  : {totals['removed']:,}")
        print(f"    Skipped  : {totals['skipped']:,}")


if __name__ == "__main__":
    main()
