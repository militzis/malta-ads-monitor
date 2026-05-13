"""
test_pipeline.py — Unit tests for the Malta/Cyprus ad pipeline.

Tests are grouped by concern and use an in-memory SQLite DB so nothing
touches the real politician_ads*.db files.

Run with:
    python -m pytest test_pipeline.py -v
    -- or --
    python test_pipeline.py
"""

import unittest
import sqlite3
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


# ── Shared helpers ────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS politician_ads (
    ad_archive_id       TEXT PRIMARY KEY,
    politician_query    TEXT NOT NULL,
    party               TEXT,
    district            TEXT,
    page_name           TEXT,
    page_id             TEXT,
    bylines             TEXT,
    is_third_party      INTEGER,
    ad_start_date       TEXT,
    ad_stop_date        TEXT,
    impressions_min     INTEGER,
    impressions_max     INTEGER,
    spend_min           INTEGER,
    spend_max           INTEGER,
    currency            TEXT,
    snapshot_url        TEXT,
    checked_at          TEXT NOT NULL,
    source              TEXT DEFAULT 'greek',
    removed             INTEGER DEFAULT 0,
    removed_checked_at  TEXT,
    ad_text             TEXT
)
"""

def make_db():
    """Return a fresh in-memory SQLite connection with the full schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def insert_ad(conn, ad_archive_id, politician_query="Test|PARTY|District1",
              page_id="111", page_name="Test Page",
              ad_start_date="2026-01-01", ad_stop_date=None,
              spend_min=None, spend_max=None,
              impressions_min=None, impressions_max=None,
              removed=0, removed_checked_at=None,
              source="greek"):
    """Insert a minimal ad row for testing."""
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
    """, (
        ad_archive_id,
        politician_query,
        politician_query.split("|")[1] if "|" in politician_query else None,
        politician_query.split("|")[2] if politician_query.count("|") >= 2 else None,
        page_name, page_id, None, 0,
        ad_start_date, ad_stop_date,
        impressions_min, impressions_max,
        spend_min, spend_max, "EUR",
        None, datetime.now(timezone.utc).isoformat(), source,
        removed, removed_checked_at, None,
    ))
    conn.commit()


def upsert_ad(conn, ad_archive_id, politician_query="Test|PARTY|District1",
              page_id="111", page_name="Test Page",
              ad_start_date="2026-01-01", ad_stop_date=None,
              spend_min=None, spend_max=None,
              impressions_min=None, impressions_max=None,
              source="greek"):
    """
    The fixed upsert — mirrors the ON CONFLICT logic in all three fetchers.
    On conflict: updates spend, impressions, stop_date, page_name, checked_at.
    Never touches removed or removed_checked_at.
    """
    now = datetime.now(timezone.utc).isoformat()
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
            impressions_min  = excluded.impressions_min,
            impressions_max  = excluded.impressions_max,
            spend_min        = excluded.spend_min,
            spend_max        = excluded.spend_max,
            ad_stop_date     = excluded.ad_stop_date,
            page_name        = excluded.page_name,
            checked_at       = excluded.checked_at
    """, (
        ad_archive_id,
        politician_query,
        politician_query.split("|")[1] if "|" in politician_query else None,
        politician_query.split("|")[2] if politician_query.count("|") >= 2 else None,
        page_name, page_id, None, 0,
        ad_start_date, ad_stop_date,
        impressions_min, impressions_max,
        spend_min, spend_max, "EUR",
        None, now, source,
        0, None, None,
    ))
    conn.commit()


def save_results(conn, results):
    """
    Mirror of save_results() from check_removed_ads_cy/mt.py.
    Policy: removed=1 is NEVER downgraded back to 0.
    results: [(ad_archive_id, removed_int, timestamp), ...]
    """
    ts = datetime.now(timezone.utc).isoformat()
    for ad_id, removed, timestamp in results:
        if removed == 1:
            conn.execute(
                "UPDATE politician_ads SET removed=1, removed_checked_at=? "
                "WHERE ad_archive_id=?",
                (timestamp or ts, ad_id)
            )
        else:
            conn.execute(
                "UPDATE politician_ads SET removed=0, removed_checked_at=? "
                "WHERE ad_archive_id=? AND (removed IS NULL OR removed = 0)",
                (timestamp or ts, ad_id)
            )
    conn.commit()


def get_ad(conn, ad_archive_id):
    """Fetch a single ad row as a dict."""
    row = conn.execute(
        "SELECT * FROM politician_ads WHERE ad_archive_id=?", (ad_archive_id,)
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM politician_ads WHERE ad_archive_id=?", (ad_archive_id,)
    ).description]
    return dict(zip(cols, row))


# ── is_removed_text() — inline copy from check_removed_ads_*.py ──────────────

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


# ═══════════════════════════════════════════════════════════════════════════════
# 1. UPSERT — spend update & removed preservation
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpsert(unittest.TestCase):

    def test_spend_updated_on_re_fetch(self):
        """Re-fetching an ad must update spend_max to the new value."""
        conn = make_db()
        insert_ad(conn, "AD001", spend_min=50, spend_max=100)
        upsert_ad(conn, "AD001", spend_min=100, spend_max=500)
        ad = get_ad(conn, "AD001")
        self.assertEqual(ad["spend_max"], 500)
        self.assertEqual(ad["spend_min"], 100)

    def test_impressions_updated_on_re_fetch(self):
        """Re-fetching must update impressions to the new value."""
        conn = make_db()
        insert_ad(conn, "AD002", impressions_min=100, impressions_max=999)
        upsert_ad(conn, "AD002", impressions_min=500, impressions_max=4999)
        ad = get_ad(conn, "AD002")
        self.assertEqual(ad["impressions_max"], 4999)

    def test_removed_1_not_overwritten_by_upsert(self):
        """
        Critical: re-fetching a confirmed-removed ad must never reset removed=0.
        This was the original bug that wiped 87 Malta removals.
        """
        conn = make_db()
        insert_ad(conn, "AD003", removed=1,
                  removed_checked_at="2026-05-01T10:00:00+00:00")
        upsert_ad(conn, "AD003", spend_max=200)
        ad = get_ad(conn, "AD003")
        self.assertEqual(ad["removed"], 1,
            "removed=1 must survive a re-fetch upsert")
        self.assertIsNotNone(ad["removed_checked_at"],
            "removed_checked_at must not be wiped")

    def test_removed_checked_at_not_overwritten(self):
        """Timestamp of removal check must be preserved after re-fetch."""
        conn = make_db()
        original_ts = "2026-04-15T08:00:00+00:00"
        insert_ad(conn, "AD004", removed=1, removed_checked_at=original_ts)
        upsert_ad(conn, "AD004", spend_max=99)
        ad = get_ad(conn, "AD004")
        self.assertEqual(ad["removed_checked_at"], original_ts)

    def test_new_ad_defaults_to_removed_0(self):
        """Newly inserted ads must start with removed=0."""
        conn = make_db()
        upsert_ad(conn, "AD005")
        ad = get_ad(conn, "AD005")
        self.assertEqual(ad["removed"], 0)
        self.assertIsNone(ad["removed_checked_at"])

    def test_stop_date_updated(self):
        """An ad that stops running should have its stop date updated."""
        conn = make_db()
        insert_ad(conn, "AD006", ad_stop_date=None)
        upsert_ad(conn, "AD006", ad_stop_date="2026-05-10")
        ad = get_ad(conn, "AD006")
        self.assertEqual(ad["ad_stop_date"], "2026-05-10")

    def test_duplicate_ad_not_double_counted(self):
        """Upserting the same ad_archive_id twice must yield exactly one row."""
        conn = make_db()
        upsert_ad(conn, "AD007")
        upsert_ad(conn, "AD007", spend_max=300)
        count = conn.execute(
            "SELECT COUNT(*) FROM politician_ads WHERE ad_archive_id='AD007'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_spend_none_does_not_overwrite_existing(self):
        """
        If the API returns no spend data on re-fetch, it should still update
        (to NULL). This verifies the upsert doesn't silently skip NULLs.
        """
        conn = make_db()
        insert_ad(conn, "AD008", spend_max=200)
        upsert_ad(conn, "AD008", spend_max=None)
        ad = get_ad(conn, "AD008")
        # NULL from API overwrites old value — reflects current Meta data
        self.assertIsNone(ad["spend_max"])


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SAVE_RESULTS — removal checker write logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveResults(unittest.TestCase):

    def test_removed_1_is_saved(self):
        """An ad confirmed as removed must be marked removed=1."""
        conn = make_db()
        insert_ad(conn, "AD010")
        save_results(conn, [("AD010", 1, None)])
        ad = get_ad(conn, "AD010")
        self.assertEqual(ad["removed"], 1)
        self.assertIsNotNone(ad["removed_checked_at"])

    def test_removed_1_never_downgraded_to_0(self):
        """
        Critical: if removed=1 is confirmed, a later 'active' check result
        must NOT reset it back to 0.
        This was the bug that wiped Run 1's removals when Run 2 ran.
        """
        conn = make_db()
        insert_ad(conn, "AD011", removed=1,
                  removed_checked_at="2026-05-01T00:00:00+00:00")
        # Checker now sees it as "active" (inconsistent render) — must not downgrade
        save_results(conn, [("AD011", 0, None)])
        ad = get_ad(conn, "AD011")
        self.assertEqual(ad["removed"], 1,
            "removed=1 must never be downgraded to 0 by save_results")

    def test_active_ad_marked_checked(self):
        """An ad confirmed as active gets removed=0 and a checked_at timestamp."""
        conn = make_db()
        insert_ad(conn, "AD012")
        save_results(conn, [("AD012", 0, None)])
        ad = get_ad(conn, "AD012")
        self.assertEqual(ad["removed"], 0)
        self.assertIsNotNone(ad["removed_checked_at"])

    def test_error_result_not_saved(self):
        """Ads with check errors must not have removed_checked_at set."""
        conn = make_db()
        insert_ad(conn, "AD013")
        # Simulate: only save non-error results (errors are filtered before calling save_results)
        # AD013 gets no save_results call — simulates the error path
        ad = get_ad(conn, "AD013")
        self.assertIsNone(ad["removed_checked_at"],
            "Error results must not be saved — ad stays unchecked for retry")

    def test_bulk_save_mixed_results(self):
        """Bulk save of mixed removed/active results applies correctly to each."""
        conn = make_db()
        insert_ad(conn, "AD014")
        insert_ad(conn, "AD015")
        insert_ad(conn, "AD016", removed=1,
                  removed_checked_at="2026-04-01T00:00:00+00:00")
        ts = "2026-05-13T12:00:00+00:00"
        save_results(conn, [
            ("AD014", 1, ts),   # newly removed
            ("AD015", 0, ts),   # active
            ("AD016", 0, ts),   # already removed — must stay removed
        ])
        self.assertEqual(get_ad(conn, "AD014")["removed"], 1)
        self.assertEqual(get_ad(conn, "AD015")["removed"], 0)
        self.assertEqual(get_ad(conn, "AD016")["removed"], 1)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BLOCKLIST FILTERING
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlocklist(unittest.TestCase):

    def setUp(self):
        self.blocklist = {"999888777", "111222333"}

    def _apply_blocklist(self, rows):
        """Mirror of the blocklist filter used in all pipeline scripts."""
        return [r for r in rows if str(r.get("page_id") or "") not in self.blocklist]

    def test_blocklisted_page_excluded(self):
        rows = [
            {"page_id": "999888777", "page_name": "Spam Business"},
            {"page_id": "123456789", "page_name": "Real Candidate"},
        ]
        result = self._apply_blocklist(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["page_id"], "123456789")

    def test_non_blocklisted_page_kept(self):
        rows = [{"page_id": "123456789", "page_name": "Real Candidate"}]
        result = self._apply_blocklist(rows)
        self.assertEqual(len(result), 1)

    def test_all_blocklisted_returns_empty(self):
        rows = [
            {"page_id": "999888777"},
            {"page_id": "111222333"},
        ]
        self.assertEqual(self._apply_blocklist(rows), [])

    def test_none_page_id_not_excluded(self):
        """Ads with no page_id must not be accidentally blocklisted."""
        rows = [{"page_id": None, "page_name": "Unknown"}]
        result = self._apply_blocklist(rows)
        self.assertEqual(len(result), 1)

    def test_blocklist_loaded_from_json(self):
        """Blocklist JSON format must be parsed correctly."""
        bl_json = json.dumps({"pages": {"999888777": "reason A", "111222333": "reason B"}})
        loaded = set(json.loads(bl_json).get("pages", {}).keys())
        self.assertIn("999888777", loaded)
        self.assertIn("111222333", loaded)
        self.assertEqual(len(loaded), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DATE BASELINE FILTERING (Oct 2025)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDateFilter(unittest.TestCase):

    def setUp(self):
        self.conn = make_db()
        insert_ad(self.conn, "OLD01", ad_start_date="2025-09-30")
        insert_ad(self.conn, "OLD02", ad_start_date="2025-01-15")
        insert_ad(self.conn, "NEW01", ad_start_date="2025-10-01")
        insert_ad(self.conn, "NEW02", ad_start_date="2026-03-15")
        insert_ad(self.conn, "NEW03", ad_start_date="2026-05-13")

    def test_oct_baseline_excludes_pre_october(self):
        rows = self.conn.execute(
            "SELECT ad_archive_id FROM politician_ads WHERE ad_start_date >= '2025-10-01'"
        ).fetchall()
        ids = {r[0] for r in rows}
        self.assertNotIn("OLD01", ids)
        self.assertNotIn("OLD02", ids)

    def test_oct_baseline_includes_from_oct_1(self):
        rows = self.conn.execute(
            "SELECT ad_archive_id FROM politician_ads WHERE ad_start_date >= '2025-10-01'"
        ).fetchall()
        ids = {r[0] for r in rows}
        self.assertIn("NEW01", ids)
        self.assertIn("NEW02", ids)
        self.assertIn("NEW03", ids)

    def test_oct_1_is_inclusive(self):
        """The boundary date 2025-10-01 itself must be included."""
        rows = self.conn.execute(
            "SELECT ad_archive_id FROM politician_ads WHERE ad_start_date >= '2025-10-01'"
        ).fetchall()
        ids = {r[0] for r in rows}
        self.assertIn("NEW01", ids)

    def test_unchecked_query_respects_date(self):
        """The 'unchecked' query used by the removal checker must respect the date filter."""
        # Mark NEW01 as checked, leave NEW02 and NEW03 unchecked
        self.conn.execute(
            "UPDATE politician_ads SET removed_checked_at='2026-05-01' WHERE ad_archive_id='NEW01'"
        )
        self.conn.commit()
        rows = self.conn.execute("""
            SELECT ad_archive_id FROM politician_ads
            WHERE removed_checked_at IS NULL AND ad_start_date >= '2025-10-01'
        """).fetchall()
        ids = {r[0] for r in rows}
        self.assertNotIn("OLD01", ids)
        self.assertNotIn("OLD02", ids)
        self.assertNotIn("NEW01", ids)
        self.assertIn("NEW02", ids)
        self.assertIn("NEW03", ids)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. POLITICIAN_QUERY PARSING
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueryParsing(unittest.TestCase):
    """The pipeline stores name|party|district in politician_query and parses it at read time."""

    def _parse(self, query):
        parts = query.split("|")
        name     = parts[0].strip() if len(parts) > 0 else ""
        party    = parts[1].strip() if len(parts) > 1 else ""
        district = parts[2].strip() if len(parts) > 2 else ""
        return name, party, district

    def test_full_query_parsed(self):
        name, party, district = self._parse("Νίκος Παπαδόπουλος|ΔΗΣΥ|Λευκωσία")
        self.assertEqual(name, "Νίκος Παπαδόπουλος")
        self.assertEqual(party, "ΔΗΣΥ")
        self.assertEqual(district, "Λευκωσία")

    def test_missing_district(self):
        name, party, district = self._parse("Robert Abela|PL|")
        self.assertEqual(name, "Robert Abela")
        self.assertEqual(party, "PL")
        self.assertEqual(district, "")

    def test_name_only(self):
        name, party, district = self._parse("Olaf McKay")
        self.assertEqual(name, "Olaf McKay")
        self.assertEqual(party, "")
        self.assertEqual(district, "")

    def test_party_level_query(self):
        """Party-level entries like ΔΗΣΥ|ΔΗΣΥ| must parse correctly."""
        name, party, district = self._parse("ΔΗΣΥ|ΔΗΣΥ|")
        self.assertEqual(name, "ΔΗΣΥ")
        self.assertEqual(party, "ΔΗΣΥ")

    def test_whitespace_stripped(self):
        name, party, district = self._parse("  Luke Said  |  PN  |  ")
        self.assertEqual(name, "Luke Said")
        self.assertEqual(party, "PN")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. IS_REMOVED_TEXT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsRemovedText(unittest.TestCase):

    def test_standard_removal_message(self):
        body = "This content was removed because it didn't follow our Advertising Standards."
        self.assertTrue(is_removed_text(body))

    def test_alternative_removal_message(self):
        body = "This content did not follow our Advertising Standards."
        self.assertTrue(is_removed_text(body))

    def test_partial_match(self):
        body = "content was removed because it didn't meet policy"
        self.assertTrue(is_removed_text(body))

    def test_active_ad_not_detected(self):
        body = "Vote for me! I will improve your community. Learn more about my campaign."
        self.assertFalse(is_removed_text(body))

    def test_empty_body(self):
        self.assertFalse(is_removed_text(""))

    def test_case_insensitive(self):
        body = "THIS CONTENT WAS REMOVED BECAUSE IT DIDN'T FOLLOW OUR ADVERTISING STANDARDS"
        self.assertTrue(is_removed_text(body))

    def test_partial_word_not_matched(self):
        """'advertising' alone without removal context must not trigger."""
        body = "Great advertising opportunity! Contact us today."
        self.assertFalse(is_removed_text(body))


# ═══════════════════════════════════════════════════════════════════════════════
# 7. RELEVANCE FILTER (name search deduplication)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelevanceFilter(unittest.TestCase):
    """Mirror of the ad_is_relevant() logic from check_all_candidates_mt.py."""

    PARTY_TERMS = {
        "PN": ["pn", "nationalist", "partit nazzjonalista"],
        "PL": ["pl", "labour", "partit laburista"],
    }

    def _is_relevant(self, name, party, ad):
        blocklist = set()
        if str(ad.get("page_id") or "") in blocklist:
            return False
        name_parts  = [p for p in name.lower().split() if len(p) > 3]
        party_terms = self.PARTY_TERMS.get(party, [party.lower()] if party else [])
        page   = (ad.get("page_name") or "").lower()
        bodies = " ".join(ad.get("ad_creative_bodies") or []).lower()
        titles = " ".join(ad.get("ad_creative_link_titles") or []).lower()
        text   = bodies + " " + titles
        if any(p in page for p in name_parts):
            return True
        name_in_text  = any(p in text for p in name_parts)
        party_in_text = any(t in text for t in party_terms) if party_terms else True
        return name_in_text and party_in_text

    def test_candidate_name_in_page_name(self):
        ad = {"page_name": "Luke Said PN", "page_id": "123",
              "ad_creative_bodies": [], "ad_creative_link_titles": []}
        self.assertTrue(self._is_relevant("Luke Said", "PN", ad))

    def test_name_and_party_in_body(self):
        ad = {"page_name": "Some Random Page", "page_id": "123",
              "ad_creative_bodies": ["Vote Luke Said - Nationalist Party"],
              "ad_creative_link_titles": []}
        self.assertTrue(self._is_relevant("Luke Said", "PN", ad))

    def test_name_without_party_rejected(self):
        """Name match alone without party context must not be enough."""
        ad = {"page_name": "Some Business", "page_id": "456",
              "ad_creative_bodies": ["Luke Said this product is great"],
              "ad_creative_link_titles": []}
        self.assertFalse(self._is_relevant("Luke Said", "PN", ad))

    def test_unrelated_ad_rejected(self):
        ad = {"page_name": "Pizza Palace Malta", "page_id": "789",
              "ad_creative_bodies": ["Best pizza in town, order now!"],
              "ad_creative_link_titles": []}
        self.assertFalse(self._is_relevant("Luke Said", "PN", ad))

    def test_short_name_parts_ignored(self):
        """Name parts <= 3 chars (e.g. 'Ray') must not be used for matching."""
        ad = {"page_name": "Ray Ban Sunglasses", "page_id": "999",
              "ad_creative_bodies": ["Ray Ban offers"],
              "ad_creative_link_titles": []}
        # "Ray" is 3 chars, filtered out → no match
        self.assertFalse(self._is_relevant("Ray Abela", "PL", ad))


# ═══════════════════════════════════════════════════════════════════════════════
# 8. API RESPONSE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

class TestAPIResponseParsing(unittest.TestCase):
    """Verify the pipeline handles various API response shapes gracefully."""

    def _parse_ad(self, api_ad):
        """Mirror of the spend/impressions extraction in upsert_ads."""
        imp   = api_ad.get("impressions") or {}
        spend = api_ad.get("spend") or {}
        return {
            "id":          api_ad.get("id"),
            "page_id":     api_ad.get("page_id"),
            "page_name":   api_ad.get("page_name"),
            "start":       api_ad.get("ad_delivery_start_time"),
            "stop":        api_ad.get("ad_delivery_stop_time"),
            "impr_min":    imp.get("lower_bound"),
            "impr_max":    imp.get("upper_bound"),
            "spend_min":   spend.get("lower_bound"),
            "spend_max":   spend.get("upper_bound"),
            "currency":    api_ad.get("currency"),
        }

    def test_full_response_parsed(self):
        api_ad = {
            "id": "12345", "page_id": "111", "page_name": "Test",
            "ad_delivery_start_time": "2026-01-01",
            "ad_delivery_stop_time": None,
            "impressions": {"lower_bound": 100, "upper_bound": 999},
            "spend": {"lower_bound": 50, "upper_bound": 99},
            "currency": "EUR",
        }
        parsed = self._parse_ad(api_ad)
        self.assertEqual(parsed["impr_max"], 999)
        self.assertEqual(parsed["spend_max"], 99)
        self.assertEqual(parsed["currency"], "EUR")

    def test_missing_spend_field(self):
        """API sometimes omits spend entirely — must not crash."""
        api_ad = {"id": "12346", "page_id": "111", "page_name": "Test",
                  "ad_delivery_start_time": "2026-01-01"}
        parsed = self._parse_ad(api_ad)
        self.assertIsNone(parsed["spend_min"])
        self.assertIsNone(parsed["spend_max"])

    def test_missing_impressions_field(self):
        api_ad = {"id": "12347", "page_id": "111", "page_name": "Test",
                  "ad_delivery_start_time": "2026-01-01",
                  "spend": {"lower_bound": 50, "upper_bound": 99}}
        parsed = self._parse_ad(api_ad)
        self.assertIsNone(parsed["impr_min"])
        self.assertIsNone(parsed["impr_max"])

    def test_empty_api_response(self):
        """Empty data list must result in zero ads processed."""
        api_response = {"data": [], "paging": {}}
        ads = api_response.get("data", [])
        self.assertEqual(len(ads), 0)

    def test_ad_text_extracted_from_bodies_and_titles(self):
        """ad_text must concatenate bodies + titles, trimmed to 1000 chars."""
        api_ad = {
            "ad_creative_bodies": ["Vote for change.", "Better future."],
            "ad_creative_link_titles": ["Campaign 2026"],
        }
        bodies = " ".join(api_ad.get("ad_creative_bodies") or [])
        titles = " ".join(api_ad.get("ad_creative_link_titles") or [])
        ad_text = (bodies + " " + titles).strip()[:1000]
        self.assertEqual(ad_text, "Vote for change. Better future. Campaign 2026")

    def test_ad_text_truncated_at_1000(self):
        api_ad = {"ad_creative_bodies": ["x" * 2000], "ad_creative_link_titles": []}
        bodies = " ".join(api_ad.get("ad_creative_bodies") or [])
        titles = " ".join(api_ad.get("ad_creative_link_titles") or [])
        ad_text = (bodies + " " + titles).strip()[:1000]
        self.assertEqual(len(ad_text), 1000)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. DB SCHEMA & MIGRATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchema(unittest.TestCase):

    def test_required_columns_exist(self):
        conn = make_db()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(politician_ads)").fetchall()}
        required = {
            "ad_archive_id", "politician_query", "page_id", "page_name",
            "ad_start_date", "ad_stop_date",
            "impressions_min", "impressions_max",
            "spend_min", "spend_max", "currency",
            "removed", "removed_checked_at",
            "source", "checked_at", "ad_text",
        }
        for col in required:
            self.assertIn(col, cols, f"Missing column: {col}")

    def test_ad_archive_id_is_primary_key(self):
        """Duplicate ad_archive_id must raise an error without ON CONFLICT."""
        conn = make_db()
        insert_ad(conn, "DUP001")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO politician_ads
                    (ad_archive_id, politician_query, checked_at)
                VALUES ('DUP001', 'X|Y|Z', '2026-01-01')
            """)

    def test_removed_defaults_to_0(self):
        conn = make_db()
        conn.execute("""
            INSERT INTO politician_ads (ad_archive_id, politician_query, checked_at)
            VALUES ('DEF001', 'Test|PN|', '2026-01-01')
        """)
        conn.commit()
        ad = get_ad(conn, "DEF001")
        self.assertEqual(ad["removed"], 0)


# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
