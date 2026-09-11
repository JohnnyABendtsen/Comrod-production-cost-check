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
def fetch_raf_order_ids(token):
    """Returns (set_of_ids, distinct_statuses_for_debug, error)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    base    = f"dataAreaId eq '{D365_COMPANY}'"

    # Try server-side filter first (two enum syntax variants)
    for flt in [
        "ProductionOrderStatus eq 'ReportedAsFinished'",
        "ProductionOrderStatus eq Microsoft.Dynamics.DataEntities.ProdStatus'ReportedAsFinished'",
    ]:
        try:
            url = (f"{D365_BASE}/data/ProductionOrderHeaders"
                   f"?$filter={base} and {flt}&$select=ProductionOrderNumber&$top=5000")
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                ids = {row["ProductionOrderNumber"] for row in r.json().get("value", [])}
                return ids, None, None
        except requests.exceptions.Timeout:
            pass  # Try next syntax

    # Fallback: fetch all (2 lightweight columns), filter in Python
    url = (f"{D365_BASE}/data/ProductionOrderHeaders"
           f"?$filter={base}&$select=ProductionOrderNumber,ProductionOrderStatus&$top=5000")
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None, None, f"HTTP {r.status_code}: {r.text[:300]}"
    rows = r.json().get("value", [])
    distinct = sorted({row.get("ProductionOrderStatus", "") for row in rows})
    ids = {row["ProductionOrderNumber"] for row in rows
           if row.get("ProductionOrderStatus") == "ReportedFinished"}
    return ids, distinct, None


def fetch_cost_data(token, prod_ids):
    """Fetch cost transactions only for the given production order IDs."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    all_rows = []
    id_list  = list(prod_ids)
    for i in range(0, len(id_list), 50):
        chunk    = id_list[i:i + 50]
        id_flt   = " or ".join(f"CollectRefProdId eq '{pid}'" for pid in chunk)
        url      = (f"{D365_BASE}/data/ProdCalcTransBiEntities"
                    f"?$filter=dataAreaId eq '{D365_COMPANY}' and ({id_flt})&$top=5000")
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:400]}"
        all_rows.extend(r.json().get("value", []))
    return pd.DataFrame(all_rows), None


def build_display(cost_df):
    cols      = list(cost_df.columns)
    prod_col  = next((c for c in cols if "collectrefprodid" in c.lower()), None)
    cost_col  = next((c for c in cols if c == "CostAmount"), None)
    rcost_col = next((c for c in cols if c == "RealCostAmount"), None)
    if not all([prod_col, cost_col, rcost_col]):
        return pd.DataFrame(), f"Missing columns. Available: {cols}"

    df = cost_df[[prod_col, cost_col, rcost_col]].copy()
    df.rename(columns={prod_col: "ProdId", cost_col: "CostAmount", rcost_col: "RealCostAmount"}, inplace=True)
    df["CostAmount"]     = pd.to_numeric(df["CostAmount"],     errors="coerce").fillna(0)
    df["RealCostAmount"] = pd.to_numeric(df["RealCostAmount"], errors="coerce").fillna(0)

    agg = df.groupby("ProdId", as_index=False).agg(
        CostAmount=("CostAmount", "sum"),
        RealCostAmount=("RealCostAmount", "sum")
    )
    if agg.empty:
        return pd.DataFrame(), "No cost data found."

    agg["Deviation %"] = ((agg["RealCostAmount"] - agg["CostAmount"]) / agg["CostAmount"].replace(0, float("nan")) * 100).round(2)
    agg["D365 Link"]   = D365_LINK + agg["ProdId"].astype(str)
    agg.sort_values("Deviation %", ascending=False, inplace=True, ignore_index=True)
    return agg[["ProdId", "Deviation %", "CostAmount", "RealCostAmount", "D365 Link"]], None


# ── UI ────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Comrod - Production Cost Deviation", layout="wide")
st.title("Comrod - Production Order Cost Deviation")
st.caption("Status: **Reported as Finished** · Compares Estimated cost vs Realized cost amount")

if st.button("Refresh data"):
    st.cache_data.clear()

@st.cache_data(ttl=300, show_spinner="Fetching data from D365...")
def load_data():
    try:
        token = get_token()
    except Exception as e:
        return None, None, None, f"Auth error: {e}"

    prod_ids, distinct_statuses, err = fetch_raf_order_ids(token)
    if err:
        return None, None, None, err
    if not prod_ids:
        return set(), None, distinct_statuses, None

    cost_df, err2 = fetch_cost_data(token, prod_ids)
    return prod_ids, cost_df, distinct_statuses, err2

prod_ids, cost_df, distinct_statuses, warn = load_data()

if warn:
    st.error(warn)
    st.stop()

# Debug: show actual status values if server-side filter failed
if distinct_statuses is not None:
    with st.expander("Debug: distinct ProductionOrderStatus values from D365"):
        st.write(distinct_statuses)

if not prod_ids:
    st.error("No Reported as Finished orders found.")
    st.stop()

if cost_df is None or cost_df.empty:
    st.error("No cost data found for the fetched orders.")
    st.stop()

display_df, err = build_display(cost_df)
if err:
    st.error(err)
    st.stop()

st.dataframe(
    display_df,
    use_container_width=True,
    column_config={
        "D365 Link":      st.column_config.LinkColumn("Production Order", display_text="Open in D365"),
        "Deviation %":    st.column_config.NumberColumn(format="%.2f %%"),
        "CostAmount":     st.column_config.NumberColumn("Estimated Cost",  format="%.2f"),
        "RealCostAmount": st.column_config.NumberColumn("Realized Cost",   format="%.2f"),
    },
    hide_index=True,
)

st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
