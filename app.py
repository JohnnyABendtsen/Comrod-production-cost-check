import streamlit as st
import requests
import pandas as pd
import io
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
TENANT_ID     = st.secrets.get("TENANT_ID", "")
CLIENT_ID     = st.secrets.get("CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "")
D365_BASE     = "https://comrodgroup-prod.operations.eu.dynamics.com"
D365_COMPANY  = "COM"
D365_LIST_URL = f"{D365_BASE}/?cmp=COM&mi=ProdTableListPage"


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


def fetch_raf_order_ids(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {
        "$filter": f"dataAreaId eq '{D365_COMPANY}' and ProductionOrderStatus eq 'ReportedFinished'",
        "$select": "ProductionOrderNumber",
        "$top":    "10000",
    }
    r = requests.get(f"{D365_BASE}/data/ProductionOrderHeaders",
                     headers=headers, params=params, timeout=20)
    if r.status_code == 200:
        ids = {row["ProductionOrderNumber"] for row in r.json().get("value", [])}
        if ids:
            return ids, None
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
    ids = {row["ProductionOrderNumber"] for row in rows
           if row.get("ProductionOrderStatus") == "ReportedFinished"}
    return ids, None


def fetch_cost_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    all_rows = []
    skip = 0
    page_size = 5000
    while True:
        params = {
            "$filter": f"dataAreaId eq '{D365_COMPANY}'",
            "$select": "CollectRefProdId,CostAmount,RealCostAmount",
            "$top":    str(page_size),
            "$skip":   str(skip),
        }
        r = requests.get(f"{D365_BASE}/data/ProdCalcTransBiEntities",
                         headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:400]}"
        batch = r.json().get("value", [])
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        skip += page_size
    return pd.DataFrame(all_rows), None


def build_display(prod_ids, cost_df):
    base = pd.DataFrame({"ProdId": sorted(prod_ids)})
    if cost_df is not None and not cost_df.empty:
        df = cost_df.copy()
        df.rename(columns={"CollectRefProdId": "ProdId"}, inplace=True)
        df["CostAmount"]     = pd.to_numeric(df["CostAmount"],     errors="coerce").fillna(0)
        df["RealCostAmount"] = pd.to_numeric(df["RealCostAmount"], errors="coerce").fillna(0)
        df = df[df["ProdId"].isin(prod_ids)]
        agg = df.groupby("ProdId", as_index=False).agg(
            CostAmount=("CostAmount", "sum"),
            RealCostAmount=("RealCostAmount", "sum")
        )
        result = base.merge(agg, on="ProdId", how="left").fillna(0)
    else:
        result = base.copy()
        result["CostAmount"]     = 0.0
        result["RealCostAmount"] = 0.0
    result["Deviation %"] = (
        (result["RealCostAmount"] - result["CostAmount"])
        / result["CostAmount"].replace(0, float("nan")) * 100
    ).round(2)
    result.sort_values("Deviation %", ascending=False, inplace=True, ignore_index=True)
    return result[["ProdId", "Deviation %", "CostAmount", "RealCostAmount"]], None


def to_excel(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        export = df.copy()
        export.columns = ["Production Order", "Deviation %", "Estimated Cost", "Realized Cost"]
        export.to_excel(writer, index=False, sheet_name="Cost Deviation")
        ws = writer.sheets["Cost Deviation"]
        for col in ws.columns:
            max_len = max(len(str(col[0].value)), max((len(str(c.value)) for c in col[1:]), default=0))
            ws.column_dimensions[col[0].column_letter].width = max_len + 4
    return buf.getvalue()


def render_table(df, d365_url):
    rows_html = ""
    for _, row in df.iterrows():
        pid   = row["ProdId"]
        dev   = f"{row['Deviation %']:.1f}%"
        est   = f"{row['CostAmount']:,.0f}"
        real  = f"{row['RealCostAmount']:,.0f}"
        color = "color:#c0392b;font-weight:600" if row["Deviation %"] > 0 else ("color:#27ae60;font-weight:600" if row["Deviation %"] < 0 else "")
        rows_html += f"""<tr>
          <td style="white-space:nowrap">{pid}</td>
          <td style="text-align:right;white-space:nowrap;{color}">{dev}</td>
          <td style="text-align:right;white-space:nowrap">{est}</td>
          <td style="text-align:right;white-space:nowrap">{real}</td>
          <td style="white-space:nowrap">
            <button onclick="navigator.clipboard.writeText('{pid}');window.open('{d365_url}','_blank')">Open</button>
          </td>
        </tr>"""

    return f"""
    <style>
      table {{border-collapse:collapse;font-family:sans-serif;font-size:13px;width:auto}}
      th {{background:#1f4e79;color:white;padding:6px 12px;text-align:left;white-space:nowrap}}
      td {{padding:5px 12px;border-bottom:1px solid #ddd}}
      tr:hover td {{background:#f0f4f8}}
      button {{background:#1f4e79;color:white;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px}}
      button:hover {{background:#2e6da4}}
      button:active {{background:#27ae60}}
    </style>
    <table>
      <thead><tr>
        <th>Order</th><th style="text-align:right">Dev %</th>
        <th style="text-align:right">Estimated</th><th style="text-align:right">Realized</th>
        <th>Open (copies ID)</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <p style="font-size:11px;color:#888;margin-top:6px">
      Klik Open: aabner D365 All Production Orders og kopierer ordre-ID til clipboard. Paste (Ctrl+V) i Quick Filter.
    </p>"""


# ── UI ────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Comrod - Production Cost Deviation", layout="wide")
st.title("Comrod - Production Order Cost Deviation")
st.caption("Status: **Reported as Finished** · Shows orders **outside** the deviation range")

if st.button("Refresh data"):
    st.cache_data.clear()

@st.cache_data(ttl=300, show_spinner="Fetching data from D365...")
def load_data(_v="v15"):
    try:
        token = get_token()
    except Exception as e:
        return None, None, f"Auth error: {e}"
    prod_ids, err = fetch_raf_order_ids(token)
    if err:
        return None, None, err
    if not prod_ids:
        return set(), None, None
    cost_df, _ = fetch_cost_data(token)
    return prod_ids, cost_df, None

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

col1, col2 = st.columns(2)
with col1:
    lo = st.number_input("Min deviation % (show below this)", value=-10.0, step=1.0, format="%.1f")
with col2:
    hi = st.number_input("Max deviation % (show above this)", value=10.0, step=1.0, format="%.1f")

filtered = display_df[(display_df["Deviation %"] < lo) | (display_df["Deviation %"] > hi)]

total     = len(display_df)
out_range = len(filtered)

col_info, col_export = st.columns([4, 1])
with col_info:
    st.caption(f"Showing {out_range} orders outside [{lo:.1f}%, {hi:.1f}%] of {total} total . Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
with col_export:
    st.download_button(
        label="Export til Excel",
        data=to_excel(filtered),
        file_name=f"cost_deviation_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

import streamlit.components.v1 as components
components.html(render_table(filtered, D365_LIST_URL), height=min(80 + len(filtered) * 34, 800), scrolling=True)
