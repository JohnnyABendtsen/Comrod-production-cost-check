import streamlit as st
import requests
import pandas as pd
from datetime import datetime

TENANT_ID     = st.secrets.get("TENANT_ID", "")
CLIENT_ID     = st.secrets.get("CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "")
D365_BASE     = "https://comrodgroup-prod.operations.eu.dynamics.com"
D365_COMPANY  = "COM"
D365_LINK     = f"{D365_BASE}/?cmp=COM&mi=ProdTableListPage&q=ProdId%3D"

def get_token():
    url  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET, "scope": f"{D365_BASE}/.default"}
    r = requests.post(url, data=data, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

@st.cache_data(ttl=300, show_spinner="Fetching status counts from D365...")
def fetch_status_counts(token_dummy):
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {
        "$filter": f"dataAreaId eq '{D365_COMPANY}'",
        "$select": "ProductionOrderNumber,ProductionOrderStatus",
        "$top":    "10000",
    }
    r = requests.get(f"{D365_BASE}/data/ProductionOrderHeaders",
                     headers=headers, params=params, timeout=60)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    rows = r.json().get("value", [])
    df = pd.DataFrame(rows)
    counts = df.groupby("ProductionOrderStatus").size().reset_index(name="Count")
    counts = counts.sort_values("Count", ascending=False)
    return counts, None

st.set_page_config(page_title="Comrod - Status Debug", layout="wide")
st.title("D365 Production Order Status Counts")
st.caption("Debug: identify OData status values and their counts")

counts_df, err = fetch_status_counts("v1")
if err:
    st.error(err)
else:
    st.dataframe(counts_df, use_container_width=True, hide_index=True)
    st.caption(f"Total orders: {counts_df['Count'].sum()}")