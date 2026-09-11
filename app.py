import streamlit as st
import requests

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

@st.cache_data(ttl=300, show_spinner="Fetching...")
def fetch_entities():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    r = requests.get(f"{D365_BASE}/data", headers=headers, timeout=30)
    entities = [e["name"] for e in r.json().get("value", [])]
    return sorted([e for e in entities if "prod" in e.lower() and "trans" in e.lower()])

st.set_page_config(page_title="Entity Search", layout="wide")
st.title("D365 entities: prod + trans")
result = fetch_entities()
for e in result:
    st.write(e)