"""
Check which Cyprus ads have been removed by Meta (violating Advertising Standards).

Uses Playwright to fully render each Meta Ad Library page and detect the
"This content was removed because it didn't follow our Advertising Standards"
banner — which is JS-rendered and invisible to plain HTTP requests.

Results are stored in the `removed` column of politician_ads.db.

Usage:
    python check_removed_ads_cy.py                      # unchecked ads, 2026+
    python check_removed_ads_cy.py --all                # re-check all ads
    python check_removed_ads_cy.py --since 2026-01-01   # ads on/after date
    python check_removed_ads_cy.py --concurrency 2      # parallel browsers (default 2)
    python check_removed_ads_cy.py --limit 50           # stop after N ads

Requires:
    pip install playwright
    playwright install chromium
"""

import sys, os, sqlite3, json, asyncio, argparse
from datetime import datetime, timezone, timedelta, date

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.exit("ERROR: playwright not installed.\nRun: pip install playwright && playwright install chromium")

BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "politician_ads.db")
BL_FILE = os.path.join(BASE, "page_blocklist.json")

AD_LIB_URL = "https://www.facebook.com/ads/library/?id={ad_id}"


def is_removed_text(body: str) -> bool:
    b = body.lower()
    strong_markers = [
        "didn't follow our advertising standards",
        "did not follow our advertising standards",
        "this content was removed",
        "content was removed because",
        "removed because it didn",
    ]
    return any(m in b for m in strong_markers)


# ── DB helpers ─────────────────────────────────────────────────────────────────

def load_blocklist() -> set:
    if os.path.exists(BL_FILE):
        with open(BL_FILE, encoding='utf-8') as f:
            data = json.load(f)
            # Support both {"pages": {...}} and flat list/dict formats
            if isinstance(data, dict) and 'pages' in data:
                return set(str(k) for k in data['pages'].keys())
            elif isinstance(data, dict):
                return set(str(k) for k in data.keys())
            elif isinstance(data, list):
                return set(str(x) for x in data)
    return set()


def migrate_db(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(politician_ads)").fetchall()}
    if "removed" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN removed INTEGER DEFAULT 0")
        print("  Added 'removed' column")
    if "removed_checked_at" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN removed_checked_at TEXT")
        print("  Added 'removed_checked_at' column")
    conn.commit()


def load_ads(conn, blocklist: set, only_unchecked: bool, since: str, limit: int,
             active_only: bool = True, recheck_days: float = 7) -> list[dict]:
    """
    Load ads to check for removal.

    active_only  — skip ads whose stop_date has already passed (default: True).
                   Stopped ads are irrelevant for daily monitoring.
    recheck_days — also re-check ads confirmed active (removed=0) if they
                   haven't been checked in this many days (default: 7).
                   Set to 0 to disable re-checking.
    """
    today_str = str(date.today())

    # ── filters ──────────────────────────────────────────────────────────────
    # Skip confirmed non-election ads — unclassified (NULL) still included.
    er_filter = "AND (election_related IS NULL OR election_related != 'NO')"

    # Only ads that are still running (no past stop_date)
    active_filter = (
        f"AND (ad_stop_date IS NULL OR ad_stop_date = '' OR ad_stop_date >= '{today_str}')"
        if active_only else ""
    )

    if only_unchecked:
        if recheck_days > 0:
            # Include: (a) never checked  OR  (b) active but stale (checked > N days ago)
            recheck_cutoff = (
                datetime.now(timezone.utc) - timedelta(days=recheck_days)
            ).isoformat()
            unchecked_clause = f"""(
                removed_checked_at IS NULL
                OR (removed = 0 AND removed_checked_at < '{recheck_cutoff}')
            )"""
        else:
            # Only truly unchecked
            unchecked_clause = "removed_checked_at IS NULL"

        sql = f"""
            SELECT ad_archive_id, page_id, page_name, politician_query, ad_start_date
            FROM politician_ads
            WHERE {unchecked_clause}
              {active_filter}
              {er_filter}
            ORDER BY ad_start_date DESC
        """
    else:
        # --all: re-check everything (respects active_only and er_filter)
        sql = f"""
            SELECT ad_archive_id, page_id, page_name, politician_query, ad_start_date
            FROM politician_ads
            WHERE 1=1
              {active_filter}
              {er_filter}
            ORDER BY ad_start_date DESC
        """

    rows = [dict(zip(['ad_archive_id','page_id','page_name','politician_query','ad_start_date'], r))
            for r in conn.execute(sql).fetchall()]

    # Apply blocklist
    rows = [r for r in rows if str(r['page_id'] or '') not in blocklist]

    # Apply date filter
    if since:
        rows = [r for r in rows if (r['ad_start_date'] or '') >= since]

    if limit:
        rows = rows[:limit]

    return rows


def save_results(results: list[tuple]):
    """Bulk save results: [(ad_archive_id, removed_int, now_str), ...]

    Policy: once removed=1 is confirmed it is NEVER downgraded back to 0,
    because Meta's Ad Library rendering is inconsistent between requests.
    """
    conn = sqlite3.connect(DB_PATH)
    for ad_id, removed, ts in results:
        if removed == 1:
            # Always mark confirmed removals
            conn.execute(
                "UPDATE politician_ads SET removed=1, removed_checked_at=? WHERE ad_archive_id=?",
                (ts, ad_id)
            )
        else:
            # Only set active if not already confirmed removed
            conn.execute(
                "UPDATE politician_ads SET removed=0, removed_checked_at=? "
                "WHERE ad_archive_id=? AND (removed IS NULL OR removed = 0)",
                (ts, ad_id)
            )
    conn.commit()
    conn.close()


# ── Playwright helpers ─────────────────────────────────────────────────────────

async def check_ad(context, ad_id: str, semaphore: asyncio.Semaphore) -> str:
    """
    Returns: 'removed' | 'active' | 'error'
    """
    url = AD_LIB_URL.format(ad_id=ad_id)
    async with semaphore:
        page = await context.new_page()
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)

            try:
                await page.wait_for_selector(
                    '[data-testid="ad_archive_ad_item"], '
                    '.x1y1aw1k, '
                    'div[role="article"]',
                    timeout=6000
                )
            except Exception:
                pass

            await page.wait_for_timeout(2500)

            body = await page.inner_text('body')
            if is_removed_text(body):
                return 'removed'
            return 'active'

        except Exception as e:
            err = str(e)[:60]
            if 'timeout' in err.lower():
                return 'error:timeout'
            return f'error:{err}'
        finally:
            await page.close()


async def check_ad_with_row(context, row: dict, semaphore: asyncio.Semaphore) -> tuple[dict, str]:
    """Wraps check_ad so the row travels with the result."""
    result = await check_ad(context, row['ad_archive_id'], semaphore)
    return row, result


async def run(rows: list[dict], concurrency: int):
    total       = len(rows)
    removed_ids = []
    active_ids  = []
    error_ids   = []
    now         = datetime.now(timezone.utc).isoformat()
    semaphore   = asyncio.Semaphore(concurrency)
    batch_size  = 50
    batch: list[tuple] = []
    done_count  = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale='en-US',
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        )

        tasks = [
            asyncio.create_task(check_ad_with_row(context, r, semaphore))
            for r in rows
        ]

        for coro in asyncio.as_completed(tasks):
            row, result = await coro
            ad_id  = row['ad_archive_id']
            pn     = (row['page_name'] or '')[:35]
            cand   = (row['politician_query'] or '').split('|')[0][:25]
            done_count += 1

            if result == 'removed':
                flag = 'REMOVED ⚠️'
                removed_ids.append(ad_id)
                batch.append((ad_id, 1, now))
            elif result == 'active':
                flag = 'OK'
                active_ids.append(ad_id)
                batch.append((ad_id, 0, now))
            else:
                flag = f'ERR ({result})'
                error_ids.append(ad_id)

            print(f"[{done_count:>4}/{total}] {flag:<14} {pn:<35} {cand}")

            if len(batch) >= batch_size:
                save_results(batch)
                batch.clear()
                print(f"  ── saved {batch_size} results ──")

        if batch:
            save_results(batch)

        await browser.close()

    return removed_ids, active_ids, error_ids


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Check Cyprus ads for Meta removal")
    parser.add_argument('--all',           action='store_true',
                        help='Re-check all ads (default: only unchecked + stale active)')
    parser.add_argument('--since',         default='2025-10-01',
                        help='Only check ads with start_date >= DATE (default: 2025-10-01)')
    parser.add_argument('--no-since',      action='store_true',
                        help='Ignore the since date filter — check all dates')
    parser.add_argument('--concurrency',   type=int, default=2,
                        help='Parallel browser pages (default: 2)')
    parser.add_argument('--limit',         type=int, default=0,
                        help='Max ads to check (0 = no limit)')
    parser.add_argument('--recheck-days',  type=float, default=7,
                        help='Re-check active ads not checked in N days; fractions OK e.g. 0.125=3h (default: 7, 0=disable)')
    parser.add_argument('--include-stopped', action='store_true',
                        help='Also check ads whose stop_date has passed (default: skip them)')
    args = parser.parse_args()

    since       = '' if args.no_since else args.since
    active_only = not args.include_stopped

    blocklist = load_blocklist()
    print(f"Loaded {len(blocklist)} blocked page IDs")

    conn = sqlite3.connect(DB_PATH)
    migrate_db(conn)
    rows = load_ads(conn, blocklist, only_unchecked=not args.all,
                    since=since, limit=args.limit,
                    active_only=active_only, recheck_days=args.recheck_days)
    conn.close()

    if not rows:
        scope = f"since {since}" if since else "all dates"
        print(f"Nothing to check ({scope}). Use --all to re-check or --no-since to widen date range.")
        return

    active_msg  = "active only" if active_only else "incl. stopped"
    recheck_msg = f"re-check every {args.recheck_days}d" if args.recheck_days else "no re-check"
    scope_msg   = f"since {since}" if since else "all dates"
    print(f"\nAds to check: {len(rows):,}  ({scope_msg} · {active_msg} · {recheck_msg} · concurrency={args.concurrency})")
    print("─" * 60)

    removed_ids, active_ids, error_ids = asyncio.run(run(rows, args.concurrency))

    print("\n── Summary " + "─" * 45)
    print(f"  Checked  : {len(removed_ids) + len(active_ids):,}")
    print(f"  Active   : {len(active_ids):,}")
    print(f"  REMOVED  : {len(removed_ids):,}")
    print(f"  Errors   : {len(error_ids):,}  (not saved — will retry next run)")

    if removed_ids:
        print("\nRemoved ads:")
        conn2 = sqlite3.connect(DB_PATH)
        for ad_id in removed_ids:
            row = conn2.execute(
                "SELECT politician_query, page_name, ad_start_date FROM politician_ads WHERE ad_archive_id=?",
                (ad_id,)
            ).fetchone()
            if row:
                cand = (row[0] or '').split('|')[0]
                print(f"  https://www.facebook.com/ads/library/?id={ad_id}")
                print(f"    → {cand} | {row[1]} | {row[2]}")
        conn2.close()

    if error_ids:
        print(f"\n  Tip: {len(error_ids)} ads had errors — re-run to retry them.")


if __name__ == '__main__':
    main()
