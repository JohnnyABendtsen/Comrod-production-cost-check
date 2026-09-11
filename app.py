import streamlit as st
import requests
from datetime import datetime

TENANT_ID     = st.secrets.get("TENANT_ID", "")
CLIENT_ID     = st.secrets.get("CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "")
D365_BASE     = "https://comrodgroup-prod.operations.eu.dynamics.com"

def get_token():
    url  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET, "scope": f"{D365_BASE}/.default"}
    r = requests.post(url, data=data, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

@st.cache_data(ttl=300, show_spinner="Fetching entity list...")
def fetch_entities():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    r = requests.get(f"{D365_BASE}/data", headers=headers, timeout=30)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    data = r.json()
    entities = [e["name"] for e in data.get("value", [])]
    prod_cost = sorted([e for e in entities if "prod" in e.lower() and "cost" in e.lower()])
    prod_calc = sorted([e for e in entities if "prod" in e.lower() and "calc" in e.lower()])
    return {"prod+cost": prod_cost, "prod+calc": prod_calc}, None

st.set_page_config(page_title="Entity Search", layout="wide")
st.title("D365 Entity Search: prod+cost / prod+calc")

result, err = fetch_entities()
if err:
    st.error(err)
else:
    st.subheader("prod + cost")
    st.write(result["prod+cost"])
    st.subheader("prod + calc")
    st.write(result["prod+calc"])