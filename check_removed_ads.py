"""
Check which ads have been removed by Meta (violating Advertising Standards).

Fetches the snapshot URL for each ad and looks for the removal message.
Results are stored in the `removed` column of politician_ads.db.

Usage:
    python check_removed_ads.py              # check only unchecked ads
    python check_removed_ads.py --all        # re-check all ads
    python check_removed_ads.py --limit 100  # check at most N ads
"""
import sys, os, sqlite3, time, argparse, requests
from datetime import datetime, timezone
from dotenv import load_dotenv

from utils import is_business, is_excluded, is_non_political_by_category, load_exclusions, load_page_categories

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "politician_ads.db")
TOKEN   = os.environ.get("META_ACCESS_TOKEN", "")

REMOVAL_MARKERS = [
    "didn't follow our Advertising Standards",
    "This content was removed",
    "content was removed",
    "removed because it didn",
]

SNAPSHOT_BASE = "https://www.facebook.com/ads/archive/render_ad/"


def migrate_db(conn):
    """Add removed columns if they don't exist yet."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(politician_ads)").fetchall()]
    if "removed" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN removed INTEGER DEFAULT 0")
        print("  Added 'removed' column to DB")
    if "removed_checked_at" not in cols:
        conn.execute("ALTER TABLE politician_ads ADD COLUMN removed_checked_at TEXT")
        print("  Added 'removed_checked_at' column to DB")
    conn.commit()


def is_removed(ad_archive_id: str) -> bool | None:
    """
    Fetch the snapshot page for this ad and check for the removal message.
    Returns True if removed, False if active, None if the request failed.
    """
    url = SNAPSHOT_BASE
    params = {"id": ad_archive_id, "access_token": TOKEN}
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return any(m in resp.text for m in REMOVAL_MARKERS)
        # 400/401 usually means expired token, not a removal
        return None
    except requests.RequestException:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all",   action="store_true", help="Re-check all ads, not just unchecked ones")
    parser.add_argument("--limit", type=int, default=0, help="Max ads to check (0 = no limit)")
    args = parser.parse_args()

    if not TOKEN:
        sys.exit("ERROR: META_ACCESS_TOKEN not set in .env")

    excl_ids, excl_names = load_exclusions()
    page_cats            = load_page_categories()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    migrate_db(conn)

    # Select ads to check
    if args.all:
        query = "SELECT ad_archive_id, page_id, page_name, politician_query FROM politician_ads ORDER BY checked_at DESC"
    else:
        query = """
            SELECT ad_archive_id, page_id, page_name, politician_query
            FROM politician_ads
            WHERE removed_checked_at IS NULL
            ORDER BY checked_at DESC
        """

    all_rows = conn.execute(query).fetchall()

    # Apply same filters as app.py — skip business/excluded/non-political pages
    rows = [
        r for r in all_rows
        if not is_excluded(r["page_id"], r["page_name"], excl_ids, excl_names)
        and not is_business(r["page_name"] or "")
        and not is_non_political_by_category(r["page_id"], page_cats)
    ]
    skipped = len(all_rows) - len(rows)
    if skipped:
        print(f"  (skipped {skipped} business/excluded ads)")

    if args.limit:
        rows = rows[:args.limit]

    total = len(rows)
    print(f"\nAds to check: {total:,}")
    if total == 0:
        print("Nothing to do — all ads already checked. Use --all to re-check.")
        conn.close()
        return

    now = datetime.now(timezone.utc).isoformat()
    removed_count = error_count = 0

    for i, row in enumerate(rows, 1):
        ad_id    = row["ad_archive_id"]
        pn       = (row["page_name"] or "")[:35]
        cand     = (row["politician_query"] or "").split("|")[0][:25]

        result = is_removed(ad_id)

        if result is True:
            removed_count += 1
            flag = "REMOVED"
            conn.execute(
                "UPDATE politician_ads SET removed=1, removed_checked_at=? WHERE ad_archive_id=?",
                (now, ad_id)
            )
        elif result is False:
            flag = "OK"
            conn.execute(
                "UPDATE politician_ads SET removed=0, removed_checked_at=? WHERE ad_archive_id=?",
                (now, ad_id)
            )
        else:
            error_count += 1
            flag = "ERR"
            # Don't update removed_checked_at so it gets retried next time

        print(f"[{i:>4}/{total}] {flag:<8} {pn:<35} {cand}")

        # Commit every 50 rows
        if i % 50 == 0:
            conn.commit()

        time.sleep(0.4)

    conn.commit()
    conn.close()

    print(f"\n── Summary ───────────────────────────────────────")
    print(f"  Checked:  {total - error_count:,}")
    print(f"  Removed:  {removed_count:,}")
    print(f"  Errors:   {error_count:,}  (token expired or network issue)")
    if error_count > 0:
        print(f"  Tip: refresh META_ACCESS_TOKEN in .env and re-run")


if __name__ == "__main__":
    main()
