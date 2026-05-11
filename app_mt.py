"""
Malta Political Ads Monitor — Streamlit dashboard.

Run with:
    streamlit run app_mt.py
"""
import os, sqlite3, json
import streamlit as st
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "politician_ads_mt.db")
BL_FILE = os.path.join(BASE, "page_blocklist_mt.json")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Malta Political Ads Monitor",
    page_icon="🗳️",
    layout="wide",
)

# ── Cached data loaders ───────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_blocklist():
    if os.path.exists(BL_FILE):
        with open(BL_FILE, encoding='utf-8') as f:
            return set(json.load(f).get('pages', {}).keys())
    return set()

@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM politician_ads", conn)
    conn.close()

    blocklist = load_blocklist()

    # Apply blocklist
    df = df[~df['page_id'].astype(str).isin(blocklist)].copy()

    # Derived columns
    df['candidate'] = df['politician_query'].str.split('|').str[0].str.strip()
    df['party']     = df['politician_query'].str.split('|').str[1].str.strip()
    df['district']  = df['politician_query'].str.split('|').str[2].str.strip() \
                        .replace('', None)

    # Removed flag
    if 'removed' not in df.columns:
        df['removed'] = None
    df['removed_label'] = df['removed'].apply(
        lambda x: 'YES' if x == 1 else ('no' if x == 0 else '—')
    )

    # Ad URL
    df['ad_url'] = df['ad_archive_id'].apply(
        lambda x: f"https://www.facebook.com/ads/library/?id={x}" if x else ""
    )

    # Ad Library page URL
    df['page_lib_url'] = df['page_id'].apply(
        lambda x: (
            f"https://www.facebook.com/ads/library/?active_status=all"
            f"&ad_type=all&country=MT&media_type=all&view_all_page_id={x}"
        ) if x else ""
    )

    # Dates
    df['ad_start_date'] = pd.to_datetime(df['ad_start_date'], errors='coerce')
    df['ad_stop_date']  = pd.to_datetime(df['ad_stop_date'],  errors='coerce')

    return df


# ── App title ─────────────────────────────────────────────────────────────────
st.title("🗳️ Malta Political Ads Monitor")
st.caption("Malta 2026 General Election — Data from Meta Ad Library")

df_all = load_data()

if df_all.empty:
    st.error(f"Database not found: {DB_PATH}")
    st.stop()

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 Filters")

    parties = sorted(df_all['party'].dropna().unique().tolist())
    sel_parties = st.multiselect("Party", parties, default=[])

    candidates = sorted(df_all['candidate'].dropna().unique().tolist())
    sel_candidates = st.multiselect("Candidate", candidates, default=[])

    active_only = st.checkbox("Active ads only", value=False)

    removed_filter = st.radio(
        "Removed by Meta",
        ["All", "Removed only", "Exclude removed"],
        index=0,
    )

    date_range = st.date_input(
        "Ad start date range",
        value=[],
        help="Filter ads that started within this date range",
    )

    search = st.text_input("Search (candidate / page name)", "")

    st.divider()
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

# ── Apply filters ─────────────────────────────────────────────────────────────
df = df_all.copy()

if sel_parties:
    df = df[df['party'].isin(sel_parties)]
if sel_candidates:
    df = df[df['candidate'].isin(sel_candidates)]
if active_only:
    df = df[df['ad_stop_date'].isna()]
if removed_filter == "Removed only":
    df = df[df['removed'] == 1]
elif removed_filter == "Exclude removed":
    df = df[df['removed'] != 1]
if len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    df = df[df['ad_start_date'].between(start, end, inclusive='both')]
if search:
    s = search.lower()
    df = df[
        df['candidate'].str.lower().str.contains(s, na=False) |
        df['page_name'].str.lower().str.contains(s, na=False)
    ]

# ── Top metrics ───────────────────────────────────────────────────────────────
col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
col1.metric("Total Ads",    f"{len(df):,}")
col2.metric("Candidates",   f"{df['candidate'].nunique():,}")
col3.metric("Unique Pages", f"{df['page_id'].nunique():,}")
col4.metric("Active",       f"{int(df['ad_stop_date'].isna().sum() - (df['removed']==1).sum()):,}")

inactive_n = int((df['ad_stop_date'].notna() & (df['removed'] != 1)).sum())
col5.metric("Inactive", f"{inactive_n:,}")

removed_n = int((df['removed'] == 1).sum())
checked_n = int(df['removed'].notna().sum())
col6.metric(
    "Removed by Meta",
    f"{removed_n:,}",
    help=f"Checked: {checked_n:,} / {len(df):,}" if checked_n else "Run check_removed_ads_mt.py",
)

total_spend_max = df['spend_max'].sum()
col7.metric(
    "Est. Spend Max",
    f"€{int(total_spend_max):,}" if total_spend_max > 0 else "—",
    help="Sum of spend_max across all filtered ads (EUR)",
)

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_overview, tab_leaderboard, tab_timeline, tab_pages, tab_active, tab_inactive, tab_removed, tab_table = st.tabs([
    "📊 Overview", "💰 Leaderboard", "📅 Timeline", "📄 Pages",
    "✅ Active", "⏹ Inactive", "⚠️ Removed by Meta", "📋 All Ads"
])

# ═══════════════════════════════════════════════════════════
# TAB 1 — Overview
# ═══════════════════════════════════════════════════════════
with tab_overview:
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Ads by Party")
        party_counts = df['party'].value_counts().reset_index()
        party_counts.columns = ['Party', 'Ads']
        st.bar_chart(party_counts.set_index('Party'))

    with c2:
        st.subheader("Active vs Stopped")
        status = pd.DataFrame({
            'Status': ['Active', 'Stopped'],
            'Count': [
                int(df['ad_stop_date'].isna().sum()),
                int(df['ad_stop_date'].notna().sum()),
            ]
        })
        st.bar_chart(status.set_index('Status'))

    st.divider()
    st.subheader("Candidate Summary")
    cand_summary = (
        df.groupby(['candidate', 'party'])
        .agg(
            Ads       =('ad_archive_id', 'count'),
            Active    =('ad_stop_date',  lambda x: x.isna().sum()),
            Impr_Max  =('impressions_max','sum'),
            Spend_Max =('spend_max',      'sum'),
            Removed   =('removed',        lambda x: (x == 1).sum()),
            Pages     =('page_id',        'nunique'),
        )
        .reset_index()
        .rename(columns={'candidate': 'Candidate', 'party': 'Party'})
        .sort_values('Ads', ascending=False)
    )
    cand_summary['Spend_Max'] = cand_summary['Spend_Max'].apply(
        lambda x: f"€{int(x):,}" if x > 0 else "—"
    )
    cand_summary['Impr_Max'] = cand_summary['Impr_Max'].apply(
        lambda x: f"{int(x):,}" if x > 0 else "—"
    )
    st.dataframe(cand_summary, use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════
# TAB 2 — Spending Leaderboard
# ═══════════════════════════════════════════════════════════
with tab_leaderboard:
    st.subheader("💰 Ad Spend Ranking")
    st.caption(
        "Estimated cumulative spend per candidate (EUR). "
        "Meta provides ranges — chart shows spend max (upper bound)."
    )

    spend = (
        df.groupby(['candidate', 'party'])
        .agg(
            Spend_Min  =('spend_min',      'sum'),
            Spend_Max  =('spend_max',      'sum'),
            Ads        =('ad_archive_id',  'count'),
            Impr_Max   =('impressions_max','sum'),
        )
        .reset_index()
        .rename(columns={'candidate': 'Candidate', 'party': 'Party'})
        .sort_values('Spend_Max', ascending=False)
    )
    spend = spend[spend['Spend_Max'] > 0]

    if spend.empty:
        st.info("No spend data available yet.")
    else:
        top_n = min(20, len(spend))
        st.bar_chart(spend.head(top_n).set_index('Candidate')[['Spend_Min', 'Spend_Max']])

        st.subheader("Full table")
        spend_display = spend.copy()
        spend_display['Spend Range (EUR)'] = spend_display.apply(
            lambda r: f"€{int(r.Spend_Min):,} – €{int(r.Spend_Max):,}", axis=1
        )
        spend_display['Impressions (max)'] = spend_display['Impr_Max'].apply(
            lambda x: f"{int(x):,}" if x > 0 else "—"
        )
        st.dataframe(
            spend_display[['Candidate', 'Party', 'Spend Range (EUR)', 'Ads', 'Impressions (max)']],
            use_container_width=True,
            hide_index=True,
        )

# ═══════════════════════════════════════════════════════════
# TAB 3 — Timeline
# ═══════════════════════════════════════════════════════════
with tab_timeline:
    st.subheader("📅 Ad activity by month")
    st.caption("Number of ads started each month.")

    df_time = df.dropna(subset=['ad_start_date']).copy()

    if df_time.empty:
        st.info("No date data available.")
    else:
        df_time['month'] = df_time['ad_start_date'].dt.to_period('M').astype(str)

        st.subheader("Total monthly volume")
        monthly = df_time.groupby('month').size().reset_index(name='Ads')
        st.bar_chart(monthly.sort_values('month').set_index('month'))

        st.divider()

        st.subheader("By Party")
        party_monthly = (
            df_time.groupby(['month', 'party'])
            .size()
            .reset_index(name='Ads')
            .pivot(index='month', columns='party', values='Ads')
            .fillna(0)
            .sort_index()
        )
        st.bar_chart(party_monthly)

        st.divider()

        st.subheader("Top 10 candidates over time")
        top10 = df_time['candidate'].value_counts().head(10).index.tolist()
        df_top = df_time[df_time['candidate'].isin(top10)]
        cand_monthly = (
            df_top.groupby(['month', 'candidate'])
            .size()
            .reset_index(name='Ads')
            .pivot(index='month', columns='candidate', values='Ads')
            .fillna(0)
            .sort_index()
        )
        st.line_chart(cand_monthly)

# ═══════════════════════════════════════════════════════════
# TAB 4 — Pages
# ═══════════════════════════════════════════════════════════
with tab_pages:
    st.subheader("📄 Pages running ads")
    st.caption("Each unique Facebook page found running ads for a candidate.")

    pages_summary = (
        df.groupby(['page_name', 'page_id', 'candidate', 'party'])
        .agg(
            Ads      =('ad_archive_id',  'count'),
            Active   =('ad_stop_date',   lambda x: x.isna().sum()),
            Impr_Max =('impressions_max','sum'),
            Spend_Max=('spend_max',      'sum'),
            Removed  =('removed',        lambda x: (x == 1).sum()),
        )
        .reset_index()
        .rename(columns={
            'page_name': 'Page Name',
            'page_id':   'Page ID',
            'candidate': 'Candidate',
            'party':     'Party',
        })
        .sort_values('Ads', ascending=False)
    )

    # Add Ad Library link
    pages_summary['Ad Library'] = pages_summary['Page ID'].apply(
        lambda pid: (
            f"https://www.facebook.com/ads/library/?active_status=all"
            f"&ad_type=all&country=MT&media_type=all&view_all_page_id={pid}"
        )
    )

    # Is the page the candidate's own?
    pages_summary['Own Page?'] = pages_summary.apply(
        lambda r: '✅' if (
            r['Candidate'].lower() in (r['Page Name'] or '').lower() or
            (r['Page Name'] or '').lower() in r['Candidate'].lower()
        ) else '🔗',
        axis=1
    )

    st.dataframe(
        pages_summary[['Candidate', 'Party', 'Page Name', 'Own Page?',
                        'Ads', 'Active', 'Impr_Max', 'Spend_Max', 'Removed', 'Ad Library']],
        use_container_width=True,
        height=560,
        column_config={
            "Ad Library": st.column_config.LinkColumn("Ad Library", display_text="View"),
        },
        hide_index=True,
    )

# ═══════════════════════════════════════════════════════════
# TAB 5 — Active Ads
# ═══════════════════════════════════════════════════════════
with tab_active:
    df_active = df[df['ad_stop_date'].isna() & (df['removed'] != 1)].copy()
    st.subheader(f"✅ Active Ads ({len(df_active):,})")
    st.caption("Ads currently running — no stop date, not removed by Meta.")

    active_summary = (
        df_active.groupby(['candidate', 'party'])
        .agg(
            Ads      =('ad_archive_id',  'count'),
            Impr_Max =('impressions_max','sum'),
            Spend_Max=('spend_max',      'sum'),
            Pages    =('page_id',        'nunique'),
        )
        .reset_index()
        .rename(columns={'candidate':'Candidate','party':'Party'})
        .sort_values('Ads', ascending=False)
    )
    st.dataframe(active_summary, use_container_width=True, hide_index=True)

    st.divider()
    df_act_disp = df_active[['candidate','party','page_name','ad_start_date','impressions_max','spend_max','ad_url']].rename(columns={
        'candidate':'Candidate','party':'Party','page_name':'Page',
        'ad_start_date':'Started','impressions_max':'Impr Max','spend_max':'Spend Max','ad_url':'Ad'
    })
    st.dataframe(df_act_disp, use_container_width=True, height=400,
        column_config={
            "Ad":      st.column_config.LinkColumn("Ad", display_text="View"),
            "Started": st.column_config.DateColumn("Started", format="DD/MM/YYYY"),
        }, hide_index=True)

# ═══════════════════════════════════════════════════════════
# TAB 6 — Inactive Ads
# ═══════════════════════════════════════════════════════════
with tab_inactive:
    df_inactive = df[df['ad_stop_date'].notna() & (df['removed'] != 1)].copy()
    st.subheader(f"⏹ Inactive Ads ({len(df_inactive):,})")
    st.caption("Ads that stopped running naturally — not removed by Meta.")

    inactive_summary = (
        df_inactive.groupby(['candidate', 'party'])
        .agg(
            Ads      =('ad_archive_id',  'count'),
            Impr_Max =('impressions_max','sum'),
            Spend_Max=('spend_max',      'sum'),
        )
        .reset_index()
        .rename(columns={'candidate':'Candidate','party':'Party'})
        .sort_values('Ads', ascending=False)
    )
    st.dataframe(inactive_summary, use_container_width=True, hide_index=True)

    st.divider()
    df_inact_disp = df_inactive[['candidate','party','page_name','ad_start_date','ad_stop_date','impressions_max','spend_max','ad_url']].rename(columns={
        'candidate':'Candidate','party':'Party','page_name':'Page',
        'ad_start_date':'Start','ad_stop_date':'Stop','impressions_max':'Impr Max','spend_max':'Spend Max','ad_url':'Ad'
    })
    st.dataframe(df_inact_disp, use_container_width=True, height=400,
        column_config={
            "Ad":   st.column_config.LinkColumn("Ad", display_text="View"),
            "Start":st.column_config.DateColumn("Start", format="DD/MM/YYYY"),
            "Stop": st.column_config.DateColumn("Stop",  format="DD/MM/YYYY"),
        }, hide_index=True)

# ═══════════════════════════════════════════════════════════
# TAB 7 — Removed by Meta
# ═══════════════════════════════════════════════════════════
with tab_removed:
    df_removed = df[df['removed'] == 1].copy()
    st.subheader(f"⚠️ Removed by Meta ({len(df_removed):,})")
    st.caption("Ads taken down for violating Meta's Advertising Standards.")

    removed_summary = (
        df_removed.groupby(['candidate', 'party'])
        .agg(
            Removed  =('ad_archive_id',  'count'),
            Impr_Max =('impressions_max','sum'),
            Spend_Max=('spend_max',      'sum'),
        )
        .reset_index()
        .rename(columns={'candidate':'Candidate','party':'Party'})
        .sort_values('Removed', ascending=False)
    )
    st.dataframe(removed_summary, use_container_width=True, hide_index=True)

    st.divider()
    df_rem_disp = df_removed[['candidate','party','page_name','ad_start_date','ad_stop_date','impressions_max','spend_max','ad_url']].rename(columns={
        'candidate':'Candidate','party':'Party','page_name':'Page',
        'ad_start_date':'Start','ad_stop_date':'Stop','impressions_max':'Impr Max','spend_max':'Spend Max','ad_url':'Ad'
    })
    st.dataframe(df_rem_disp, use_container_width=True, height=400,
        column_config={
            "Ad":   st.column_config.LinkColumn("Ad", display_text="View"),
            "Start":st.column_config.DateColumn("Start", format="DD/MM/YYYY"),
            "Stop": st.column_config.DateColumn("Stop",  format="DD/MM/YYYY"),
        }, hide_index=True)

# ═══════════════════════════════════════════════════════════
# TAB 8 — All Ads Table
# ═══════════════════════════════════════════════════════════
with tab_table:
    st.subheader(f"All Ads ({len(df):,})")

    display_cols = [
        'candidate', 'party',
        'page_name', 'removed_label',
        'ad_start_date', 'ad_stop_date',
        'impressions_min', 'impressions_max',
        'spend_min', 'spend_max', 'currency',
        'ad_url',
    ]
    col_labels = {
        'candidate':      'Candidate',
        'party':          'Party',
        'page_name':      'Page',
        'removed_label':  'Removed',
        'ad_start_date':  'Start',
        'ad_stop_date':   'Stop',
        'impressions_min':'Impr Min',
        'impressions_max':'Impr Max',
        'spend_min':      'Spend Min',
        'spend_max':      'Spend Max',
        'currency':       'Currency',
        'ad_url':         'Ad',
    }
    df_display = df[display_cols].rename(columns=col_labels)

    st.dataframe(
        df_display,
        use_container_width=True,
        height=560,
        column_config={
            "Ad":    st.column_config.LinkColumn("Ad", display_text="View"),
            "Start": st.column_config.DateColumn("Start", format="DD/MM/YYYY"),
            "Stop":  st.column_config.DateColumn("Stop",  format="DD/MM/YYYY"),
        },
        hide_index=True,
    )

    @st.cache_data
    def to_excel_bytes(dataframe):
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False, sheet_name='Ads')
        return buf.getvalue()

    st.download_button(
        label="⬇️ Download as Excel",
        data=to_excel_bytes(df_display),
        file_name="malta_political_ads_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
