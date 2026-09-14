import streamlit as st
import streamlit.components.v1 as components
import requests
import pandas as pd
import io
from datetime import datetime

TENANT_ID     = st.secrets.get("TENANT_ID", "")
CLIENT_ID     = st.secrets.get("CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "")
D365_BASE     = "https://comrodgroup-prod.operations.eu.dynamics.com"
D365_COMPANY  = "COM"
D365_LIST_URL = f"{D365_BASE}/?cmp={D365_COMPANY}&mi=ProdTableListPage"

def get_token():
    url  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET, "scope": f"{D365_BASE}/.default"}
    r = requests.post(url, data=data, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

def _pick_pool(row):
    """Try most likely OData field names for ProdPoolId in order."""
    for f in ("ProductionPoolId", "ProductionPool", "ProdPoolId"):
        v = row.get(f, "")
        if v:
            return v
    return ""

def fetch_raf_order_ids(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {
        "$filter": f"dataAreaId eq '{D365_COMPANY}' and ProductionOrderStatus eq 'ReportedFinished'",
        "$select": "ProductionOrderNumber,ProductionPoolId,ProductionPool,ProdPoolId",
        "$top": "10000",
    }
    r = requests.get(f"{D365_BASE}/data/ProductionOrderHeaders", headers=headers, params=params, timeout=20)
    if r.status_code == 200:
        rows = r.json().get("value", [])
        id_pool = {row["ProductionOrderNumber"]: _pick_pool(row) for row in rows}
        if id_pool:
            return id_pool, None
    params = {
        "$filter": f"dataAreaId eq '{D365_COMPANY}'",
        "$select": "ProductionOrderNumber,ProductionOrderStatus,ProductionPoolId,ProductionPool,ProdPoolId",
        "$top": "10000",
    }
    r = requests.get(f"{D365_BASE}/data/ProductionOrderHeaders", headers=headers, params=params, timeout=60)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    rows = r.json().get("value", [])
    id_pool = {row["ProductionOrderNumber"]: _pick_pool(row)
               for row in rows if row.get("ProductionOrderStatus") == "ReportedFinished"}
    return id_pool, None

def fetch_cost_data(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    all_rows = []
    skip = 0
    page_size = 5000
    while True:
        params = {
            "$filter": f"dataAreaId eq '{D365_COMPANY}'",
            "$select": "CollectRefProdId,CostAmount,RealCostAmount",
            "$top": str(page_size),
            "$skip": str(skip),
        }
        r = requests.get(f"{D365_BASE}/data/ProdCalcTransBiEntities", headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:400]}"
        batch = r.json().get("value", [])
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        skip += page_size
    return pd.DataFrame(all_rows), None

def build_display(id_pool, cost_df):
    sorted_ids = sorted(id_pool.keys())
    base = pd.DataFrame({"ProdId": sorted_ids, "Pool": [id_pool[k] for k in sorted_ids]})
    if cost_df is not None and not cost_df.empty:
        df = cost_df.copy()
        df.rename(columns={"CollectRefProdId": "ProdId"}, inplace=True)
        df["CostAmount"]     = pd.to_numeric(df["CostAmount"],     errors="coerce").fillna(0)
        df["RealCostAmount"] = pd.to_numeric(df["RealCostAmount"], errors="coerce").fillna(0)
        df = df[df["ProdId"].isin(id_pool.keys())]
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
    return result[["ProdId", "Pool", "Deviation %", "CostAmount", "RealCostAmount"]]

def to_excel(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        export = df.copy()
        export.columns = ["Production Order", "Pool", "Deviation %", "Estimated Cost", "Realized Cost"]
        export.to_excel(writer, index=False, sheet_name="Cost Deviation")
        ws = writer.sheets["Cost Deviation"]
        for col in ws.columns:
            max_len = max(len(str(col[0].value)), max((len(str(c.value or "")) for c in col[1:]), default=0))
            ws.column_dimensions[col[0].column_letter].width = max_len + 4
    return buf.getvalue()

def render_table(df, d365_url):
    esc = lambda s: str(s).replace("&","&amp;").replace("<","&lt;").replace('"','&quot;')
    rows_html = ""
    for _, row in df.iterrows():
        pid  = esc(row["ProdId"])
        pool = esc(str(row.get("Pool", "") or ""))
        dev  = row["Deviation %"]
        est  = f"{row['CostAmount']:,.0f}"
        real = f"{row['RealCostAmount']:,.0f}"
        color = "red" if dev > 0 else "#1a7f37"
        dev_str = f"{dev:.1f}%"
        rows_html += f"""<tr>
  <td style="white-space:nowrap;padding:4px 10px" data-val="{pid}">{pid}</td>
  <td style="white-space:nowrap;padding:4px 10px" data-val="{pool}">{pool}</td>
  <td style="white-space:nowrap;padding:4px 10px;color:{color};text-align:right" data-val="{dev}">{dev_str}</td>
  <td style="white-space:nowrap;padding:4px 10px;text-align:right" data-val="{row['CostAmount']}">{est}</td>
  <td style="white-space:nowrap;padding:4px 10px;text-align:right" data-val="{row['RealCostAmount']}">{real}</td>
  <td style="white-space:nowrap;padding:4px 10px">
    <button onclick="navigator.clipboard.writeText('{pid}');window.open('{d365_url}','_blank')"
      style="background:#1f4e79;color:white;border:none;padding:3px 10px;border-radius:4px;cursor:pointer">Open</button>
  </td>
</tr>"""

    js = """
<script>
var _sc = -1, _sa = true;
var activeFilters = {};

function sortTable(col, numeric) {
  var tbody = document.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  if (_sc === col) { _sa = !_sa; } else { _sc = col; _sa = true; }
  rows.sort(function(a, b) {
    var av = a.cells[col].getAttribute('data-val');
    var bv = b.cells[col].getAttribute('data-val');
    if (numeric) {
      return _sa ? parseFloat(av) - parseFloat(bv) : parseFloat(bv) - parseFloat(av);
    }
    return _sa ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
  document.querySelectorAll('thead th span.sort-arrow').forEach(function(s, i) {
    s.textContent = i === col ? (_sa ? ' ▲' : ' ▼') : '';
  });
}

function buildDropdown(col) {
  var tbody = document.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var vals = {};
  rows.forEach(function(r) {
    var v = r.cells[col].getAttribute('data-val') || '';
    vals[v] = true;
  });
  var sorted = Object.keys(vals).sort(function(a,b){ return a.localeCompare(b, undefined, {numeric:true}); });
  var dd = document.getElementById('filter-dd');
  dd.innerHTML = '';
  var cur = activeFilters[col] || null;

  var allItem = document.createElement('div');
  allItem.style.cssText = 'padding:4px 10px;cursor:pointer;background:' + (cur === null ? '#1f4e79' : '#fff') + ';color:' + (cur === null ? '#fff' : '#000');
  allItem.textContent = '(All)';
  allItem.onmouseenter = function(){ if(cur !== null) this.style.background='#eef'; };
  allItem.onmouseleave = function(){ if(cur !== null) this.style.background='#fff'; };
  allItem.onclick = function() {
    delete activeFilters[col];
    applyFilters();
    dd.style.display = 'none';
  };
  dd.appendChild(allItem);

  sorted.forEach(function(v) {
    var item = document.createElement('div');
    item.style.cssText = 'padding:4px 10px;cursor:pointer;background:' + (cur === v ? '#1f4e79' : '#fff') + ';color:' + (cur === v ? '#fff' : '#000');
    item.textContent = v || '(blank)';
    item.onmouseenter = function(){ if(cur !== v) this.style.background='#eef'; };
    item.onmouseleave = function(){ if(cur !== v) this.style.background='#fff'; };
    item.onclick = function() {
      activeFilters[col] = v;
      applyFilters();
      dd.style.display = 'none';
    };
    dd.appendChild(item);
  });

  var th = document.querySelectorAll('thead th')[col];
  var rect = th.getBoundingClientRect();
  dd.style.left = rect.left + 'px';
  dd.style.top  = (rect.bottom + window.scrollY) + 'px';
  dd.style.display = 'block';
}

function applyFilters() {
  var tbody = document.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  rows.forEach(function(r) {
    var show = true;
    Object.keys(activeFilters).forEach(function(col) {
      var val = r.cells[col].getAttribute('data-val') || '';
      if (val !== activeFilters[col]) show = false;
    });
    r.style.display = show ? '' : 'none';
  });
  // Update filter indicators
  document.querySelectorAll('thead th span.filter-dot').forEach(function(s, i) {
    s.textContent = activeFilters[i] !== undefined ? ' ●' : '';
  });
}

document.addEventListener('click', function(e) {
  var dd = document.getElementById('filter-dd');
  if (!e.target.closest('#filter-dd') && !e.target.closest('.flt-btn')) {
    dd.style.display = 'none';
  }
});
</script>
"""

    return f"""<div style="position:relative">
<div id="filter-dd" style="display:none;position:fixed;z-index:9999;background:#fff;border:1px solid #ccc;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,.2);max-height:300px;overflow-y:auto;font-size:13px;min-width:120px"></div>
<table style="border-collapse:collapse;width:auto;font-size:14px">
<thead><tr style="background:#1f4e79;color:white">
  <th style="padding:6px 10px;text-align:left;white-space:nowrap;cursor:pointer" onclick="sortTable(0,false)">Order<span class="sort-arrow"></span><span class="filter-dot" style="color:#ffd700"></span><button class="flt-btn" onclick="event.stopPropagation();buildDropdown(0)" style="background:none;border:none;color:white;cursor:pointer;padding:0 0 0 4px;font-size:11px">▾</button></th>
  <th style="padding:6px 10px;text-align:left;white-space:nowrap;cursor:pointer" onclick="sortTable(1,false)">Pool<span class="sort-arrow"></span><span class="filter-dot" style="color:#ffd700"></span><button class="flt-btn" onclick="event.stopPropagation();buildDropdown(1)" style="background:none;border:none;color:white;cursor:pointer;padding:0 0 0 4px;font-size:11px">▾</button></th>
  <th style="padding:6px 10px;text-align:right;white-space:nowrap;cursor:pointer" onclick="sortTable(2,true)">Dev %<span class="sort-arrow"></span><span class="filter-dot" style="color:#ffd700"></span><button class="flt-btn" onclick="event.stopPropagation();buildDropdown(2)" style="background:none;border:none;color:white;cursor:pointer;padding:0 0 0 4px;font-size:11px">▾</button></th>
  <th style="padding:6px 10px;text-align:right;white-space:nowrap;cursor:pointer" onclick="sortTable(3,true)">Estimated<span class="sort-arrow"></span><span class="filter-dot" style="color:#ffd700"></span><button class="flt-btn" onclick="event.stopPropagation();buildDropdown(3)" style="background:none;border:none;color:white;cursor:pointer;padding:0 0 0 4px;font-size:11px">▾</button></th>
  <th style="padding:6px 10px;text-align:right;white-space:nowrap;cursor:pointer" onclick="sortTable(4,true)">Realized<span class="sort-arrow"></span><span class="filter-dot" style="color:#ffd700"></span><button class="flt-btn" onclick="event.stopPropagation();buildDropdown(4)" style="background:none;border:none;color:white;cursor:pointer;padding:0 0 0 4px;font-size:11px">▾</button></th>
  <th style="padding:6px 10px;text-align:left;white-space:nowrap">Open (copies ID)</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>{js}"""

# ── UI ────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Comrod - Production Cost Deviation", layout="wide")
st.title("Comrod - Production Order Cost Deviation")
st.markdown("## Powered by Inspirit365")
st.caption("Status: **Reported as Finished** · Shows orders **outside** the deviation range")

st.markdown("""<style>
div[data-testid="stNumberInput"] { max-width: 200px; }
</style>""", unsafe_allow_html=True)

qp = st.query_params
_lo_default = float(qp.get("lo", -10.0))
_hi_default = float(qp.get("hi",  10.0))

@st.cache_data(ttl=300, show_spinner="Fetching data from D365...")
def load_data(_v="v29"):
    try:
        token = get_token()
    except Exception as e:
        return None, None, f"Auth error: {e}"
    id_pool, err = fetch_raf_order_ids(token)
    if err:
        return None, None, err
    if not id_pool:
        return {}, None, None
    cost_df, _ = fetch_cost_data(token)
    return id_pool, cost_df, None

id_pool, cost_df, warn = load_data()
if warn:
    st.error(warn)
    st.stop()
if not id_pool:
    st.error("No Reported as Finished orders found.")
    st.stop()

display_df = build_display(id_pool, cost_df)

col1, col2 = st.columns([1, 9])
with col1:
    lo = st.number_input("Min dev %", value=_lo_default, step=1.0, format="%.1f")
with col2:
    hi = st.number_input("Max dev %", value=_hi_default, step=1.0, format="%.1f")

st.query_params["lo"] = str(lo)
st.query_params["hi"] = str(hi)

components.html(f"""
<script>
(function(){{
  try {{
    var parent = window.parent;
    var p = new URLSearchParams(parent.location.search);
    if (p.has('lo')) {{
      parent.localStorage.setItem('dev_lo', p.get('lo'));
      parent.localStorage.setItem('dev_hi', p.get('hi') || '10.0');
    }} else {{
      var slo = parent.localStorage.getItem('dev_lo');
      var shi = parent.localStorage.getItem('dev_hi');
      if (slo !== null && shi !== null) {{
        parent.location.href = parent.location.pathname + '?lo=' + slo + '&hi=' + shi;
      }}
    }}
  }} catch(e) {{}}
}})();
</script>
""", height=1)

filtered = display_df[(display_df["Deviation %"] < lo) | (display_df["Deviation %"] > hi)].reset_index(drop=True)

search = st.text_input("🔍 Search order or pool", placeholder="e.g. 110170 or 1001")
if search:
    s = search.lower()
    filtered = filtered[
        filtered["ProdId"].str.lower().str.contains(s, na=False) |
        filtered["Pool"].astype(str).str.lower().str.contains(s, na=False)
    ].reset_index(drop=True)

total     = len(display_df)
out_range = len(filtered)
st.caption(f"Showing {out_range} orders outside [{lo:.1f}%, {hi:.1f}%] of {total} total · Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

rb_col, ex_col, _ = st.columns([1, 1, 8])
with rb_col:
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()
with ex_col:
    st.download_button(
        label="⬇️ Export to Excel",
        data=to_excel(filtered),
        file_name=f"cost_deviation_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

components.html(render_table(filtered, D365_LIST_URL),
                height=min(80 + len(filtered) * 34, 800), scrolling=True)
