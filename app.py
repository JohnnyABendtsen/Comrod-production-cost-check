import streamlit as st
import requests
import pandas as pd
from datetime import datetime

TENANT_ID     = st.secrets.get("TENANT_ID", "YOUR_TENANT_ID")
CLIENT_ID     = st.secrets.get("CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "YOUR_CLIENT_SECRET")

D365_BASE    = "https://comrodgroup-prod.operations.eu.dynamics.com"
D365_COMPANY = "COM"
D365_LINK    = f"{D365_BASE}/?cmp=com&mi=ProdTable&q=ProdId%3D"

def get_token():
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET, "scope": f"{D365_BASE}/.default"}
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def find_calc_entity(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    candidates = ["ProdCalcTrans", "ProductionOrderCostLines",
                  "ProductionOrderCostEstimates", "ProdCostEstimates",
                  "ProdCostLines", "ProductionCostEstimateLines",
                  "ProdCalcTransV2", "ProdCalcTransDataEntity"]
    for name in candidates:
        url = f"{D365_BASE}/data/{name}?$top=1&$filter=dataAreaId eq '{D365_COMPANY}'"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return name
    return None

def fetch_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    prod_url = (f"{D365_BASE}/data/ProdTable"
                f"?$filter=dataAreaId eq '{D365_COMPANY}' and ProdStatus eq 'ReportedAsFinished'"
                f"&$select=ProdId,ItemId&$top=5000")
    r = requests.get(prod_url, headers=headers, timeout=60)
    r.raise_for_status()
    prod = pd.DataFrame(r.json().get("value", []))
    if prod.empty:
        return pd.DataFrame(), None
    entity = find_calc_entity(token)
    calc = pd.DataFrame()
    if entity:
        r2 = requests.get(f"{D365_BASE}/data/{entity}?$filter=dataAreaId eq '{D365_COMPANY}'&$top=5000",
                          headers=headers, timeout=60)
        if r2.status_code == 200:
            calc = pd.DataFrame(r2.json().get("value", []))
    if calc.empty:
        r3 = requests.get(
            f"{D365_BASE}/data/ProdTable?$filter=dataAreaId eq '{D365_COMPANY}' and ProdStatus eq 'ReportedAsFinished'"
            f"&$select=ProdId,ItemId,CostAmount,RealCostAmount,ProdQty&$top=5000",
            headers=headers, timeout=60)
        r3.raise_for_status()
        calc = pd.DataFrame(r3.json().get("value", []))
        if not calc.empty:
            calc = calc.rename(columns={"ProdQty": "Qty"})
            df = calc.copy()
        else:
            return pd.DataFrame(), entity
    else:
        col_map = {}
        for c in calc.columns:
            cl = c.lower()
            if "prodid" in cl or "productionorder" in cl: col_map[c] = "ProdId"
            elif cl == "costamount" or "estimatedcost" in cl: col_map[c] = "CostAmount"
            elif "realcost" in cl or "actualcost" in cl: col_map[c] = "RealCostAmount"
            elif cl in ("qty", "quantity", "prodqty"): col_map[c] = "Qty"
        calc = calc.rename(columns=col_map)
        if "ProdId" not in calc.columns:
            return pd.DataFrame(), entity
        df = calc.merge(prod[["ProdId", "ItemId"]], on="ProdId", how="inner")

    if "ItemId" not in df.columns:
        df = df.merge(prod[["ProdId", "ItemId"]], on="ProdId", how="left")
    items = df["ItemId"].dropna().unique().tolist()
    if items:
        filter_str = " or ".join([f"ItemNumber eq '{i}'" for i in items[:200]])
        ri = requests.get(f"{D365_BASE}/data/ReleasedProductsV2?$filter=({filter_str})&$select=ItemNumber,ProductName",
                          headers=headers, timeout=60)
        if ri.status_code == 200:
            idf = pd.DataFrame(ri.json().get("value", [])).rename(columns={"ItemNumber": "ItemId"})
            df = df.merge(idf, on="ItemId", how="left")
        else:
            df["ProductName"] = df["ItemId"]
    else:
        df["ProductName"] = ""
    for col in ["CostAmount", "RealCostAmount", "Qty"]:
        if col not in df.columns: df[col] = 0.0
    agg = df.groupby(["ProdId", "ProductName"], as_index=False).agg(
        CostAmount=("CostAmount", "sum"), RealCostAmount=("RealCostAmount", "sum"), Qty=("Qty", "sum"))
    agg["Afvigelse_%"] = ((agg["RealCostAmount"] - agg["CostAmount"]) / agg["CostAmount"].abs() * 100).round(1)
    return agg[["ProdId", "ProductName", "Afvigelse_%", "Qty", "CostAmount", "RealCostAmount"]], entity

SAMPLE = pd.DataFrame([
    {"ProdId": "P-10042", "ProductName": "Antenna VHF 108",  "Afvigelse_%":  18.4, "Qty": 120, "CostAmount": 45200, "RealCostAmount": 53511},
    {"ProdId": "P-10039", "ProductName": "Cable Assy 15m",   "Afvigelse_%":   7.2, "Qty":  80, "CostAmount": 12800, "RealCostAmount": 13722},
    {"ProdId": "P-10035", "ProductName": "Whip Antenna 3m",  "Afvigelse_%":  -3.1, "Qty": 200, "CostAmount": 31000, "RealCostAmount": 30039},
    {"ProdId": "P-10031", "ProductName": "Mast Mount Kit",   "Afvigelse_%":  12.0, "Qty":  60, "CostAmount": 18600, "RealCostAmount": 20832},
    {"ProdId": "P-10028", "ProductName": "Broadband Antenna", "Afvigelse_%": -8.5, "Qty":  45, "CostAmount": 67500, "RealCostAmount": 61763},
])

st.set_page_config(page_title="Comrod · Kostprisafvigelse", page_icon="🏭", layout="wide")
st.markdown("""
<style>
  .kpi-box{background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:16px 20px;text-align:center}
  .kpi-label{font-size:12px;color:#64748B;font-weight:600;text-transform:uppercase;letter-spacing:.06em}
  .kpi-value{font-size:28px;font-weight:700;color:#1A2332;line-height:1.2}
  .kpi-value.red{color:#DC2626} .kpi-value.green{color:#16A34A}
  a{text-decoration:none;color:#0078D4;font-weight:500} a:hover{text-decoration:underline}
</style>""", unsafe_allow_html=True)

col_h1, col_h2 = st.columns([5, 1])
with col_h1:
    st.markdown("#### 🏭 Comrod Group — Produktionsordre Kostprisafvigelse")
    st.caption("Kun ordrer med status *Reported as Finished* · Klik ProdId for at åbne i D365 F&O")
with col_h2:
    refresh = st.button("🔄 Opdater nu", use_container_width=True)

creds_ok = not any(str(v).startswith("YOUR_") for v in [TENANT_ID, CLIENT_ID, CLIENT_SECRET])

@st.cache_data(ttl=600, show_spinner="Henter data fra D365 F&O …")
def load(_trigger):
    token = get_token()
    return fetch_data(token)

if refresh:
    st.cache_data.clear()

sample_mode = False
entity_used = None
if creds_ok:
    try:
        df, entity_used = load(0)
        if df.empty:
            st.info("Ingen ordrer med status Reported as Finished fundet.")
            st.stop()
        last_updated = datetime.now().strftime("%d-%m-%Y %H:%M")
    except Exception as e:
        st.error(f"D365 F&O fejl: {e}")
        df = SAMPLE.copy()
        last_updated = "— (fejl, viser demo-data)"
        sample_mode = True
else:
    df = SAMPLE.copy()
    last_updated = "— (demo-data, mangler credentials)"
    sample_mode = True

if sample_mode:
    st.warning("⚠️ Viser demo-data — credentials er ikke sat som Secrets i Streamlit.")
if entity_used:
    st.caption(f"✅ OData entity fundet: `{entity_used}`")
elif creds_ok and not sample_mode:
    st.caption("⚠️ Ingen separat kostpris-entity fundet — bruger ProdTable direkte.")

st.caption(f"Sidst opdateret: {last_updated}")

total = len(df); avg_dev = df["Afvigelse_%"].mean(); max_dev = df["Afvigelse_%"].max(); under = (df["Afvigelse_%"] < 0).sum()
k1, k2, k3, k4 = st.columns(4)
with k1: st.markdown(f'<div class="kpi-box"><div class="kpi-label">Ordrer</div><div class="kpi-value">{total}</div></div>', unsafe_allow_html=True)
with k2:
    cls = "red" if avg_dev > 0 else "green"
    st.markdown(f'<div class="kpi-box"><div class="kpi-label">Gns. afvigelse</div><div class="kpi-value {cls}">{avg_dev:+.1f}%</div></div>', unsafe_allow_html=True)
with k3: st.markdown(f'<div class="kpi-box"><div class="kpi-label">Maks. afvigelse</div><div class="kpi-value red">{max_dev:+.1f}%</div></div>', unsafe_allow_html=True)
with k4: st.markdown(f'<div class="kpi-box"><div class="kpi-label">Under budget</div><div class="kpi-value green">{under}</div></div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

filter_opt = st.radio("Filter", ["Alle", "Høj >10%", "Middel 5-10%", "Lav <5%", "Under budget"], horizontal=True, label_visibility="collapsed")
fdf = df.copy()
if filter_opt == "Høj >10%": fdf = fdf[fdf["Afvigelse_%"] > 10]
elif filter_opt == "Middel 5-10%": fdf = fdf[(fdf["Afvigelse_%"] >= 5) & (fdf["Afvigelse_%"] <= 10)]
elif filter_opt == "Lav <5%": fdf = fdf[(fdf["Afvigelse_%"] >= 0) & (fdf["Afvigelse_%"] < 5)]
elif filter_opt == "Under budget": fdf = fdf[fdf["Afvigelse_%"] < 0]
fdf = fdf.sort_values("Afvigelse_%", ascending=False).reset_index(drop=True)

search = st.text_input("🔍 Søg ProdId eller Produktnavn", placeholder="fx P-10042 eller Antenna")
if search:
    mask = fdf["ProdId"].str.contains(search, case=False, na=False) | fdf["ProductName"].str.contains(search, case=False, na=False)
    fdf = fdf[mask]

def color_dev(val):
    if val > 10: return "color:#DC2626;font-weight:600"
    if val > 0:  return "color:#D97706;font-weight:500"
    return "color:#16A34A;font-weight:500"

def build_html(row):
    link = f'<a href="{D365_LINK}{row.ProdId}" target="_blank">{row.ProdId}</a>'
    style = color_dev(row["Afvigelse_%"])
    return f"<tr><td>{link}</td><td>{row.ProductName}</td><td style='text-align:right'><span style='{style}'>{row['Afvigelse_%']:+.1f}%</span></td><td style='text-align:right'>{row.Qty:,.0f}</td><td style='text-align:right'>{row.CostAmount:,.0f} kr</td><td style='text-align:right'>{row.RealCostAmount:,.0f} kr</td></tr>"

rows_html = "\n".join(fdf.apply(build_html, axis=1))
st.markdown(f"""
<table style="width:100%;border-collapse:collapse;font-size:14px">
  <thead><tr style="border-bottom:2px solid #E2E8F0;color:#64748B;font-size:12px;text-transform:uppercase">
    <th style="padding:8px 12px;text-align:left">ProdId</th>
    <th style="padding:8px 12px;text-align:left">Produktnavn</th>
    <th style="padding:8px 12px;text-align:right">Afvigelse %</th>
    <th style="padding:8px 12px;text-align:right">Antal</th>
    <th style="padding:8px 12px;text-align:right">Estimeret</th>
    <th style="padding:8px 12px;text-align:right">Realiseret</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>""", unsafe_allow_html=True)
st.caption(f"{len(fdf)} ordre(r) vist")
