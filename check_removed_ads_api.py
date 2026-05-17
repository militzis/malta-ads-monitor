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
    python check_removed_ads_api.py                      # CY + MT, unchecked + stale
    python check_removed_ads_api.py --cy                 # CY only
    python check_removed_ads_api.py --mt                 # MT only
    python check_removed_ads_api.py --all                # re-check all active ads
    python check_removed_ads_api.py --active-only        # fast mode: only pages with running ads
    python check_removed_ads_api.py --limit 1000         # cap total ads checked
    python check_removed_ads_api.py --recheck-days 7
    python check_removed_ads_api.py --sleep 2            # seconds between page requests

--active-only mode:
    Restricts checking to pages that currently have at least one running ad
    (ad_stop_date IS NULL or >= today).  Only checks non-removed ads on those pages.
    CY: ~26 pages (~78 sec).  MT: ~30 pages (~90 sec).  Safe to run hourly.
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
LOG_FILE  = os.path.join(BASE, "removals_log.xlsx")
AD_URL    = "https://www.facebook.com/ads/library/?id={}"

LOG_HEADERS = [
    "Date Detected (UTC)", "Country", "Candidate", "Party", "District",
    "Page Name", "Ad ID", "Ad Start Date", "Ad End Date",
    "Spend Min (€)", "Spend Max (€)", "Impressions Max", "Ad Library URL",
]

REMOVAL_TEXT = "this content was removed because it didn't follow our advertising standards"


# ── DB ────────────────────────────────────────────────────────────────────────

def load_active_page_ads(conn) -> list[dict]:
    """
    Fast-check mode (--active-only): return all non-removed ads that belong to
    pages currently running at least one ad (stop_date IS NULL or >= today).

    This restricts the API call queue to only the pages that matter right now,
    making the check fast enough to run hourly without hitting rate limits.
    """
    today_str = str(date.today())
    rows = conn.execute("""
        SELECT a.ad_archive_id, a.page_id, a.ad_start_date
        FROM politician_ads a
        WHERE a.page_id IN (
            SELECT DISTINCT page_id
            FROM politician_ads
            WHERE (ad_stop_date IS NULL OR ad_stop_date = '' OR ad_stop_date >= ?)
              AND election_related IN ('YES', 'UNCERTAIN')
              AND page_id IS NOT NULL AND page_id != ''
        )
        AND a.election_related IN ('YES', 'UNCERTAIN')
        AND (a.removed IS NULL OR a.removed = 0)
        ORDER BY a.ad_start_date DESC
    """, (today_str,)).fetchall()
    return [{'ad_archive_id': r[0], 'page_id': str(r[1] or ''), 'ad_start_date': r[2] or ''}
            for r in rows]


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
    Bulk-save [(ad_archive_id, removed_int, now_str, spend_min, spend_max,
                imp_min, imp_max, currency), ...].
    Policy: removed=1 is never downgraded to 0.
    Spend/impressions are updated whenever the API returns them (fills NULLs).
    """
    for row in results:
        ad_id, removed, ts = row[0], row[1], row[2]
        smin, smax = row[3], row[4]
        imin, imax = row[5], row[6]
        cur        = row[7] if len(row) > 7 else None

        if removed == 1:
            conn.execute("""
                UPDATE politician_ads
                SET removed=1, removed_checked_at=?,
                    spend_min      = CASE WHEN ? IS NOT NULL THEN ? ELSE spend_min END,
                    spend_max      = CASE WHEN ? IS NOT NULL THEN ? ELSE spend_max END,
                    impressions_min= CASE WHEN ? IS NOT NULL THEN ? ELSE impressions_min END,
                    impressions_max= CASE WHEN ? IS NOT NULL THEN ? ELSE impressions_max END,
                    currency       = CASE WHEN ? IS NOT NULL THEN ? ELSE currency END
                WHERE ad_archive_id=?
            """, (ts,
                  smin, smin, smax, smax,
                  imin, imin, imax, imax,
                  cur,  cur,
                  ad_id))
        else:
            conn.execute("""
                UPDATE politician_ads
                SET removed=0, removed_checked_at=?,
                    spend_min      = CASE WHEN ? IS NOT NULL THEN ? ELSE spend_min END,
                    spend_max      = CASE WHEN ? IS NOT NULL THEN ? ELSE spend_max END,
                    impressions_min= CASE WHEN ? IS NOT NULL THEN ? ELSE impressions_min END,
                    impressions_max= CASE WHEN ? IS NOT NULL THEN ? ELSE impressions_max END,
                    currency       = CASE WHEN ? IS NOT NULL THEN ? ELSE currency END
                WHERE ad_archive_id=? AND (removed IS NULL OR removed = 0)
            """, (ts,
                  smin, smin, smax, smax,
                  imin, imin, imax, imax,
                  cur,  cur,
                  ad_id))
    conn.commit()


# ── Excel log ────────────────────────────────────────────────────────────────

def _load_log_ids() -> set:
    """Return ad_archive_ids already in removals_log.xlsx (avoid duplicate rows)."""
    if not os.path.exists(LOG_FILE):
        return set()
    try:
        from openpyxl import load_workbook
        wb = load_workbook(LOG_FILE, read_only=True)
        ws = wb.active
        ids = set()
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue   # skip header
            ad_id = row[6]  # column G = Ad ID
            if ad_id:
                ids.add(str(ad_id))
        wb.close()
        return ids
    except Exception as e:
        print(f"  [log] Could not read existing log: {e}")
        return set()


def _append_to_log(rows: list[dict]) -> None:
    """Append new-removal rows to removals_log.xlsx, creating it if needed."""
    if not rows:
        return
    try:
        from openpyxl import load_workbook, Workbook
        from openpyxl.styles import PatternFill, Font, Alignment

        RED_FILL  = PatternFill("solid", fgColor="C00000")
        WHITE_HDR = Font(bold=True, color="FFFFFF")

        if os.path.exists(LOG_FILE):
            wb = load_workbook(LOG_FILE)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "Removals Log"
            ws.append(LOG_HEADERS)
            for cell in ws[1]:
                cell.fill      = RED_FILL
                cell.font      = WHITE_HDR
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
            ws.row_dimensions[1].height = 22
            ws.freeze_panes = "A2"
            # Column widths
            widths = [20, 8, 28, 16, 14, 32, 20, 12, 12, 12, 12, 14, 55]
            from openpyxl.utils import get_column_letter
            for i, w in enumerate(widths, 1):
                ws.column_dimensions[get_column_letter(i)].width = w

        for r in rows:
            ws.append([
                r["detected_at"],
                r["country"],
                r["candidate"],
                r["party"],
                r["district"],
                r["page_name"],
                r["ad_id"],
                r["start_date"],
                r["stop_date"],
                r["spend_min"],
                r["spend_max"],
                r["impressions_max"],
                AD_URL.format(r["ad_id"]),
            ])
            # Style the URL cell
            url_cell = ws.cell(ws.max_row, 13)
            url_cell.font = Font(color="0563C1", underline="single")

        wb.save(LOG_FILE)
        print(f"  [log] Appended {len(rows)} new removal(s) → removals_log.xlsx")

    except ImportError:
        print("  [log] openpyxl not installed — skipping Excel log")
    except Exception as e:
        print(f"  [log] Error writing log: {e}")


def log_new_removals(conn, newly_removed_ids: list[str], country: str,
                     detected_at: str, existing_log_ids: set) -> None:
    """Look up details for newly removed ads and append to the log."""
    to_log = [aid for aid in newly_removed_ids if aid not in existing_log_ids]
    if not to_log:
        return

    placeholders = ",".join("?" * len(to_log))
    rows_db = conn.execute(f"""
        SELECT ad_archive_id, politician_query, party, page_name,
               ad_start_date, ad_stop_date,
               spend_min, spend_max, impressions_max
        FROM politician_ads
        WHERE ad_archive_id IN ({placeholders})
    """, to_log).fetchall()

    log_rows = []
    for r in rows_db:
        query  = r[1] or ""
        parts  = query.split("|")
        cand   = parts[0].strip()
        party  = r[2] or (parts[1].strip() if len(parts) > 1 else "")
        dist   = parts[2].strip() if len(parts) > 2 else ""
        log_rows.append({
            "detected_at":   detected_at[:19].replace("T", " "),
            "country":       country,
            "candidate":     cand,
            "party":         party,
            "district":      dist,
            "page_name":     r[3] or "",
            "ad_id":         r[0],
            "start_date":    r[4] or "",
            "stop_date":     r[5] or "",
            "spend_min":     r[6],
            "spend_max":     r[7],
            "impressions_max": r[8],
        })

    _append_to_log(log_rows)
    # Update the in-memory set so subsequent DBs don't re-log the same IDs
    existing_log_ids.update(r["ad_id"] for r in log_rows)


# ── API ───────────────────────────────────────────────────────────────────────

RATE_LIMIT_CODE      = 613   # Meta API error code for rate limiting
RATE_LIMIT_SLEEP     = 60   # seconds to wait on rate limit before retry
MAX_RETRIES          = 2    # cap at 2 retries → max 180s per page (was 420s at 3)
CONSEC_ERROR_ABORT   = 5    # abort this DB after N consecutive non-rate-limit failures
SERVER_ERROR_SLEEP   = 10   # seconds to wait before retrying a 500


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
        "fields":               "id,ad_creative_bodies,spend,impressions,currency",
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
                try:
                    err_body = resp.json().get("error", {})
                except Exception:
                    err_body = {}
                err_code = err_body.get("code", 0)
                err_msg  = err_body.get("message", f"HTTP {resp.status_code}")
                if err_code == RATE_LIMIT_CODE and retries < MAX_RETRIES:
                    wait = RATE_LIMIT_SLEEP * (2 ** retries)
                    print(f"      [rate limit] sleeping {wait}s then retrying page {page_id}…")
                    time.sleep(wait)
                    retries += 1
                    continue
                # Non-rate-limit error (e.g. 500): retry once with short sleep
                if resp.status_code >= 500 and retries < 1:
                    print(f"      [!] API {resp.status_code} — sleeping {SERVER_ERROR_SLEEP}s then retrying page {page_id}…")
                    time.sleep(SERVER_ERROR_SLEEP)
                    retries += 1
                    continue
                print(f"      [!] API {resp.status_code}: {err_msg}")
                return results, True   # signal: API error occurred
            retries = 0   # reset on success
            data = resp.json()
            for ad in data.get("data", []):
                bodies = ad.get("ad_creative_bodies") or []
                if bodies:
                    is_removed = any(REMOVAL_TEXT in (b or "").lower() for b in bodies)
                    spend  = ad.get("spend") or {}
                    imp    = ad.get("impressions") or {}
                    results[ad["id"]] = {
                        "is_removed":     is_removed,
                        "spend_min":      spend.get("lower_bound"),
                        "spend_max":      spend.get("upper_bound"),
                        "impressions_min": imp.get("lower_bound"),
                        "impressions_max": imp.get("upper_bound"),
                        "currency":       ad.get("currency"),
                    }
                # ads without bodies: not conclusive, skip (leave unchecked)
            url    = data.get("paging", {}).get("next")
            params = {}
            if url:
                time.sleep(0.3)
    except requests.RequestException as e:
        print(f"      [!] Request failed: {e}")
        return results, True   # signal: API error occurred
    return results, False      # False = no error


# ── Core ─────────────────────────────────────────────────────────────────────

def process_db(db_path: str, country: str, args, token: str,
               existing_log_ids: set) -> dict:
    """Check one DB (CY or MT). Returns summary counts."""
    label = country
    print(f"\n{'═'*60}")
    print(f"  {label} — {db_path}")
    print(f"{'═'*60}")

    conn = sqlite3.connect(db_path)

    if args.active_only:
        ads = load_active_page_ads(conn)
        mode_label = "active-only"
    else:
        ads = load_unchecked(conn, only_unchecked=not args.all,
                             recheck_days=args.recheck_days,
                             since=args.since, limit=args.limit)
        mode_label = "full"

    if not ads:
        print(f"  Nothing to check.")
        conn.close()
        return {'total': 0, 'removed': 0, 'active': 0, 'skipped': 0}

    print(f"  Mode         : {mode_label}")
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
    newly_removed: list[str] = []   # ad_ids newly detected as removed this run
    BATCH_SIZE = 100
    page_count = 0
    consec_errors = 0              # consecutive API error counter

    # Pre-load which ads in our queue are ALREADY removed in the DB
    # so we don't log them as "new" when they were previously known
    all_ids = [a['ad_archive_id'] for a in ads]
    if all_ids:
        ph = ",".join("?" * len(all_ids))
        already_removed_in_db = set(
            r[0] for r in conn.execute(
                f"SELECT ad_archive_id FROM politician_ads "
                f"WHERE ad_archive_id IN ({ph}) AND removed=1", all_ids
            ).fetchall()
        )
    else:
        already_removed_in_db = set()

    for page_id, page_ads in by_page.items():
        # Use the oldest ad_start_date in this group as the since_date
        dates     = [a['ad_start_date'] for a in page_ads if a['ad_start_date']]
        since_str = min(dates) if dates else "2025-01-01"

        api_results, had_error = fetch_page_ads(page_id, since_str, country, token)
        page_count += 1

        if had_error:
            consec_errors += 1
            if consec_errors >= CONSEC_ERROR_ABORT:
                print(f"\n  [!] {consec_errors} consecutive API errors — Meta API appears to be down.")
                print(f"  [!] Aborting {label} check early to avoid wasting timeout budget.")
                break
            total_skipped += len(page_ads)
            continue
        else:
            consec_errors = 0   # reset on any successful page

        for a in page_ads:
            ad_id = a['ad_archive_id']
            if ad_id not in api_results:
                total_skipped += 1
                continue
            info       = api_results[ad_id]
            is_removed = info["is_removed"]
            spend_row  = (ad_id, 1 if is_removed else 0, now,
                          info["spend_min"], info["spend_max"],
                          info["impressions_min"], info["impressions_max"],
                          info["currency"])
            if is_removed:
                total_removed += 1
                batch.append(spend_row)
                if ad_id not in already_removed_in_db:
                    newly_removed.append(ad_id)
                    print(f"  ⚠ NEW REMOVAL  {ad_id}  (page {page_id})")
                else:
                    print(f"  ⚠ removed      {ad_id}  (already known)")
            else:
                total_active += 1
                batch.append(spend_row)

        if len(batch) >= BATCH_SIZE:
            save_results(conn, batch)
            batch.clear()

        if page_count < len(by_page) and args.sleep > 0:
            time.sleep(args.sleep)

    if batch:
        save_results(conn, batch)

    # Log newly detected removals to Excel
    if newly_removed:
        log_new_removals(conn, newly_removed, country, now, existing_log_ids)

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
    parser.add_argument('--active-only',  action='store_true',
                        help='Fast mode: only pages with currently-running ads (~78s CY, ~90s MT). '
                             'Safe to run hourly.')
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

    # Load existing log IDs once — shared across CY and MT to avoid duplicates
    existing_log_ids = _load_log_ids()

    totals = {'total': 0, 'removed': 0, 'active': 0, 'skipped': 0}

    if run_cy:
        r = process_db(CY_DB, "CY", args, token, existing_log_ids)
        for k in totals:
            totals[k] += r[k]

    if run_mt:
        r = process_db(MT_DB, "MT", args, token, existing_log_ids)
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
