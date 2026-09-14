import streamlit as st
import streamlit.components.v1 as components
import requests
import pandas as pd
from datetime import datetime

# ── Azure AD / D365 credentials (Streamlit Secrets) ─────────────────────────
TENANT_ID     = st.secrets.get("TENANT_ID", "YOUR_TENANT_ID")
CLIENT_ID     = st.secrets.get("CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "YOUR_CLIENT_SECRET")

D365_BASE    = "https://comrodgroup-prod.operations.eu.dynamics.com"
D365_COMPANY = "COM"
D365_LINK    = f"{D365_BASE}/?cmp=com&mi=ProdTable&q=ProdId%3D"


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


# ── Find OData entity name for ProdCalcTrans ─────────────────────────────────
def find_calc_entity(token):
    """Query OData metadata to find the entity backed by ProdCalcTrans table."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    candidates = [
        "ProdCalcTrans",
        "ProductionOrderCostLines",
        "ProductionOrderCostEstimates",
        "ProdCostEstimates",
        "ProdCostLines",
        "ProductionCostEstimateLines",
        "ProdCalcTransV2",
        "ProdCalcTransDataEntity",
    ]
    for name in candidates:
        url = f"{D365_BASE}/data/{name}?$top=1&$filter=dataAreaId eq '{D365_COMPANY}'"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return name
    return None


# ── Fetch data ────────────────────────────────────────────────────────────────
def fetch_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # 1. ProdTable – Reported as Finished (ingen Pool i $select – hentes fra ProductionOrderHeaders)
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
        return pd.DataFrame(), None

    # 2. Pool fra ProductionOrderHeaders (felt: ProductionPool – bekræftet i v28)
    pool_url = (
        f"{D365_BASE}/data/ProductionOrderHeaders"
        f"?$filter=dataAreaId eq '{D365_COMPANY}' and ProductionOrderStatus eq 'ReportedFinished'"
        f"&$select=ProductionOrderNumber,ProductionPool"
        f"&$top=5000"
    )
    rp = requests.get(pool_url, headers=headers, timeout=30)
    if rp.status_code == 200:
        pool_df = pd.DataFrame(rp.json().get("value", []))
        if not pool_df.empty:
            pool_df = pool_df.rename(columns={"ProductionOrderNumber": "ProdId", "ProductionPool": "Pool"})
            prod = prod.merge(pool_df[["ProdId", "Pool"]], on="ProdId", how="left")
    if "Pool" not in prod.columns:
        prod["Pool"] = ""
    prod["Pool"] = prod["Pool"].fillna("")

    # 2. Find cost entity
    entity = find_calc_entity(token)
    calc = pd.DataFrame()

    if entity:
        calc_url = (
            f"{D365_BASE}/data/{entity}"
            f"?$filter=dataAreaId eq '{D365_COMPANY}'"
            f"&$top=5000"
        )
        r2 = requests.get(calc_url, headers=headers, timeout=60)
        if r2.status_code == 200:
            calc = pd.DataFrame(r2.json().get("value", []))

    # 3. Fallback: try cost fields directly on ProdTable
    if calc.empty:
        ext_url = (
            f"{D365_BASE}/data/ProdTable"
            f"?$filter=dataAreaId eq '{D365_COMPANY}' and ProdStatus eq 'ReportedAsFinished'"
            f"&$select=ProdId,ItemId,CostAmount,RealCostAmount,ProdQty"
            f"&$top=5000"
        )
        r3 = requests.get(ext_url, headers=headers, timeout=60)
        r3.raise_for_status()
        calc = pd.DataFrame(r3.json().get("value", []))
        if not calc.empty:
            calc = calc.rename(columns={"ProdQty": "Qty"})
            df = calc.copy()
    else:
        col_map = {}
        for c in calc.columns:
            cl = c.lower()
            if "prodid" in cl or "productionorder" in cl:
                col_map[c] = "ProdId"
            elif cl == "costamount" or "estimatedcost" in cl:
                col_map[c] = "CostAmount"
            elif "realcost" in cl or "actualcost" in cl:
                col_map[c] = "RealCostAmount"
            elif cl in ("qty", "quantity", "prodqty"):
                col_map[c] = "Qty"
        calc = calc.rename(columns=col_map)
        df = calc.merge(prod[["ProdId", "ItemId", "Pool"]], on="ProdId", how="inner")

    if "ProdId" not in calc.columns or calc.empty:
        return pd.DataFrame(), entity

    if "ItemId" not in df.columns:
        df = df.merge(prod[["ProdId", "ItemId", "Pool"]], on="ProdId", how="left")
    elif "Pool" not in df.columns:
        df = df.merge(prod[["ProdId", "Pool"]], on="ProdId", how="left")

    # 4. ReleasedProductsV2 – product names
    items = df["ItemId"].dropna().unique().tolist()
    if items:
        filter_str = " or ".join([f"ItemNumber eq '{i}'" for i in items[:200]])
        ri = requests.get(
            f"{D365_BASE}/data/ReleasedProductsV2?$filter=({filter_str})&$select=ItemNumber,ProductName",
            headers=headers, timeout=60
        )
        if ri.status_code == 200:
            idf = pd.DataFrame(ri.json().get("value", [])).rename(columns={"ItemNumber": "ItemId"})
            df = df.merge(idf, on="ItemId", how="left")
        else:
            df["ProductName"] = df["ItemId"]
    else:
        df["ProductName"] = ""

    # 5. Aggregate
    for col in ["CostAmount", "RealCostAmount", "Qty"]:
        if col not in df.columns:
            df[col] = 0.0

    agg = df.groupby(["ProdId", "ProductName", "Pool"], as_index=False).agg(
        CostAmount=("CostAmount", "sum"),
        RealCostAmount=("RealCostAmount", "sum"),
        Qty=("Qty", "sum"),
    )
    agg["Afvigelse_%"] = (
        (agg["RealCostAmount"] - agg["CostAmount"]) / agg["CostAmount"].abs() * 100
    ).round(1)

    return agg[["ProdId", "ProductName", "Pool", "Afvigelse_%", "Qty", "CostAmount", "RealCostAmount"]], entity


# ── Sample data ───────────────────────────────────────────────────────────────
SAMPLE = pd.DataFrame([
    {"ProdId": "P-10042", "ProductName": "Antenna VHF 108",  "Pool": "POOL1", "Afvigelse_%":  18.4, "Qty": 120, "CostAmount": 45200, "RealCostAmount": 53511},
    {"ProdId": "P-10039", "ProductName": "Cable Assy 15m",   "Pool": "POOL1", "Afvigelse_%":   7.2, "Qty":  80, "CostAmount": 12800, "RealCostAmount": 13722},
    {"ProdId": "P-10035", "ProductName": "Whip Antenna 3m",  "Pool": "POOL2", "Afvigelse_%":  -3.1, "Qty": 200, "CostAmount": 31000, "RealCostAmount": 30039},
    {"ProdId": "P-10031", "ProductName": "Mast Mount Kit",   "Pool": "POOL2", "Afvigelse_%":  12.0, "Qty":  60, "CostAmount": 18600, "RealCostAmount": 20832},
    {"ProdId": "P-10028", "ProductName": "Broadband Antenna","Pool": "POOL3", "Afvigelse_%":  -8.5, "Qty":  45, "CostAmount": 67500, "RealCostAmount": 61763},
    {"ProdId": "P-10021", "ProductName": "Coax Cable 5m",    "Pool": "POOL3", "Afvigelse_%":   5.9, "Qty": 300, "CostAmount":  9000, "RealCostAmount":  9531},
    {"ProdId": "P-10017", "ProductName": "SATCOM Terminal",  "Pool": "POOL4", "Afvigelse_%":  22.3, "Qty":  10, "CostAmount": 85000, "RealCostAmount": 103955},
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
  .kpi-value.red{color:#DC2626} .kpi-value.green{color:#16A34A}
  a{text-decoration:none;color:#0078D4;font-weight:500} a:hover{text-decoration:underline}
  [data-testid="stAppViewContainer"]{background:#F8FAFC}
</style>
""", unsafe_allow_html=True)

col_h1, col_h2 = st.columns([5, 1])
with col_h1:
    st.markdown("#### 🏭 Comrod Group — Produktionsordre Kostprisafvigelse")
    st.caption("Kun ordrer med status *Reported as Finished* · Klik ProdId for at åbne ordren i D365 F&O")
with col_h2:
    refresh = st.button("🔄 Opdater nu", use_container_width=True)

creds_ok = not any(v.startswith("YOUR_") for v in [TENANT_ID, CLIENT_ID, CLIENT_SECRET])

@st.cache_data(ttl=600, show_spinner="Henter data fra D365 F&O …")
def load(_t):
    token = get_token()
    return fetch_data(token), datetime.now().strftime("%d-%m-%Y %H:%M")

if refresh:
    st.cache_data.clear()

entity_used = None
if creds_ok:
    try:
        (df, entity_used), last_updated = load(0)
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
if entity_used:
    st.caption(f"OData entity: `{entity_used}`")

st.caption(f"Sidst opdateret: {last_updated}")

if df.empty:
    st.info("Ingen ordrer fundet — tjek OData entitetsnavn og D365 adgang.")
    st.stop()

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

filter_opt = st.radio("Filter", ["Alle", "Høj >10%", "Middel 5-10%", "Lav <5%", "Under budget"],
                      horizontal=True, label_visibility="collapsed")
fdf = df.copy()
if filter_opt == "Høj >10%":      fdf = fdf[fdf["Afvigelse_%"] > 10]
elif filter_opt == "Middel 5-10%": fdf = fdf[(fdf["Afvigelse_%"] >= 5) & (fdf["Afvigelse_%"] <= 10)]
elif filter_opt == "Lav <5%":      fdf = fdf[(fdf["Afvigelse_%"] >= 0) & (fdf["Afvigelse_%"] < 5)]
elif filter_opt == "Under budget":  fdf = fdf[fdf["Afvigelse_%"] < 0]
fdf = fdf.sort_values("Afvigelse_%", ascending=False).reset_index(drop=True)

search = st.text_input("🔍 Søg ProdId, Produktnavn eller Pool", placeholder="fx P-10042 eller Antenna")
if search:
    pool_col = fdf["Pool"].str.contains(search, case=False, na=False) if "Pool" in fdf.columns else False
    fdf = fdf[fdf["ProdId"].str.contains(search, case=False, na=False) |
              fdf["ProductName"].str.contains(search, case=False, na=False) |
              pool_col]

def color_dev(v):
    if v > 10: return "color:#DC2626;font-weight:600"
    if v > 0:  return "color:#D97706;font-weight:500"
    return "color:#16A34A;font-weight:500"

_fdf = fdf.rename(columns={"Afvigelse_%": "Afvigelse_pct"})
rows = "\n".join(
    f"<tr>"
    f"<td style='padding:8px 12px'><a href='{D365_LINK}{r.ProdId}' target='_blank'>{r.ProdId}</a></td>"
    f"<td style='padding:8px 12px'>{r.Pool or ''}</td>"
    f"<td style='padding:8px 12px'>{r.ProductName}</td>"
    f"<td style='text-align:right;padding:8px 12px' data-val='{r.Afvigelse_pct}'><span style='{color_dev(r.Afvigelse_pct)}'>{r.Afvigelse_pct:+.1f}%</span></td>"
    f"<td style='text-align:right;padding:8px 12px' data-val='{r.Qty}'>{r.Qty:,.0f}</td>"
    f"<td style='text-align:right;padding:8px 12px' data-val='{r.CostAmount}'>{r.CostAmount:,.0f} kr</td>"
    f"<td style='text-align:right;padding:8px 12px' data-val='{r.RealCostAmount}'>{r.RealCostAmount:,.0f} kr</td>"
    f"</tr>"
    for r in _fdf.itertuples()
)

table_html = f"""
<style>
  body{{margin:0;font-family:sans-serif}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  thead tr{{border-bottom:2px solid #E2E8F0;color:#64748B;font-size:12px;text-transform:uppercase;letter-spacing:.05em}}
  th{{padding:8px 12px;cursor:pointer;user-select:none;white-space:nowrap}}
  th:hover{{color:#1A2332}}
  th.asc::after{{content:' ▲'}}
  th.desc::after{{content:' ▼'}}
  a{{text-decoration:none;color:#0078D4;font-weight:500}}
  a:hover{{text-decoration:underline}}
</style>
<table id="t">
  <thead><tr>
    <th style="text-align:left" onclick="sort(0,false)">ProdId</th>
    <th style="text-align:left" onclick="sort(1,false)">Pool</th>
    <th style="text-align:left" onclick="sort(2,false)">Produktnavn</th>
    <th style="text-align:right" onclick="sort(3,true)">Afvigelse %</th>
    <th style="text-align:right" onclick="sort(4,true)">Antal</th>
    <th style="text-align:right" onclick="sort(5,true)">Estimeret</th>
    <th style="text-align:right" onclick="sort(6,true)">Realiseret</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
<script>
var _c=-1,_a=true;
function sort(col,num){{
  var tb=document.querySelector('#t tbody');
  var rs=Array.from(tb.rows);
  if(_c===col){{_a=!_a;}}else{{_c=col;_a=true;}}
  rs.sort(function(a,b){{
    var av=a.cells[col].getAttribute('data-val')||a.cells[col].innerText;
    var bv=b.cells[col].getAttribute('data-val')||b.cells[col].innerText;
    if(num){{return _a?parseFloat(av)-parseFloat(bv):parseFloat(bv)-parseFloat(av);}}
    return _a?av.localeCompare(bv):bv.localeCompare(av);
  }});
  rs.forEach(function(r){{tb.appendChild(r);}});
  document.querySelectorAll('th').forEach(function(th,i){{
    th.className=i===col?(_a?'asc':'desc'):'';
  }});
}}
</script>
"""

components.html(table_html, height=min(60 + len(fdf) * 37, 1200), scrolling=True)
st.caption(f"{len(fdf)} ordre(r) vist")
