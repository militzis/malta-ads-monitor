"""
Cyprus Political Ads Monitor — Streamlit dashboard.

Run with:
    streamlit run app.py
"""
import os, sqlite3
import streamlit as st
import pandas as pd

from utils import (
    flag_page, is_business, is_excluded,
    load_exclusions, load_page_categories, is_non_political_by_category,
    PARTY_PAGE_LABEL,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "politician_ads.db")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Κυπριακές Πολιτικές Διαφημίσεις",
    page_icon="🗳️",
    layout="wide",
)

# ── Cached data loaders ───────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def _load_exclusions():
    return load_exclusions()

@st.cache_data(ttl=300)
def _load_page_categories():
    return load_page_categories()

# ── Load & filter data ────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM politician_ads", conn)
    conn.close()

    excl_ids, excl_names = _load_exclusions()
    page_cats            = _load_page_categories()

    # Apply same filters as make_combined_excel.py
    mask = ~(
        df.apply(lambda r: is_excluded(r.get('page_id'), r.get('page_name'), excl_ids, excl_names), axis=1) |
        df['page_name'].apply(lambda x: is_business(x)) |
        df['page_id'].apply(lambda x: is_non_political_by_category(x, page_cats))
    )
    df = df[mask].copy()

    # Derived columns
    df['candidate'] = df['politician_query'].str.split('|').str[0]
    df['source']    = df['source'].fillna('greek').str.capitalize()
    df['flag']      = df.apply(lambda r: flag_page(r.get('page_name'), r.get('politician_query')), axis=1)
    df['page_name'] = df.apply(
        lambda r: PARTY_PAGE_LABEL if r['flag'] == 'PARTY_PAGE' else r['page_name'], axis=1
    )
    # Removed flag: 1 = confirmed removed by Meta, 0 = confirmed active, NaN = not yet checked
    if 'removed' not in df.columns:
        df['removed'] = None
    df['removed_label'] = df['removed'].apply(
        lambda x: 'YES' if x == 1 else ('no' if x == 0 else '—')
    )

    # Ad URL
    df['ad_url'] = df['ad_archive_id'].apply(
        lambda x: f"https://www.facebook.com/ads/library/?id={x}" if x else ""
    )

    # Dates as proper types for sorting
    df['ad_start_date'] = pd.to_datetime(df['ad_start_date'], errors='coerce')
    df['ad_stop_date']  = pd.to_datetime(df['ad_stop_date'],  errors='coerce')

    return df


# ── App ───────────────────────────────────────────────────────────────────────

st.title("🗳️ Κυπριακές Πολιτικές Διαφημίσεις")
st.caption("Βουλευτικές Εκλογές 2026 — Δεδομένα από Meta Ad Library & Google Ads")

df_all = load_data()

if df_all.empty:
    st.error(f"Δεν βρέθηκε βάση δεδομένων: {DB_PATH}")
    st.stop()

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 Φίλτρα")

    # Party
    parties = sorted(df_all['party'].dropna().unique().tolist())
    sel_parties = st.multiselect("Κόμμα", parties, default=[])

    # District
    districts = sorted(df_all['district'].dropna().unique().tolist())
    sel_districts = st.multiselect("Επαρχία", districts, default=[])

    # Flag
    flags = sorted(df_all['flag'].dropna().unique().tolist())
    sel_flags = st.multiselect("Flag", flags, default=[])

    # Source
    sources = sorted(df_all['source'].dropna().unique().tolist())
    sel_sources = st.multiselect("Πηγή αναζήτησης", sources, default=[])

    # Active only
    active_only = st.checkbox("Μόνο ενεργές διαφημίσεις", value=False)

    # Removed filter
    removed_filter = st.radio(
        "Αφαιρεμένες από Meta",
        ["Όλες", "Μόνο αφαιρεμένες", "Χωρίς αφαιρεμένες"],
        index=0,
    )

    # Free text search
    search = st.text_input("Αναζήτηση (υποψήφιος / σελίδα)", "")

    st.divider()
    if st.button("🔄 Ανανέωση δεδομένων"):
        st.cache_data.clear()
        st.rerun()

# ── Apply sidebar filters ─────────────────────────────────────────────────────
df = df_all.copy()

if sel_parties:
    df = df[df['party'].isin(sel_parties)]
if sel_districts:
    df = df[df['district'].isin(sel_districts)]
if sel_flags:
    df = df[df['flag'].isin(sel_flags)]
if sel_sources:
    df = df[df['source'].isin(sel_sources)]
if active_only:
    df = df[df['ad_stop_date'].isna()]
if removed_filter == "Μόνο αφαιρεμένες":
    df = df[df['removed'] == 1]
elif removed_filter == "Χωρίς αφαιρεμένες":
    df = df[df['removed'] != 1]
if search:
    s = search.lower()
    df = df[
        df['candidate'].str.lower().str.contains(s, na=False) |
        df['page_name'].str.lower().str.contains(s, na=False)
    ]

# ── Top metrics ───────────────────────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Σύνολο διαφημίσεων", f"{len(df):,}")
col2.metric("Μοναδικές σελίδες",  f"{df['page_id'].nunique():,}")
col3.metric("Ενεργές",            f"{df['ad_stop_date'].isna().sum():,}")
col4.metric("Υποψήφιοι",          f"{df['candidate'].nunique():,}")
removed_n = int((df['removed'] == 1).sum())
checked_n = int(df['removed'].notna().sum())
col5.metric(
    "Αφαιρεμένες",
    f"{removed_n:,}",
    help=f"Ελεγμένες: {checked_n:,} / {len(df):,}" if checked_n else "Τρέξε check_removed_ads.py",
)

st.divider()

# ── Google Ads loader ─────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_google_ads():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        gdf = pd.read_sql_query("SELECT * FROM google_ads", conn)
    except Exception:
        gdf = pd.DataFrame()
    finally:
        conn.close()
    if gdf.empty:
        return gdf
    gdf['first_shown'] = pd.to_datetime(gdf['first_shown'], errors='coerce')
    gdf['last_shown']  = pd.to_datetime(gdf['last_shown'],  errors='coerce')
    return gdf

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_overview, tab_leaderboard, tab_timeline, tab_table, tab_google = st.tabs([
    "📊 Overview", "💰 Leaderboard", "📅 Timeline", "📋 Διαφημίσεις", "🔍 Google Ads"
])

# ═══════════════════════════════════════════════════════════
# TAB 1 — Overview
# ═══════════════════════════════════════════════════════════
with tab_overview:
    st.subheader("Διαφημίσεις ανά κόμμα")
    party_counts = df['party'].value_counts().reset_index()
    party_counts.columns = ['Κόμμα', 'Διαφημίσεις']
    st.bar_chart(party_counts.set_index('Κόμμα'))

    st.subheader("Ανάλυση ανά υποψήφιο")
    cand_summary = (
        df.groupby(['candidate', 'party', 'district'])
        .agg(
            Διαφημίσεις=('ad_archive_id', 'count'),
            Ενεργές=('ad_stop_date', lambda x: x.isna().sum()),
            Impr_Max=('impressions_max', 'max'),
            Spend_Max=('spend_max', 'sum'),
        )
        .reset_index()
        .rename(columns={'candidate': 'Υποψήφιος', 'party': 'Κόμμα', 'district': 'Επαρχία'})
        .sort_values('Διαφημίσεις', ascending=False)
    )
    st.dataframe(cand_summary, use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════
# TAB 2 — Spending Leaderboard
# ═══════════════════════════════════════════════════════════
with tab_leaderboard:
    st.subheader("💰 Κατάταξη δαπάνης διαφήμισης")
    st.caption("Αθροιστική εκτιμώμενη δαπάνη ανά υποψήφιο (σε EUR). "
               "Το Meta δίνει εύρος τιμών — εδώ φαίνεται το άνω όριο (spend max).")

    spend = (
        df.groupby(['candidate', 'party', 'district'])
        .agg(
            Spend_Min=('spend_min', 'sum'),
            Spend_Max=('spend_max', 'sum'),
            Διαφημίσεις=('ad_archive_id', 'count'),
            Impr_Max=('impressions_max', 'sum'),
        )
        .reset_index()
        .rename(columns={'candidate': 'Υποψήφιος', 'party': 'Κόμμα', 'district': 'Επαρχία'})
        .sort_values('Spend_Max', ascending=False)
    )
    spend = spend[spend['Spend_Max'] > 0]   # hide candidates with no spend data

    # Bar chart — top 20
    top_n = min(20, len(spend))
    chart_data = spend.head(top_n).set_index('Υποψήφιος')[['Spend_Min', 'Spend_Max']]
    st.bar_chart(chart_data)

    # Full table
    st.subheader("Πλήρης πίνακας")
    spend['Εύρος δαπάνης (EUR)'] = spend.apply(
        lambda r: f"€{int(r.Spend_Min):,} – €{int(r.Spend_Max):,}", axis=1
    )
    st.dataframe(
        spend[['Υποψήφιος', 'Κόμμα', 'Επαρχία', 'Εύρος δαπάνης (EUR)',
               'Διαφημίσεις', 'Impr_Max']],
        use_container_width=True,
        hide_index=True,
    )

# ═══════════════════════════════════════════════════════════
# TAB 3 — Timeline
# ═══════════════════════════════════════════════════════════
with tab_timeline:
    st.subheader("📅 Δραστηριότητα διαφημίσεων ανά μήνα")
    st.caption("Αριθμός διαφημίσεων που ξεκίνησαν κάθε μήνα.")

    df_time = df.dropna(subset=['ad_start_date']).copy()

    if df_time.empty:
        st.info("Δεν υπάρχουν δεδομένα ημερομηνίας.")
    else:
        df_time['month'] = df_time['ad_start_date'].dt.to_period('M').astype(str)

        # Overall monthly volume
        monthly = df_time.groupby('month').size().reset_index(name='Διαφημίσεις')
        monthly = monthly.sort_values('month')
        st.subheader("Συνολικός όγκος ανά μήνα")
        st.bar_chart(monthly.set_index('month'))

        st.divider()

        # Per-party monthly breakdown
        st.subheader("Ανά κόμμα")
        party_monthly = (
            df_time.groupby(['month', 'party'])
            .size()
            .reset_index(name='Διαφημίσεις')
            .pivot(index='month', columns='party', values='Διαφημίσεις')
            .fillna(0)
            .sort_index()
        )
        st.bar_chart(party_monthly)

        st.divider()

        # Top candidates over time (filter to top 10 by total)
        st.subheader("Top 10 υποψήφιοι ανά μήνα")
        top10 = df_time['candidate'].value_counts().head(10).index.tolist()
        df_top = df_time[df_time['candidate'].isin(top10)]
        cand_monthly = (
            df_top.groupby(['month', 'candidate'])
            .size()
            .reset_index(name='Διαφημίσεις')
            .pivot(index='month', columns='candidate', values='Διαφημίσεις')
            .fillna(0)
            .sort_index()
        )
        st.line_chart(cand_monthly)

# ═══════════════════════════════════════════════════════════
# TAB 4 — Ads Table
# ═══════════════════════════════════════════════════════════
with tab_table:
    st.subheader(f"Διαφημίσεις ({len(df):,})")

    display_cols = [
        'candidate', 'party', 'district',
        'page_name', 'flag', 'removed_label', 'source',
        'ad_start_date', 'ad_stop_date',
        'impressions_min', 'impressions_max',
        'spend_min', 'spend_max', 'currency',
        'ad_url',
    ]
    col_labels = {
        'candidate':      'Υποψήφιος',
        'party':          'Κόμμα',
        'district':       'Επαρχία',
        'page_name':      'Σελίδα',
        'flag':           'Flag',
        'removed_label':  'Αφαιρέθηκε',
        'source':         'Πηγή',
        'ad_start_date':  'Έναρξη',
        'ad_stop_date':   'Λήξη',
        'impressions_min':'Impr Min',
        'impressions_max':'Impr Max',
        'spend_min':      'Spend Min',
        'spend_max':      'Spend Max',
        'currency':       'Νόμισμα',
        'ad_url':         'Διαφήμιση',
    }
    df_display = df[display_cols].rename(columns=col_labels)

    st.dataframe(
        df_display,
        use_container_width=True,
        height=520,
        column_config={
            "Διαφήμιση": st.column_config.LinkColumn("Διαφήμιση", display_text="Προβολή"),
            "Έναρξη": st.column_config.DateColumn("Έναρξη", format="DD/MM/YYYY"),
            "Λήξη":   st.column_config.DateColumn("Λήξη",   format="DD/MM/YYYY"),
        },
        hide_index=True,
    )

    # Download filtered data
    @st.cache_data
    def to_excel_bytes(dataframe):
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False, sheet_name='Ads')
        return buf.getvalue()

    st.download_button(
        label="⬇️ Λήψη ως Excel",
        data=to_excel_bytes(df_display),
        file_name="political_ads_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ═══════════════════════════════════════════════════════════
# TAB 5 — Google Ads
# ═══════════════════════════════════════════════════════════
with tab_google:
    gdf = load_google_ads()

    if gdf.empty:
        st.info("Δεν υπάρχουν δεδομένα Google Ads. "
                "Τρέξε: `python check_google_ads.py`")
        st.stop()

    st.subheader("🔍 Google Ads — Κυπριακές Πολιτικές Διαφημίσεις")
    st.caption("Δεδομένα από Google Ads Transparency Center (BigQuery). "
               "Μόνο διαφημίσεις που εμφανίστηκαν στην Κύπρο (CY).")

    # ── Google sidebar filters (inside the tab) ───────────────────────────
    gcol_f1, gcol_f2, gcol_f3 = st.columns(3)
    with gcol_f1:
        g_parties = sorted(gdf['matched_party'].dropna().unique().tolist())
        sel_g_parties = st.multiselect("Κόμμα", g_parties, default=[], key="g_party")
    with gcol_f2:
        g_districts = sorted(gdf['matched_district'].dropna().unique().tolist())
        sel_g_districts = st.multiselect("Επαρχία", g_districts, default=[], key="g_district")
    with gcol_f3:
        g_search = st.text_input("Αναζήτηση υποψηφίου", "", key="g_search")

    gdf_f = gdf.copy()
    if sel_g_parties:
        gdf_f = gdf_f[gdf_f['matched_party'].isin(sel_g_parties)]
    if sel_g_districts:
        gdf_f = gdf_f[gdf_f['matched_district'].isin(sel_g_districts)]
    if g_search:
        gdf_f = gdf_f[gdf_f['matched_candidate'].str.lower().str.contains(g_search.lower(), na=False)]

    # ── Top metrics ───────────────────────────────────────────────────────
    gcol1, gcol2, gcol3, gcol4 = st.columns(4)
    gcol1.metric("Διαφημίσεις",       f"{gdf_f['creative_id'].nunique():,}")
    gcol2.metric("Υποψήφιοι",         f"{gdf_f['matched_candidate'].nunique():,}")
    gcol3.metric("Κόμματα",           f"{gdf_f['matched_party'].nunique():,}")
    total_impr = gdf_f['times_shown_upper_bound'].sum()
    gcol4.metric("Impressions (max)", f"{int(total_impr):,}" if total_impr else "—")

    st.divider()

    # ── Summary per candidate ─────────────────────────────────────────────
    st.subheader("Διαφημίσεις ανά υποψήφιο")
    g_summary = (
        gdf_f.groupby(['matched_candidate', 'matched_party', 'matched_district'])
        .agg(
            Διαφημίσεις=('creative_id', 'nunique'),
            Πρώτη=('first_shown', 'min'),
            Τελευταία=('last_shown', 'max'),
            Impressions_Max=('times_shown_upper_bound', 'sum'),
            Topics=('topic', lambda x: ', '.join(sorted(set(v for v in x if v)))),
        )
        .reset_index()
        .rename(columns={
            'matched_candidate': 'Υποψήφιος',
            'matched_party':     'Κόμμα',
            'matched_district':  'Επαρχία',
        })
        .sort_values('Διαφημίσεις', ascending=False)
    )
    st.dataframe(g_summary, use_container_width=True, hide_index=True)

    # ── Bar chart ─────────────────────────────────────────────────────────
    st.subheader("Γράφημα")
    chart_g = g_summary.set_index('Υποψήφιος')[['Διαφημίσεις']]
    st.bar_chart(chart_g)

    st.divider()

    # ── Full ads table with clickable links ───────────────────────────────
    st.subheader(f"Λίστα διαφημίσεων ({len(gdf_f):,} εγγραφές)")

    g_display_cols = [
        'matched_candidate', 'matched_party', 'matched_district',
        'advertiser_disclosed_name', 'advertiser_location',
        'ad_format_type', 'topic',
        'first_shown', 'last_shown',
        'times_shown_lower_bound', 'times_shown_upper_bound',
        'match_type', 'creative_page_url',
    ]
    g_col_labels = {
        'matched_candidate':         'Υποψήφιος',
        'matched_party':             'Κόμμα',
        'matched_district':          'Επαρχία',
        'advertiser_disclosed_name': 'Διαφημιστής',
        'advertiser_location':       'Χώρα',
        'ad_format_type':            'Τύπος',
        'topic':                     'Θέμα',
        'first_shown':               'Πρώτη',
        'last_shown':                'Τελευταία',
        'times_shown_lower_bound':   'Impr Min',
        'times_shown_upper_bound':   'Impr Max',
        'match_type':                'Match',
        'creative_page_url':         'Σύνδεσμος',
    }
    gdf_show = gdf_f[g_display_cols].rename(columns=g_col_labels)

    st.dataframe(
        gdf_show,
        use_container_width=True,
        height=520,
        column_config={
            "Σύνδεσμος": st.column_config.LinkColumn("Σύνδεσμος", display_text="Άνοιγμα"),
            "Πρώτη":     st.column_config.DateColumn("Πρώτη",     format="DD/MM/YYYY"),
            "Τελευταία": st.column_config.DateColumn("Τελευταία", format="DD/MM/YYYY"),
        },
        hide_index=True,
    )

    # Download
    @st.cache_data
    def google_to_excel(dataframe):
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False, sheet_name='Google Ads')
        return buf.getvalue()

    st.download_button(
        label="⬇️ Λήψη ως Excel",
        data=google_to_excel(gdf_show),
        file_name="google_ads_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
