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
D365_LINK     = f"{D365_BASE}/?cmp=com&mi=ProdTable&q=ProdId%3D"

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
def fetch_cost_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    probe = requests.get(
        f"{D365_BASE}/data/ProdCalcTransBiEntities?$top=1&$filter=dataAreaId eq '{D365_COMPANY}'",
        headers=headers, timeout=15
    )
    if probe.status_code != 200:
        return None, f"HTTP {probe.status_code}: {probe.text[:400]}"

    sample = probe.json().get("value", [])
    if not sample:
        return pd.DataFrame(), None

    all_cols = list(sample[0].keys())

    prod_col = next((c for c in all_cols if "prodid" in c.lower() or "collectref" in c.lower() or "productionorder" in c.lower()), None)
    type_col = next((c for c in all_cols if c.lower() in ("reftype", "transtype", "costgrouptype", "type", "linetype")), None)

    base_filter = f"dataAreaId eq '{D365_COMPANY}'"
    type_filter = f" and {type_col} eq 'Production'" if type_col else ""

    url = (
        f"{D365_BASE}/data/ProdCalcTransBiEntities"
        f"?$filter={base_filter}{type_filter}"
        f"&$top=5000"
    )
    r = requests.get(url, headers=headers, timeout=30)

    if r.status_code != 200:
        url_no_filter = f"{D365_BASE}/data/ProdCalcTransBiEntities?$filter={base_filter}&$top=5000"
        r2 = requests.get(url_no_filter, headers=headers, timeout=30)
        if r2.status_code != 200:
            return None, f"HTTP {r2.status_code}: {r2.text[:400]}"
        df = pd.DataFrame(r2.json().get("value", []))
        return df, f"⚠️ Type filter failed — showing all rows. Columns: {all_cols}"

    df = pd.DataFrame(r.json().get("value", []))
    return df, None


def fetch_finished_orders(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = (
        f"{D365_BASE}/data/ProductionOrderHeaders"
        f"?$filter=dataAreaId eq '{D365_COMPANY}'"
        f"&$select=ProductionOrderNumber,ItemNumber,ProductionOrderStatus"
        f"&$top=5000"
    )
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    df = pd.DataFrame(r.json().get("value", []))
    return df, None


def build_display(orders_df, cost_df):
    if orders_df is None or orders_df.empty:
        return pd.DataFrame(), "No 'Reported as Finished' orders found."
    if cost_df is None or cost_df.empty:
        return pd.DataFrame(), "No cost calculation data found."

    cols = list(cost_df.columns)

    prod_col  = next((c for c in cols if "collectrefprodid" in c.lower() or "prodid" in c.lower() or "productionordernumber" in c.lower()), None)
    cost_col  = next((c for c in cols if c.lower() == "costamount"), None)
    rcost_col = next((c for c in cols if c.lower() == "realcostamount"), None)

    if not all([prod_col, cost_col, rcost_col]):
        return pd.DataFrame(), f"Missing columns. Available: {cols}"

    cost_df = cost_df[[prod_col, cost_col, rcost_col]].copy()
    cost_df.rename(columns={prod_col: "ProdId", cost_col: "CostAmount", rcost_col: "RealCostAmount"}, inplace=True)
    cost_df["CostAmount"]     = pd.to_numeric(cost_df["CostAmount"],     errors="coerce")
    cost_df["RealCostAmount"] = pd.to_numeric(cost_df["RealCostAmount"], errors="coerce")

    finished_ids = set(orders_df["ProductionOrderNumber"].astype(str))
    cost_df = cost_df[cost_df["ProdId"].astype(str).isin(finished_ids)]

    if cost_df.empty:
        return pd.DataFrame(), "No match between cost data and finished orders."

    cost_df["Deviation %"] = ((cost_df["RealCostAmount"] - cost_df["CostAmount"]) / cost_df["CostAmount"] * 100).round(2)
    cost_df["D365 Link"]   = D365_LINK + cost_df["ProdId"].astype(str)
    cost_df.sort_values("Deviation %", ascending=False, inplace=True, ignore_index=True)
    return cost_df[["ProdId", "Deviation %", "CostAmount", "RealCostAmount", "D365 Link"]], None


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
    orders_df, e1 = fetch_finished_orders(token)
    if e1:
        return None, None, e1
    cost_df, e2 = fetch_cost_data(token)
    return orders_df, cost_df, e2

orders_df, cost_df, warn = load_data()

if warn and "⚠️" in str(warn):
    st.warning(warn)
elif warn:
    st.error(warn)
    st.stop()

if orders_df is None:
    st.stop()

display_df, err = build_display(orders_df, cost_df)

if err:
    st.error(err)
    if cost_df is not None and not cost_df.empty:
        st.info(f"Columns in ProdCalcTransBiEntities: {list(cost_df.columns)}")
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
