import streamlit as st
import requests
import pandas as pd
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
TENANT_ID     = st.secrets.get("TENANT_ID", "")
CLIENT_ID     = st.secrets.get("CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "")
D365_BASE     = "https://comrodgroup-prod.operations.eu.dynamics.com"
D365_COMPANY  = "COM"
D365_LINK     = f"{D365_BASE}/?cmp=COM&mi=ProdTableListPage&q=ProdId%3D"

# Enum syntax attempts for ReportedAsFinished filter
_STATUS_FILTERS = [
    "ProductionOrderStatus eq 'ReportedAsFinished'",
    "ProductionOrderStatus eq Microsoft.Dynamics.DataEntities.ProdStatus'ReportedAsFinished'",
]

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_token():
    url  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         f"{D365_BASE}/.default",
    }
    r = requests.post(url, data=data, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

# ── Data fetch ────────────────────────────────────────────────────────────────
def fetch_cost_data(token, prod_ids):
    """Fetch cost data only for the given production order IDs."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    if not prod_ids:
        return pd.DataFrame(), None

    base_filter = f"dataAreaId eq '{D365_COMPANY}'"

    # Build ID filter — batch in chunks of 50 to avoid URL length limits
    all_rows = []
    chunk_size = 50
    id_list = list(prod_ids)
    for i in range(0, len(id_list), chunk_size):
        chunk = id_list[i:i + chunk_size]
        id_filter = " or ".join(f"CollectRefProdId eq '{pid}'" for pid in chunk)
        url = (
            f"{D365_BASE}/data/ProdCalcTransBiEntities"
            f"?$filter={base_filter} and ({id_filter})"
            f"&$top=5000"
        )
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:400]}"
        all_rows.extend(r.json().get("value", []))

    return pd.DataFrame(all_rows), None


def fetch_reported_as_finished_orders(token):
    """Fetch only ReportedAsFinished production orders — tries OData filter first."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    base    = f"dataAreaId eq '{D365_COMPANY}'"

    # Try each enum filter syntax until one works
    for status_filter in _STATUS_FILTERS:
        url = (
            f"{D365_BASE}/data/ProductionOrderHeaders"
            f"?$filter={base} and {status_filter}"
            f"&$select=ProductionOrderNumber"
            f"&$top=5000"
        )
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            rows = r.json().get("value", [])
            ids  = {row["ProductionOrderNumber"] for row in rows}
            return ids, None
        # 400 = bad filter syntax — try next

    # Last resort: fetch all with only 2 columns, filter in Python
    url = (
        f"{D365_BASE}/data/ProductionOrderHeaders"
        f"?$filter={base}"
        f"&$select=ProductionOrderNumber,ProductionOrderStatus"
        f"&$top=5000"
    )
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    rows = r.json().get("value", [])
    ids  = {row["ProductionOrderNumber"] for row in rows
            if row.get("ProductionOrderStatus") == "ReportedAsFinished"}
    return ids, None


def build_display(prod_ids, cost_df):
    if not prod_ids:
        return pd.DataFrame(), "No 'Reported as Finished' orders found."
    if cost_df is None or cost_df.empty:
        return pd.DataFrame(), "No cost calculation data found."

    cols = list(cost_df.columns)

    prod_col  = next((c for c in cols if c == "CollectRefProdId" or "collectrefprodid" in c.lower()), None)
    cost_col  = next((c for c in cols if c == "CostAmount"), None)
    rcost_col = next((c for c in cols if c == "RealCostAmount"), None)

    if not all([prod_col, cost_col, rcost_col]):
        return pd.DataFrame(), f"Missing columns. Available: {cols}"

    cost_df = cost_df[[prod_col, cost_col, rcost_col]].copy()
    cost_df.rename(columns={prod_col: "ProdId", cost_col: "CostAmount", rcost_col: "RealCostAmount"}, inplace=True)
    cost_df["CostAmount"]     = pd.to_numeric(cost_df["CostAmount"],     errors="coerce").fillna(0)
    cost_df["RealCostAmount"] = pd.to_numeric(cost_df["RealCostAmount"], errors="coerce").fillna(0)

    agg = cost_df.groupby("ProdId", as_index=False).agg(
        CostAmount=("CostAmount", "sum"),
        RealCostAmount=("RealCostAmount", "sum")
    )

    if agg.empty:
        return pd.DataFrame(), "No cost data found for 'Reported as Finished' orders."

    agg["Deviation %"] = ((agg["RealCostAmount"] - agg["CostAmount"]) / agg["CostAmount"].replace(0, float("nan")) * 100).round(2)
    agg["D365 Link"]   = D365_LINK + agg["ProdId"].astype(str)
    agg.sort_values("Deviation %", ascending=False, inplace=True, ignore_index=True)
    return agg[["ProdId", "Deviation %", "CostAmount", "RealCostAmount", "D365 Link"]], None


# ── UI ────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Comrod – Production Cost Deviation", layout="wide")
st.title("🏭 Comrod – Production Order Cost Deviation")
st.caption("Status: **Reported as Finished** · Compares Estimated cost vs Realized cost amount")

if st.button("🔄 Refresh data"):
    st.cache_data.clear()

@st.cache_data(ttl=300, show_spinner="Fetching data from D365…")
def load_data():
    try:
        token = get_token()
    except Exception as e:
        return None, None, f"Auth error: {e}"

    prod_ids, e1 = fetch_reported_as_finished_orders(token)
    if e1:
        return None, None, e1

    if not prod_ids:
        return set(), pd.DataFrame(), None

    cost_df, e2 = fetch_cost_data(token, prod_ids)
    return prod_ids, cost_df, e2

prod_ids, cost_df, warn = load_data()

if warn:
    st.error(warn)
    st.stop()

if prod_ids is None:
    st.stop()

display_df, err = build_display(prod_ids, cost_df)

if err:
    st.error(err)
    st.stop()

st.dataframe(
    display_df,
    use_container_width=True,
    column_config={
        "D365 Link":      st.column_config.LinkColumn("Production Order", display_text="🔗 Open in D365"),
        "Deviation %":    st.column_config.NumberColumn(format="%.2f %%"),
        "CostAmount":     st.column_config.NumberColumn("Estimated Cost",  format="%.2f"),
        "RealCostAmount": st.column_config.NumberColumn("Realized Cost",   format="%.2f"),
    },
    hide_index=True,
)

st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
