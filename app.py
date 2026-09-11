import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import os

# ── Azure AD / D365 credentials ─────────────────────────────────────────────
TENANT_ID     = os.getenv("TENANT_ID", "YOUR_TENANT_ID")
CLIENT_ID     = os.getenv("CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "YOUR_CLIENT_SECRET")

D365_BASE     = "https://comrodgroup-prod.operations.eu.dynamics.com"
D365_COMPANY  = "COM"
D365_LINK     = f"{D365_BASE}/?cmp=com&mi=ProdTable&q=ProdId%3D"


# ── Auth ─────────────────────────────────────────────────────────────────────
def get_token():
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         f"{D365_BASE}/.default",
    }
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


# ── Fetch ProdCalcTrans (Reported as Finished only) ──────────────────────────
def fetch_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # ProdCalcTrans – cost data
    calc_url = (
        f"{D365_BASE}/data/ProdCalcTrans"
        f"?$filter=dataAreaId eq '{D365_COMPANY}'"
        f"&$select=CollectRefProdId,CostAmount,RealCostAmount,Qty,RealQty"
        f"&$top=5000"
    )
    r = requests.get(calc_url, headers=headers, timeout=60)
    r.raise_for_status()
    calc = pd.DataFrame(r.json().get("value", []))

    if calc.empty:
        return pd.DataFrame()

    calc = calc.rename(columns={"CollectRefProdId": "ProdId"})

    # ProdTable – filter status = Reported as Finished (status 4)
    prod_url = (
        f"{D365_BASE}/data/ProdTable"
        f"?$filter=dataAreaId eq '{D365_COMPANY}' and ProdStatus eq 'ReportedAsFinished'"
        f"&$select=ProdId,ItemId"
        f"&$top=5000"
    )
    r = requests.get(prod_url, headers=headers, timeout=60)
    r.raise_for_status()
    prod = pd.DataFrame(r.json().get("value", []))

    if prod.empty:
        return pd.DataFrame()

    # ReleasedProductsV2 – product names
    items = prod["ItemId"].dropna().unique().tolist()
    filter_str = " or ".join([f"ItemNumber eq '{i}'" for i in items[:200]])
    items_url = (
        f"{D365_BASE}/data/ReleasedProductsV2"
        f"?$filter=({filter_str})"
        f"&$select=ItemNumber,ProductName"
    )
    r = requests.get(items_url, headers=headers, timeout=60)
    r.raise_for_status()
    items_df = pd.DataFrame(r.json().get("value", []))
    items_df = items_df.rename(columns={"ItemNumber": "ItemId"})

    # Join
    df = calc.merge(prod[["ProdId", "ItemId"]], on="ProdId", how="inner")
    df = df.merge(items_df, on="ItemId", how="left")

    # Aggregate per ProdId
    agg = df.groupby(["ProdId", "ProductName"], as_index=False).agg(
        CostAmount=("CostAmount", "sum"),
        RealCostAmount=("RealCostAmount", "sum"),
        Qty=("RealQty", "sum"),
    )

    agg["Afvigelse_%"] = (
        (agg["RealCostAmount"] - agg["CostAmount"]) / agg["CostAmount"].abs() * 100
    ).round(1)

    return agg[["ProdId", "ProductName", "Afvigelse_%", "Qty", "CostAmount", "RealCostAmount"]]


# ── Sample data (shown when credentials are missing) ─────────────────────────
SAMPLE = pd.DataFrame([
    {"ProdId": "P-10042", "ProductName": "Antenna VHF 108",   "Afvigelse_%":  18.4, "Qty": 120, "CostAmount": 45200, "RealCostAmount": 53511},
    {"ProdId": "P-10039", "ProductName": "Cable Assy 15m",    "Afvigelse_%":   7.2, "Qty":  80, "CostAmount": 12800, "RealCostAmount": 13722},
    {"ProdId": "P-10035", "ProductName": "Whip Antenna 3m",   "Afvigelse_%":  -3.1, "Qty": 200, "CostAmount": 31000, "RealCostAmount": 30039},
    {"ProdId": "P-10031", "ProductName": "Mast Mount Kit",    "Afvigelse_%":  12.0, "Qty":  60, "CostAmount": 18600, "RealCostAmount": 20832},
    {"ProdId": "P-10028", "ProductName": "Broadband Antenna",  "Afvigelse_%":  -8.5, "Qty":  45, "CostAmount": 67500, "RealCostAmount": 61763},
    {"ProdId": "P-10021", "ProductName": "Coax Cable 5m",     "Afvigelse_%":   5.9, "Qty": 300, "CostAmount":  9000, "RealCostAmount":  9531},
    {"ProdId": "P-10017", "ProductName": "SATCOM Terminal",   "Afvigelse_%":  22.3, "Qty":  10, "CostAmount": 85000, "RealCostAmount": 103955},
])


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Comrod · Produktionsordre Kostprisafvigelse",
    page_icon="🏭",
    layout="wide",
)

st.markdown("""
<style>
  .kpi-box{background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:16px 20px;text-align:center}
  .kpi-label{font-size:12px;color:#64748B;font-weight:600;text-transform:uppercase;letter-spacing:.06em}
  .kpi-value{font-size:28px;font-weight:700;color:#1A2332;line-height:1.2}
  .kpi-value.red{color:#DC2626}
  .kpi-value.green{color:#16A34A}
  a{text-decoration:none;color:#0078D4;font-weight:500}
  a:hover{text-decoration:underline}
  [data-testid="stAppViewContainer"]{background:#F8FAFC}
</style>
""", unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────
col_h1, col_h2 = st.columns([5, 1])
with col_h1:
    st.markdown("#### 🏭 Comrod Group — Produktionsordre Kostprisafvigelse")
    st.caption("Kun ordrer med status *Reported as Finished* · Klik ProdId for at åbne ordren i D365 F&O")
with col_h2:
    refresh = st.button("🔄 Opdater nu", use_container_width=True)


# ── Load data ─────────────────────────────────────────────────────────────────
creds_ok = not any(v.startswith("YOUR_") for v in [TENANT_ID, CLIENT_ID, CLIENT_SECRET])

@st.cache_data(ttl=600, show_spinner="Henter data fra D365 F&O …")
def load(_trigger):
    token = get_token()
    return fetch_data(token), datetime.now().strftime("%d-%m-%Y %H:%M")

if refresh:
    st.cache_data.clear()

if creds_ok:
    try:
        df, last_updated = load(0)
        sample_mode = False
    except Exception as e:
        st.error(f"D365 F&O fejl: {e}")
        df, last_updated = SAMPLE.copy(), "— (fejl, viser demo-data)"
        sample_mode = True
else:
    df = SAMPLE.copy()
    last_updated = "— (demo-data, mangler credentials)"
    sample_mode = True

if sample_mode:
    st.warning("⚠️ Viser demo-data — TENANT_ID / CLIENT_ID / CLIENT_SECRET er ikke sat som secrets.")

st.caption(f"Sidst opdateret: {last_updated}")

if df.empty:
    st.info("Ingen ordrer med status Reported as Finished fundet.")
    st.stop()


# ── KPI strip ─────────────────────────────────────────────────────────────────
total   = len(df)
avg_dev = df["Afvigelse_%"].mean()
max_dev = df["Afvigelse_%"].max()
under   = (df["Afvigelse_%"] < 0).sum()

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown(f'<div class="kpi-box"><div class="kpi-label">Ordrer</div><div class="kpi-value">{total}</div></div>', unsafe_allow_html=True)
with k2:
    cls = "red" if avg_dev > 0 else "green"
    st.markdown(f'<div class="kpi-box"><div class="kpi-label">Gns. afvigelse</div><div class="kpi-value {cls}">{avg_dev:+.1f}%</div></div>', unsafe_allow_html=True)
with k3:
    st.markdown(f'<div class="kpi-box"><div class="kpi-label">Maks. afvigelse</div><div class="kpi-value red">{max_dev:+.1f}%</div></div>', unsafe_allow_html=True)
with k4:
    st.markdown(f'<div class="kpi-box"><div class="kpi-label">Under budget</div><div class="kpi-value green">{under}</div></div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── Filter ────────────────────────────────────────────────────────────────────
filter_opt = st.radio(
    "Filter",
    ["Alle", "Høj >10%", "Middel 5-10%", "Lav <5%", "Under budget"],
    horizontal=True,
    label_visibility="collapsed",
)

fdf = df.copy()
if filter_opt == "Høj >10%":
    fdf = fdf[fdf["Afvigelse_%"] > 10]
elif filter_opt == "Middel 5-10%":
    fdf = fdf[(fdf["Afvigelse_%"] >= 5) & (fdf["Afvigelse_%"] <= 10)]
elif filter_opt == "Lav <5%":
    fdf = fdf[(fdf["Afvigelse_%"] >= 0) & (fdf["Afvigelse_%"] < 5)]
elif filter_opt == "Under budget":
    fdf = fdf[fdf["Afvigelse_%"] < 0]

fdf = fdf.sort_values("Afvigelse_%", ascending=False).reset_index(drop=True)


# ── Søgning ───────────────────────────────────────────────────────────────────
search = st.text_input("🔍 Søg ProdId eller Produktnavn", placeholder="fx P-10042 eller Antenna")
if search:
    mask = (
        fdf["ProdId"].str.contains(search, case=False, na=False) |
        fdf["ProductName"].str.contains(search, case=False, na=False)
    )
    fdf = fdf[mask]


# ── Table ─────────────────────────────────────────────────────────────────────
def color_dev(val):
    if val > 10:  return "color:#DC2626;font-weight:600"
    if val > 0:   return "color:#D97706;font-weight:500"
    return "color:#16A34A;font-weight:500"

def build_html(row):
    link  = f'<a href="{D365_LINK}{row.ProdId}" target="_blank">{row.ProdId}</a>'
    style = color_dev(row["Afvigelse_%"])
    dev   = f'<span style="{style}">{row["Afvigelse_%"]:+.1f}%</span>'
    qty   = f'{row.Qty:,.0f}'
    est   = f'{row.CostAmount:,.0f} kr'
    real  = f'{row.RealCostAmount:,.0f} kr'
    return f"<tr><td>{link}</td><td>{row.ProductName}</td><td style='text-align:right'>{dev}</td><td style='text-align:right'>{qty}</td><td style='text-align:right'>{est}</td><td style='text-align:right'>{real}</td></tr>"

rows_html = "\n".join(fdf.apply(build_html, axis=1))

table_html = f"""
<table style="width:100%;border-collapse:collapse;font-size:14px">
  <thead>
    <tr style="border-bottom:2px solid #E2E8F0;color:#64748B;font-size:12px;text-transform:uppercase;letter-spacing:.05em">
      <th style="padding:8px 12px;text-align:left">ProdId</th>
      <th style="padding:8px 12px;text-align:left">Produktnavn</th>
      <th style="padding:8px 12px;text-align:right">Afvigelse %</th>
      <th style="padding:8px 12px;text-align:right">Antal</th>
      <th style="padding:8px 12px;text-align:right">Estimeret</th>
      <th style="padding:8px 12px;text-align:right">Realiseret</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
"""

st.markdown(table_html, unsafe_allow_html=True)
st.caption(f"{len(fdf)} ordre(r) vist")
