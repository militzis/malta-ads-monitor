"""
Re-fetch spend & impressions data for Cyprus ads that have NULL spend_min.

Queries politician_ads.db for distinct page_ids of ads missing spend data,
then hits the Meta API with search_page_ids for each page and updates any
matching ads with the returned spend/impressions figures.

Usage:
    python refresh_spend_cy.py              # all ads with NULL spend
    python refresh_spend_cy.py --removed    # also include removed=1 ads
    python refresh_spend_cy.py --limit 50   # stop after 50 pages
"""

import os, sys, json, sqlite3, time, argparse, requests
from datetime import datetime, timezone
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(override=True)

BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'politician_ads.db')
META_AD_LIBRARY_URL = 'https://graph.facebook.com/v25.0/ads_archive'
COUNTRY = 'CY'
SINCE   = '2025-09-01'


def fetch_page_ads(page_id: str, token: str) -> list[dict]:
    params = {
        'ad_type': 'ALL',
        'ad_reached_countries': json.dumps([COUNTRY]),
        'ad_delivery_date_min': SINCE,
        'fields': 'id,impressions,spend,currency,ad_delivery_stop_time',
        'search_page_ids': json.dumps([page_id]),
        'limit': 100,
        'access_token': token,
    }
    all_ads = []
    url = META_AD_LIBRARY_URL
    try:
        while url:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                err = resp.json().get('error', {}).get('message', '')
                print(f'    [!] API error {resp.status_code}: {err}')
                return []
            data = resp.json()
            all_ads.extend(data.get('data', []))
            next_url = data.get('paging', {}).get('next')
            url = next_url
            params = {}
            if next_url:
                time.sleep(0.3)
    except requests.RequestException as e:
        print(f'    [!] Request failed: {e}')
    return all_ads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--removed', action='store_true',
                        help='Also refresh removed=1 ads (default: active only)')
    parser.add_argument('--limit', type=int, default=0,
                        help='Max pages to process (0 = no limit)')
    args = parser.parse_args()

    token = os.environ.get('META_ACCESS_TOKEN')
    if not token:
        sys.exit('ERROR: META_ACCESS_TOKEN not set')

    conn = sqlite3.connect(DB_PATH)

    removed_filter = '' if args.removed else "AND removed = 0"

    # Get distinct page_ids that have at least one ad with NULL spend
    # Only dashboard-visible ads: election_related = 'YES'
    page_rows = conn.execute(f"""
        SELECT DISTINCT page_id, page_name
        FROM politician_ads
        WHERE spend_min IS NULL
          AND page_id IS NOT NULL
          AND election_related = 'YES'
          {removed_filter}
        ORDER BY page_id
    """).fetchall()

    if args.limit:
        page_rows = page_rows[:args.limit]

    print(f'Pages to refresh: {len(page_rows)}')
    print(f'Fetching ads from: {SINCE}\n')

    total_updated = 0
    total_still_null = 0
    now = datetime.now(timezone.utc).isoformat()

    for i, (page_id, page_name) in enumerate(page_rows, 1):
        print(f'[{i}/{len(page_rows)}] {(page_name or page_id)[:50]}', end='  ')

        # Get the ad_archive_ids we need to update for this page
        null_ads = {
            row[0]: row
            for row in conn.execute(f"""
                SELECT ad_archive_id, spend_min, impressions_min
                FROM politician_ads
                WHERE page_id = ? AND spend_min IS NULL
                  AND election_related = 'YES'
                  {removed_filter}
            """, (page_id,)).fetchall()
        }

        api_ads = fetch_page_ads(page_id, token)

        updated = 0
        for ad in api_ads:
            ad_id = ad.get('id')
            if ad_id not in null_ads:
                continue
            spend = ad.get('spend', {})
            imp   = ad.get('impressions', {})
            if spend.get('lower_bound') is None and imp.get('lower_bound') is None:
                continue  # API still not returning data for this ad
            conn.execute("""
                UPDATE politician_ads
                SET spend_min       = ?,
                    spend_max       = ?,
                    impressions_min = ?,
                    impressions_max = ?,
                    currency        = ?,
                    checked_at      = ?
                WHERE ad_archive_id = ?
            """, (
                spend.get('lower_bound'),
                spend.get('upper_bound'),
                imp.get('lower_bound'),
                imp.get('upper_bound'),
                ad.get('currency'),
                now,
                ad_id,
            ))
            updated += 1

        still_null = len(null_ads) - updated
        print(f'updated {updated}/{len(null_ads)} ads  ({still_null} still NULL)')
        total_updated    += updated
        total_still_null += still_null

        conn.commit()

        if i < len(page_rows):
            time.sleep(1)

    conn.close()

    print(f'\n{"─"*50}')
    print(f'Total ads updated : {total_updated}')
    print(f'Still NULL        : {total_still_null}  (Meta not returning data for these pages)')


if __name__ == '__main__':
    main()
