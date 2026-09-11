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

# ── Fetch RAF order IDs ───────────────────────────────────────────────────────
def fetch_raf_order_ids(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    base = f"dataAreaId eq '{D365_COMPANY}'"
    # Try server-side filter (fast path)
    for flt in [
        "ProductionOrderStatus eq 'ReportedFinished'",
        "ProductionOrderStatus eq Microsoft.Dynamics.DataEntities.ProdStatus'ReportedFinished'",
    ]:
        try:
            url = (f"{D365_BASE}/data/ProductionOrderHeaders"
                   f"?={base} and {flt}&=ProductionOrderNumber&=5000")
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                ids = {row["ProductionOrderNumber"] for row in r.json().get("value", [])}
                if ids:
                    return ids, None
        except requests.exceptions.Timeout:
            pass
    # Fallback: fetch all, filter in Python
    url = (f"{D365_BASE}/data/ProductionOrderHeaders"
           f"?={base}&=ProductionOrderNumber,ProductionOrderStatus&=5000")
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    rows = r.json().get("value", [])
    ids = {row["ProductionOrderNumber"] for row in rows
           if row.get("ProductionOrderStatus") == "ReportedFinished"}
    return ids, None

# ── Fetch ALL cost data, join with RAF IDs in Python ─────────────────────────
def fetch_cost_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = (f"{D365_BASE}/data/ProdCalcTransBiEntities"
           f"?=dataAreaId eq '{D365_COMPANY}'"
           f"&=5000")
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:400]}"
    return pd.DataFrame(r.json().get("value", [])), None

# ── Build display dataframe ───────────────────────────────────────────────────
def build_display(prod_ids, cost_df):
    if not prod_ids:
        return pd.DataFrame(), "No Reported as Finished orders found."
    if cost_df is None or cost_df.empty:
        return pd.DataFrame(), "No cost calculation data found."

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

    # Filter to RAF orders only
    df = df[df["ProdId"].astype(str).isin({str(i) for i in prod_ids})]

    agg = df.groupby("ProdId", as_index=False).agg(
        CostAmount=("CostAmount", "sum"),
        RealCostAmount=("RealCostAmount", "sum")
    )
    # Only orders with realized costs
    agg = agg[agg["RealCostAmount"] > 0]

    if agg.empty:
        return pd.DataFrame(), "No RAF orders with realized costs found."

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
        return None, None, f"Auth error: {e}"
    prod_ids, err = fetch_raf_order_ids(token)
    if err:
        return None, None, err
    cost_df, err2 = fetch_cost_data(token)
    return prod_ids, cost_df, err2

prod_ids, cost_df, warn = load_data()

if warn:
    st.error(warn)
    st.stop()

if not prod_ids:
    st.error("No Reported as Finished orders found.")
    st.stop()

display_df, err = build_display(prod_ids, cost_df)
if err:
    st.error(err)
    st.stop()

# ── Deviation filter ──────────────────────────────────────────────────────────
min_dev = float(display_df["Deviation %"].min())
max_dev = float(display_df["Deviation %"].max())
col1, col2 = st.columns(2)
with col1:
    lo = st.number_input("Min deviation %", value=round(min_dev, 2), step=1.0, format="%.2f")
with col2:
    hi = st.number_input("Max deviation %", value=round(max_dev, 2), step=1.0, format="%.2f")
filtered = display_df[(display_df["Deviation %"] >= lo) & (display_df["Deviation %"] <= hi)]

st.dataframe(
    filtered,
    use_container_width=True,
    column_config={
        "ProdId":         st.column_config.TextColumn("Order", width="small"),
        "Deviation %":    st.column_config.NumberColumn("Deviation %", format="%.2f %%", width="small"),
        "CostAmount":     st.column_config.NumberColumn("Estimated Cost", format="%.2f", width="medium"),
        "RealCostAmount": st.column_config.NumberColumn("Realized Cost",  format="%.2f", width="medium"),
        "D365 Link":      st.column_config.LinkColumn("Production Order", display_text="Open in D365", width="small"),
    },
    hide_index=True,
)

st.caption(f"Showing {len(filtered)} of {len(display_df)} orders · Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
