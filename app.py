import pandas as pd
import streamlit as st

import config
import db

st.set_page_config(page_title="Congress + FEC Tracker", layout="wide", menu_items={})

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap');

    html, body, [class*="css"], .stMarkdown, .stDataFrame, input, textarea, button, select {
        font-family: 'Source Serif 4', Georgia, Cambria, 'Times New Roman', serif !important;
    }

    /* display:none (not visibility:hidden) -- a merely-invisible fixed-position
       header still occupies space and intercepts clicks on whatever sits
       beneath it, which was silently swallowing clicks on the sidebar's
       view-switcher radio buttons near the top of the page. */
    #MainMenu, header, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {
        display: none !important;
    }

    .stApp {
        background-color: #F5F0E6;
    }

    section[data-testid="stSidebar"] {
        background-color: #FFFFFF;
        border-right: 1px solid #E4DDCC;
    }

    h1, h2, h3 {
        color: #1F1D1A;
        letter-spacing: 0.01em;
    }

    [data-testid="stMetricValue"] {
        color: #1F1D1A;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_connection():
    conn = db.connect()
    db.init_main_db(conn)
    return conn


def df(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(sql, conn, params=params)


def has_data() -> bool:
    return df("SELECT COUNT(*) AS n FROM bills")["n"].iloc[0] > 0


st.title("Congress + FEC Tracker")
st.caption(
    "Congressional bills through their full lifecycle, paired with FEC itemized donor "
    "retention for House/Senate campaign committees, linked by sponsor and by policy area."
)

if not has_data():
    st.warning(
        "No data yet. Run `python scripts/refresh_all.py` locally (after setting up your "
        "`.env` with API keys) to populate `data/tracker.db`, then reload this page."
    )
    st.stop()

page = st.sidebar.radio(
    "View",
    [
        "Active Bills Overview", "Search Bills", "Browse by Period",
        "Committee Retention", "Sponsor Spotlight", "Search by Donor",
    ],
)

STAGE_ORDER = ["Introduced", "In Committee", "Passed House", "Passed Senate", "To President", "Signed", "Vetoed"]

# ---------------------------------------------------------------- Overview
if page == "Active Bills Overview":
    congress = st.sidebar.selectbox(
        "Congress", options=sorted(df("SELECT DISTINCT congress FROM bills")["congress"], reverse=True),
        index=0,
    )
    bills = df("SELECT * FROM v_active_bills_overview WHERE congress = ?", (int(congress),))

    st.subheader(f"All active bills — {congress}th Congress")
    counts = bills["current_stage"].value_counts()
    cols = st.columns(len(STAGE_ORDER))
    for c, stage in zip(cols, STAGE_ORDER):
        c.metric(stage, int(counts.get(stage, 0)))

    stage_filter = st.multiselect("Filter by stage", STAGE_ORDER, default=STAGE_ORDER)
    shown = bills[bills["current_stage"].isin(stage_filter)].sort_values("latest_action_date", ascending=False)
    st.dataframe(
        shown[["bill_id", "title", "current_stage", "sponsor_name", "policy_area", "latest_action_date"]],
        use_container_width=True, hide_index=True,
    )

# ---------------------------------------------------------------- Search
elif page == "Search Bills":
    keyword = st.text_input("Keyword in title")
    policy_areas = [p for p in df("SELECT DISTINCT policy_area FROM bills")["policy_area"] if pd.notna(p)]
    policy_area = st.selectbox("Policy area", ["(any)"] + sorted(policy_areas))

    sql = "SELECT * FROM v_active_bills_overview WHERE 1=1"
    params: list = []
    if keyword:
        sql += " AND title LIKE ?"
        params.append(f"%{keyword}%")
    if policy_area != "(any)":
        sql += " AND policy_area = ?"
        params.append(policy_area)

    results = df(sql, tuple(params))
    st.write(f"{len(results)} bill(s)")
    st.dataframe(
        results[["bill_id", "title", "current_stage", "sponsor_name", "policy_area", "introduced_date"]],
        use_container_width=True, hide_index=True,
    )

# ---------------------------------------------------------------- Browse by period
elif page == "Browse by Period":
    bills_all = df("SELECT * FROM v_active_bills_overview")
    bills_all["introduced_date"] = pd.to_datetime(bills_all["introduced_date"], errors="coerce")

    granularity = st.radio("Group by", ["Month", "Year", "Congress", "Election cycle"], horizontal=True)
    if granularity == "Congress":
        options = sorted(bills_all["congress"].dropna().unique(), reverse=True)
        pick = st.selectbox("Congress", options)
        shown = bills_all[bills_all["congress"] == pick]
    elif granularity == "Election cycle":
        bills_all["cycle"] = bills_all["introduced_date"].dt.year.apply(
            lambda y: config.cycle_for_year(int(y)) if pd.notna(y) else None
        )
        options = sorted(bills_all["cycle"].dropna().unique(), reverse=True)
        pick = st.selectbox("Election cycle", options)
        shown = bills_all[bills_all["cycle"] == pick]
    elif granularity == "Year":
        options = sorted(bills_all["introduced_date"].dt.year.dropna().unique(), reverse=True)
        pick = st.selectbox("Year", options)
        shown = bills_all[bills_all["introduced_date"].dt.year == pick]
    else:
        years = sorted(bills_all["introduced_date"].dt.year.dropna().unique(), reverse=True)
        year_pick = st.selectbox("Year", years)
        months = list(range(1, 13))
        month_pick = st.selectbox("Month", months, format_func=lambda m: pd.Timestamp(2000, m, 1).strftime("%B"))
        shown = bills_all[
            (bills_all["introduced_date"].dt.year == year_pick)
            & (bills_all["introduced_date"].dt.month == month_pick)
        ]

    st.write(f"{len(shown)} bill(s)")
    st.dataframe(
        shown[["bill_id", "title", "current_stage", "sponsor_name", "policy_area", "introduced_date"]],
        use_container_width=True, hide_index=True,
    )

# ---------------------------------------------------------------- Committee retention
elif page == "Committee Retention":
    st.subheader("Itemized Donor Retention")
    st.caption(
        "Share of a committee's prior-cycle itemized donors (>$200 aggregate per election) who "
        "also gave in the current cycle. Donor identity is a name+ZIP fingerprint, not a stable "
        "person ID — see README for matching caveats. Individual donors are never shown, only "
        "committee-level aggregates."
    )
    retention = df(
        """
        SELECT fc.name AS committee_name, fcand.name AS candidate_name, fcand.state, fcand.office,
               dr.prior_cycle, dr.current_cycle, dr.prior_cycle_donor_count, dr.retained_donor_count,
               dr.retention_pct, dr.prior_cycle_total_receipts, dr.current_cycle_total_receipts
        FROM donor_retention dr
        JOIN fec_committees fc ON fc.committee_id = dr.committee_id
        JOIN fec_candidates fcand ON fcand.candidate_id = fc.candidate_id
        ORDER BY dr.retention_pct DESC
        """
    )
    st.dataframe(retention, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- Sponsor spotlight
elif page == "Sponsor Spotlight":
    sponsors = df(
        "SELECT DISTINCT sponsor_bioguide_id, sponsor_name FROM v_active_bills_overview "
        "WHERE sponsor_bioguide_id IS NOT NULL ORDER BY sponsor_name"
    )
    name_to_id = dict(zip(sponsors["sponsor_name"], sponsors["sponsor_bioguide_id"]))
    chosen_name = st.selectbox("Sponsor", sponsors["sponsor_name"])
    bioguide_id = name_to_id[chosen_name]

    st.subheader(chosen_name)
    st.markdown("**Active bills sponsored**")
    sponsored = df(
        "SELECT bill_id, title, current_stage, policy_area FROM v_active_bills_overview "
        "WHERE sponsor_bioguide_id = ? ORDER BY latest_action_date DESC",
        (bioguide_id,),
    )
    st.dataframe(sponsored, use_container_width=True, hide_index=True)

    st.markdown("**Campaign committee retention**")
    committee_retention = df(
        "SELECT DISTINCT committee_name, prior_cycle, current_cycle, retention_pct, "
        "retained_donor_count, prior_cycle_donor_count FROM v_bill_sponsor_retention "
        "WHERE sponsor_bioguide_id = ? ORDER BY current_cycle DESC",
        (bioguide_id,),
    )
    if committee_retention.empty:
        st.info("No retention data ingested yet for this sponsor's committee.")
    else:
        st.dataframe(committee_retention, use_container_width=True, hide_index=True)

    st.markdown("**Industry signal (approximate — keyword-based, not an authoritative classification)**")
    industry = df(
        "SELECT DISTINCT industry_label, cycle, matched_amount, matched_contribution_count "
        "FROM v_bill_industry_signal WHERE sponsor_bioguide_id = ? "
        "ORDER BY matched_amount DESC",
        (bioguide_id,),
    )
    if industry.empty:
        st.info("No industry-signal data ingested yet for this sponsor's committee.")
    else:
        st.dataframe(industry, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- Search by donor
elif page == "Search by Donor":
    st.subheader("Search by Donor")
    st.caption(
        "Look up an organization or PAC to see which campaign committees it gave to, and which "
        "bills those committees' sponsors have introduced. This covers organizational/PAC donors "
        "only — individual donor identity is never shown, only committee-level aggregates "
        "elsewhere in this app."
    )
    donor_query = st.text_input("Donor or PAC name contains")
    if not donor_query:
        st.info("Enter a donor or PAC name to search — e.g. a company, union, or trade association.")
    else:
        matches = df(
            "SELECT DISTINCT donor_name FROM fec_committee_org_donors "
            "WHERE donor_name LIKE ? ORDER BY donor_name LIMIT 50",
            (f"%{donor_query.upper()}%",),
        )
        if matches.empty:
            st.info("No matching donors ingested yet.")
        else:
            chosen_donor = st.selectbox("Matches", matches["donor_name"])

            st.markdown("**Committees funded**")
            given = df(
                "SELECT DISTINCT committee_name, cycle, total_amount, contribution_count "
                "FROM v_donor_bills WHERE donor_name = ? ORDER BY cycle DESC, total_amount DESC",
                (chosen_donor,),
            )
            st.dataframe(given, use_container_width=True, hide_index=True)

            st.markdown("**Bills sponsored by candidates this donor funded**")
            supported_bills = df(
                "SELECT DISTINCT bill_id, title, current_stage, policy_area, sponsor_name "
                "FROM v_donor_bills WHERE donor_name = ? ORDER BY bill_id",
                (chosen_donor,),
            )
            if supported_bills.empty:
                st.info("No bills found yet for this donor's funded candidates.")
            else:
                st.dataframe(supported_bills, use_container_width=True, hide_index=True)
