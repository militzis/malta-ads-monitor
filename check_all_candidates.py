"""
check_all_candidates.py — unified candidate scanner for CY (Greek + Latin) and MT.

Replaces three separate scripts — select a source with --source:

Usage:
    python check_all_candidates.py --source greek --chunk-size 30   # CY Greek names
    python check_all_candidates.py --source latin --chunk-size 30   # CY transliterated names
    python check_all_candidates.py --source mt    --chunk-size 30   # Malta
    python check_all_candidates.py --source greek --full            # re-fetch all history
"""

import os, sys, csv, json, sqlite3, argparse, re, requests, time
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(override=True)

BASE                = os.path.dirname(os.path.abspath(__file__))
META_AD_LIBRARY_URL = "https://graph.facebook.com/v25.0/ads_archive"
DEFAULT_START       = "2025-09-01"   # hard floor — never fetch before this date
OVERLAP_DAYS        = 7              # re-fetch last N days to catch updated spend/impressions


# ── Source configuration ──────────────────────────────────────────────────────
# Each entry fully describes one scanning mode.  Add a new country here and it
# just works — no code changes needed elsewhere in the file.
SOURCES = {
    "greek": {
        "db":             os.path.join(BASE, "politician_ads.db"),
        "state_file":     os.path.join(BASE, "fetch_state.json"),
        "candidates":     os.path.join(BASE, "candidates.csv"),
        "blocklist":      os.path.join(BASE, "page_blocklist.json"),
        "country":        "CY",
        "chunk_key":      "greek",
        "db_source":      "greek",
        "transliterate":  False,
        "page_id_search": True,    # page-ID lookup + name search
    },
    "latin": {
        "db":             os.path.join(BASE, "politician_ads.db"),
        "state_file":     os.path.join(BASE, "fetch_state.json"),
        "candidates":     os.path.join(BASE, "candidates.csv"),
        "blocklist":      os.path.join(BASE, "page_blocklist.json"),
        "country":        "CY",
        "chunk_key":      "latin",
        "db_source":      "latin",
        "transliterate":  True,
        "page_id_search": False,   # page IDs already covered by greek run; name only here
    },
    "mt": {
        "db":             os.path.join(BASE, "politician_ads_mt.db"),
        "state_file":     os.path.join(BASE, "fetch_state_mt.json"),
        "candidates":     os.path.join(BASE, "candidates_mt.csv"),
        "blocklist":      os.path.join(BASE, "page_blocklist_mt.json"),
        "country":        "MT",
        "chunk_key":      "mt",
        "db_source":      "mt",
        "transliterate":  False,
        "page_id_search": True,
    },
}


# ── Party terms (per source) ───────────────────────────────────────────────────
PARTY_TERMS = {
    "greek": {
        "ΔΗΣΥ":            ["δησυ", "δημοκρατικός συναγερμός", "disy"],
        "ΑΚΕΛ":            ["ακελ", "akel"],
        "ΔΗΚΟ":            ["δηκο", "δημοκρατικό κόμμα", "diko"],
        "ΕΔΕΚ":            ["εδεκ", "edek", "σοσιαλδημοκράτες"],
        "ΕΛΑΜ":            ["ελαμ", "εθνικό λαϊκό μέτωπο", "elam"],
        "ΑΜΔΗ":            ["αμδη", "άμεση δημοκρατία", "αδ"],
        "ΒΟΛΤ":            ["βολτ", "volt"],
        "ΔΕΚ":             ["δεκ", "δημοκρατικό εθνικό κίνημα"],
        "ΟΙΚΟΛΟΓΟΙ":       ["οικολόγοι", "πράσινοι", "οικολογικό", "κίνημα οικολόγων-συνεργασία πολιτών"],
        "ΑΛΜΑ":            ["αλμα"],
        "ΔΗΠΑ":            ["δηπα", "δημοκρατική παράταξη"],
        "ΣΗΚΟΥ ΠΑΝΩ":      ["σήκου πάνω"],
    },
    "latin": {
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
        "ΑΓΡΟΝΟΜΟΣ":       ["agronomos"],
        "ΑΚΡΟ":            ["akro"],
        "ΔΑ":              ["dimokratiki allagi"],
        "ΕΝΕΡΓΟΙ ΠΟΛΙΤΕΣ": ["energoi polites", "kynigon"],
        "ΛΑΕ":             ["laikos agonas"],
        "ΛΑΚΕΔΑΙΜΟΝΙΟΙ":   ["lakedaimonioi"],
        "ΠΡΑΣΙΝΟΙ":        ["prasino komma", "prasini"],
    },
    "mt": {
        "PN":              ["pn", "nationalist", "partit nazzjonalista"],
        "PL":              ["pl", "labour", "partit laburista", "laburista"],
        "Momentum":        ["momentum"],
        "ADPD":            ["adpd", "alternattiva demokratika"],
        "Imperium Ewropa": ["imperium ewropa", "imperium"],
        "Ahwa Maltin":     ["ahwa maltin", "partit popolari", "people's party"],
        "Independent":     ["independent", "indipendenti", "indipendent"],
    },
}


# ── Transliteration (used only for --source latin) ────────────────────────────
_DIGRAPHS = {
    'ου':'ou', 'αυ':'av', 'ευ':'ev', 'οι':'oi',
    'αι':'ai', 'ει':'ei', 'υι':'yi', 'γγ':'ng',
    'γκ':'gk', 'μπ':'mp', 'ντ':'nt', 'τζ':'tz',
    'ού':'ou', 'αύ':'av', 'εύ':'ev', 'οί':'oi',
    'αί':'ai', 'εί':'ei',
    'όυ':'ou', 'άυ':'av', 'έυ':'ev',
}
_CHARS = {
    'α':'a',  'β':'v',  'γ':'g',  'δ':'d',  'ε':'e',  'ζ':'z',
    'η':'i',  'θ':'th', 'ι':'i',  'κ':'k',  'λ':'l',  'μ':'m',
    'ν':'n',  'ξ':'x',  'ο':'o',  'π':'p',  'ρ':'r',  'σ':'s',
    'ς':'s',  'τ':'t',  'υ':'y',  'φ':'f',  'χ':'ch', 'ψ':'ps',
    'ω':'o',  'ά':'a',  'έ':'e',  'ή':'i',  'ί':'i',  'ό':'o',
    'ύ':'y',  'ώ':'o',  'ϊ':'i',  'ϋ':'y',  'ΐ':'i',  'ΰ':'y',
}

def _translit(text: str) -> str:
    text = text.lower()
    result, i = "", 0
    while i < len(text):
        two = text[i:i+2]
        if two in _DIGRAPHS:
            result += _DIGRAPHS[two]
            i += 2
        else:
            result += _CHARS.get(text[i], text[i])
            i += 1
    return result

def name_to_latin(name: str) -> str:
    """'Νικολάου Ανδρέας' → 'Nikolaou Andreas'"""
    return " ".join(_translit(part).capitalize() for part in name.strip().split())


# ── Blocklist ─────────────────────────────────────────────────────────────────
def load_blocklist(blocklist_file: str) -> set:
    if os.path.exists(blocklist_file):
        with open(blocklist_file, encoding='utf-8') as f:
            return set(json.load(f).get('pages', {}).keys())
    return set()


# ── Fetch-state helpers ───────────────────────────────────────────────────────
def load_fetch_since(state_file: str, chunk_key: str) -> str:
    """Return ad_delivery_date_min for incremental fetching."""
    if os.path.exists(state_file):
        try:
            with open(state_file, encoding='utf-8') as f:
                state = json.load(f)
            last_str = state.get(chunk_key)
            if last_str:
                last_date = date.fromisoformat(last_str)
                since = max(
                    date.fromisoformat(DEFAULT_START),
                    last_date - timedelta(days=OVERLAP_DAYS),
                )
                return since.isoformat()
        except Exception as e:
            print(f"[state] Could not read {state_file}: {e}")
    return DEFAULT_START


def save_fetch_date(state_file: str, chunk_key: str):
    """Persist today as the last-run date (advances the incremental window)."""
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            pass
    state[chunk_key] = date.today().isoformat()
    tmp = state_file + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_file)
        print(f"[state] Saved last-run date for '{chunk_key}': {state[chunk_key]}")
    except Exception as e:
        print(f"[state] WARNING: could not save last-run date: {e}")


def load_chunk_pos(state_file: str, chunk_key: str) -> int:
    """Return the next candidate index for chunked runs."""
    if os.path.exists(state_file):
        try:
            with open(state_file, encoding='utf-8') as f:
                return int(json.load(f).get(f"{chunk_key}_chunk_pos", 0))
        except Exception:
            pass
    return 0


def save_chunk_pos(state_file: str, chunk_key: str, pos: int):
    """Persist the next chunk start position."""
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            pass
    state[f"{chunk_key}_chunk_pos"] = pos
    tmp = state_file + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_file)
        print(f"[state] Chunk position for '{chunk_key}': {pos}")
    except Exception as e:
        print(f"[state] WARNING: could not save chunk position: {e}")


# ── Database ──────────────────────────────────────────────────────────────────
def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
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
            ad_text          TEXT,
            first_seen_at    TEXT,
            election_related TEXT
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(politician_ads)").fetchall()}
    for col, defn in [
        ('source',             "TEXT DEFAULT 'greek'"),
        ('removed',            "INTEGER DEFAULT 0"),
        ('removed_checked_at', "TEXT"),
        ('ad_text',            "TEXT"),
        ('first_seen_at',      "TEXT"),
        ('election_related',   "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE politician_ads ADD COLUMN {col} {defn}")
    conn.commit()
    conn.close()


def upsert_ads(db_path: str, ads: list[dict], db_source: str) -> int:
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for ad in ads:
        imp   = ad.get("impressions", {})
        spend = ad.get("spend", {})
        try:
            bodies  = " ".join(ad.get("ad_creative_bodies") or [])
            titles  = " ".join(ad.get("ad_creative_link_titles") or [])
            ad_text = (bodies + " " + titles).strip()[:1000] or None

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
                    impressions_min    = excluded.impressions_min,
                    impressions_max    = excluded.impressions_max,
                    spend_min          = excluded.spend_min,
                    spend_max          = excluded.spend_max,
                    ad_stop_date       = excluded.ad_stop_date,
                    page_name          = excluded.page_name,
                    checked_at         = excluded.checked_at,
                    ad_text            = excluded.ad_text,
                    removed            = CASE
                                           WHEN excluded.removed = 1 THEN 1
                                           ELSE politician_ads.removed
                                         END,
                    removed_checked_at = CASE
                                           WHEN excluded.removed_checked_at IS NOT NULL
                                           THEN excluded.removed_checked_at
                                           ELSE politician_ads.removed_checked_at
                                         END
                    -- first_seen_at intentionally NOT updated: set once on first INSERT only
            """, (
                ad.get("id"), ad.get("_query"), ad.get("_party"), ad.get("_district"),
                ad.get("page_name"), ad.get("page_id"), ad.get("bylines"),
                1 if ad.get("is_third_party") else 0,
                ad.get("ad_delivery_start_time"), ad.get("ad_delivery_stop_time"),
                imp.get("lower_bound"), imp.get("upper_bound"),
                spend.get("lower_bound"), spend.get("upper_bound"),
                ad.get("currency"), ad.get("ad_snapshot_url"),
                now, db_source, 0, None, ad_text, now,
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"    [db] skipped ad {ad.get('id')}: {e}")
    conn.commit()
    conn.close()
    return inserted


# ── API ───────────────────────────────────────────────────────────────────────
def _fetch_raw(params: dict) -> list[dict]:
    """Paginate through a single API query and return all ads."""
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
            url      = next_url
            params   = {}
            if next_url:
                time.sleep(0.5)
    except requests.RequestException as e:
        print(f"    [!] Request failed: {e}")
    return all_ads


def _base_params(country: str, since_date: str) -> dict:
    return {
        "ad_type":              "ALL",
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


def fetch_ads(search_name: str, page_ids: list[str], country: str,
              since_date: str, do_page_id_search: bool) -> tuple[list[dict], list[dict]]:
    """
    Fetch ads using a name search and (optionally) per-page-ID searches.
    Returns (page_id_ads, name_ads) — deduplicated by the caller.
    """
    base = _base_params(country, since_date)

    page_id_ads: list[dict] = []
    if do_page_id_search:
        for pid in page_ids:
            p = dict(base)
            p["search_page_ids"] = json.dumps([pid])
            page_id_ads.extend(_fetch_raw(p))
            if pid != page_ids[-1]:
                time.sleep(0.5)

    p = dict(base)
    p["search_terms"] = search_name
    name_ads = _fetch_raw(p)

    return page_id_ads, name_ads


# ── Report ────────────────────────────────────────────────────────────────────
def print_report(db_path: str, source: str):
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM politician_ads").fetchone()[0]
    print(f"\n{'='*60}")
    print(f"REPORT — {source.upper()} SCAN")
    print(f"{'='*60}")
    print(f"Total ads in DB : {total:,}")
    rows = conn.execute("""
        SELECT politician_query, party, district,
               COUNT(*) as total_ads, MAX(impressions_max) as max_imp
        FROM politician_ads
        GROUP BY politician_query ORDER BY total_ads DESC
    """).fetchall()
    for q, party, district, n, imp in rows:
        name = q.split("|")[0]
        print(f"\n  {name} ({party or '?'} — {district or '?'})")
        print(f"    Ads: {n}" + (f"  |  Max imp: {imp:,}" if imp else ""))
    conn.close()
    print(f"\n{'='*60}")
    print(f"Data saved to: {db_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Unified candidate scanner for CY (Greek/Latin) and MT."
    )
    parser.add_argument("--source",     choices=["greek", "latin", "mt"], default="greek",
                        help="Scanning mode (default: greek)")
    parser.add_argument("--parties",    nargs="+",
                        help="Only process these parties")
    parser.add_argument("--full",       action="store_true",
                        help="Ignore saved state, fetch from DEFAULT_START")
    parser.add_argument("--start",      type=int, default=1,
                        help="Start from candidate number N (manual override)")
    parser.add_argument("--chunk-size", type=int, default=0,
                        help="Process N candidates per run; cursor auto-advances (0=all)")
    args = parser.parse_args()

    cfg         = SOURCES[args.source]
    party_terms = PARTY_TERMS[args.source]
    state_file  = cfg["state_file"]
    chunk_key   = cfg["chunk_key"]

    if not os.path.exists(cfg["candidates"]):
        sys.exit(f"Error: {cfg['candidates']} not found.")

    page_blocklist = load_blocklist(cfg["blocklist"])
    init_db(cfg["db"])

    with open(cfg["candidates"], encoding="utf-8") as f:
        candidates = list(csv.DictReader(f))

    if args.parties:
        pf = [p.strip() for p in args.parties]
        candidates = [c for c in candidates if c.get("party", "").strip() in pf]
        print(f"\nFiltering to parties: {', '.join(pf)}")

    # ── Chunk mode: auto-advance cursor across runs ───────────────────────────
    chunk_complete   = False
    total_candidates = len(candidates)
    next_pos         = 0   # initialise; only meaningful when chunk_size > 0

    if args.chunk_size > 0:
        chunk_pos = load_chunk_pos(state_file, chunk_key)
        if chunk_pos >= total_candidates:
            print(f"[chunk] Position {chunk_pos} beyond list size {total_candidates} — resetting to 0")
            chunk_pos = 0
        chunk_end = chunk_pos + args.chunk_size
        chunk = candidates[chunk_pos:chunk_end]
        if not chunk:
            print("[chunk] Empty chunk — skipping this run, position unchanged.")
            save_chunk_pos(state_file, chunk_key, chunk_pos)
            return
        next_pos = chunk_end if chunk_end < total_candidates else 0
        print(f"\n[chunk] Position {chunk_pos}–{chunk_pos + len(chunk) - 1} of {total_candidates} "
              f"(next run starts at {next_pos})")
        candidates = chunk
        if next_pos == 0:
            chunk_complete = True
    elif args.start > 1:
        candidates = candidates[args.start - 1:]
        print(f"\nStarting from candidate #{args.start}")

    # ── Incremental date window ───────────────────────────────────────────────
    # In chunk mode, since_date is held fixed until the cycle completes so that
    # all chunks query the same date window.
    since_date = DEFAULT_START if args.full else load_fetch_since(state_file, chunk_key)
    print(f"\nSource     : {args.source}  |  Country: {cfg['country']}")
    print(f"Candidates : {len(candidates)}  |  Since: {since_date}"
          f"{'  [--full]' if args.full else '  (incremental)'}\n")

    for i, c in enumerate(candidates, 1):
        name     = c.get("name", "").strip()
        page_id  = c.get("page_id", "").strip()
        party    = c.get("party", "").strip()
        district = c.get("district", "").strip()
        if not name:
            continue

        # Apply transliteration for latin source
        if cfg["transliterate"]:
            search_name = name_to_latin(name)
            print(f"[{i}/{len(candidates)}] {name} → '{search_name}' ({party})")
        else:
            search_name = name
            page_ids    = [p.strip() for p in page_id.split(",") if p.strip()]
            print(f"[{i}/{len(candidates)}] {name} ({party})"
                  + (f"  [{len(page_ids)} page IDs]" if page_ids else ""))

        page_ids = [p.strip() for p in page_id.split(",") if p.strip()]
        page_id_ads, name_ads = fetch_ads(
            search_name, page_ids, cfg["country"], since_date, cfg["page_id_search"]
        )

        # Relevance filter on name-search results
        name_parts  = [p for p in search_name.lower().split() if len(p) > 3]
        pt          = party_terms.get(party, [party.lower()] if party else [])

        def ad_is_relevant(ad):
            if str(ad.get("page_id") or "") in page_blocklist:
                return False
            page   = (ad.get("page_name") or "").lower()
            bodies = " ".join(ad.get("ad_creative_bodies") or []).lower()
            titles = " ".join(ad.get("ad_creative_link_titles") or []).lower()
            text   = bodies + " " + titles
            # Page name contains part of the candidate's (search) name
            if any(p in page for p in name_parts):
                return True
            # Ad text must contain ALL name parts (whole-word) AND a party term
            all_name = (all(re.search(r'(?<!\w)' + re.escape(p) + r'(?!\w)', text)
                            for p in name_parts) if name_parts else False)
            party_match = any(t in text for t in pt)
            return all_name and party_match

        before   = len(name_ads)
        name_ads = [ad for ad in name_ads if ad_is_relevant(ad)]
        filtered = before - len(name_ads)
        if filtered:
            print(f"    (name search: filtered {filtered} irrelevant ads)")

        # Merge and deduplicate by ad_archive_id (page-ID ads take priority)
        seen: set    = set()
        all_ads: list = []
        for ad in page_id_ads + name_ads:
            aid = ad.get("id")
            if aid and aid not in seen:
                seen.add(aid)
                all_ads.append(ad)

        for ad in all_ads:
            ad["_query"]        = f"{name}|{party}|{district}"
            ad["_party"]        = party
            ad["_district"]     = district
            ad["is_third_party"] = 0

        saved = upsert_ads(cfg["db"], all_ads, cfg["db_source"])
        if cfg["page_id_search"]:
            print(f"    page_id: {len(page_id_ads)}  |  name: {len(name_ads)}"
                  f"  →  {len(all_ads)} unique  ({saved} saved)")
        else:
            print(f"    name: {len(name_ads)}  →  {len(all_ads)} unique  ({saved} saved)")

        if i < len(candidates):
            time.sleep(10)

    # ── Persist state ─────────────────────────────────────────────────────────
    if args.chunk_size > 0:
        save_chunk_pos(state_file, chunk_key, next_pos)
        if chunk_complete:
            save_fetch_date(state_file, chunk_key)
            print("[state] Full cycle complete — since_date advanced.")
        else:
            print(f"[state] Partial cycle — since_date unchanged until all {total_candidates} done.")
    else:
        save_fetch_date(state_file, chunk_key)

    print_report(cfg["db"], args.source)


if __name__ == "__main__":
    main()
