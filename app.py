"""Wash / Dry / Style / Sewing floor dashboard.  Run:  streamlit run app.py"""
import datetime as dt
import pandas as pd, streamlit as st
import core as c

st.set_page_config(page_title="Floor Monitor", page_icon="🏭", layout="wide", initial_sidebar_state="collapsed")
st.markdown("<style>.block-container{padding:.8rem .7rem} [data-testid=stMetricValue]{font-size:1.35rem}</style>", unsafe_allow_html=True)


@st.cache_resource
def store():
    try: sec = dict(st.secrets)
    except Exception: sec = {}
    return c.Store(sec)

@st.cache_data(ttl=60, show_spinner="Loading data...")
def load(): return store().read_all()


def show(df, neg=(), status=False, **kw):
    """Dataframe with red/green variation and coloured status."""
    s = df.style.format(precision=0, thousands=",", na_rep="")
    mp = (s.map if hasattr(s, "map") else s.applymap)
    f = lambda v: "color:#c0392b;font-weight:600" if isinstance(v, (int, float)) and v < 0 else ("color:#1e8449" if isinstance(v, (int, float)) and v > 0 else "")
    cols = [x for x in neg if x in df.columns]
    if cols: s = mp(f, subset=cols); mp = (s.map if hasattr(s, "map") else s.applymap)
    if status and "Status" in df:
        col = {"BREAKDOWN": "#e74c3c;color:white", "Behind": "#f5b7b1", "On track": "#d5f5e3", "Complete": "#abebc6"}
        s = mp(lambda v: f"background-color:{col.get(v, '')}" if v in col else "", subset=["Status"])
    st.dataframe(s, use_container_width=True, hide_index=True, **kw)


def plan_vs_out(t, plan="Plan", out="Output"):
    st.bar_chart(t.set_index("Slot")[[plan, out]], height=240)


def kpis(items):
    for col, (k, v) in zip(st.columns(len(items)), items): col.metric(k, f"{v:,.0f}" if isinstance(v, (int, float)) else v)


d = load()
H0, H1 = c.settings(d)
top = st.columns([2, 1, 3])
date = top[0].date_input("Date", dt.datetime.now(c.TZ).date())
if top[1].button("🔄 Refresh"): st.cache_data.clear(); st.rerun()
top[2].caption(f"Source: {store().mode} · Working hours {H0:02d}:00–{H1:02d}:00 · auto-cache 60s")
nh = c.now_hour(date)
tabs = st.tabs(["🧺 Wash (machine-wise)", "🔥 Dry / Hydro / Dryer", "👖 Style tracking", "🧵 Sewing hourly", "🗄️ Monthly archive"])

# ---------------- WASH ----------------
with tabs[0]:
    units = sorted(set(d["Machines"].Unit) | set(d["Wash_Plan"].Unit), key=lambda u: list(d["Machines"].Unit.unique()).index(u) if u in set(d["Machines"].Unit) else 99)
    unit = st.selectbox("Wash unit", units, key="wu")
    s = c.wash_summary(d, date, unit, nh)
    only = st.toggle("Only machines with plan / output / breakdown", True)
    v = s[(s["Plan Pcs"] > 0) | (s["Output Pcs"] > 0) | (s["Breakdown Hrs"] > 0) | (s["Quality Pcs"] > 0)] if only else s
    kpis([("Plan lots", s["Plan Lots"].sum()), ("Plan pcs", s["Plan Pcs"].sum()), ("Lots done", s["Done Lots"].sum()),
          ("Output pcs", s["Output Pcs"].sum()), ("Var vs plan-till-now", s["Variation"].sum()), ("WIP", s["WIP (Plan-Out)"].sum()),
          ("Breakdown hrs", s["Breakdown Hrs"].sum()), ("Rewash lots", s["Rewash Lots"].sum())])
    st.caption(f"Unit capacity/day: {s['Cap/Day'].sum():,.0f} pcs · machines {len(s)} · running status: {s.Status.value_counts().to_dict()}")
    h = c.wash_hourly(d, date, unit, nh)
    with st.expander("Unit hourly plan vs output", True):
        plan_vs_out(h); show(h[["Slot", "Plan", "Output", "Variation", "Breakdown Min", "Cum Plan", "Cum Output"]], neg=["Variation"])
    st.subheader("Machine-wise monitor")
    show(v.drop(columns=["Cap/Day"]), neg=["Variation"], status=True)
    if len(v):
        mc = st.selectbox("Machine detail", list(v.Machine), key="wm")
        r = s[s.Machine == mc].iloc[0]
        st.markdown(f"**{unit} {mc}** — Plan: **{r['Plan Lots']:.0f} lots / {r['Plan Pcs']:,.0f} pcs** · Done: **{r['Done Lots']:.0f} lots / {r['Done Pcs']:,.0f} pcs** · "
                    f"Output: **{r['Output Pcs']:,.0f}** · WIP: **{r['WIP (Plan-Out)']:,.0f}** · Breakdown: **{r['Breakdown Hrs']:.1f} h** ({r['Breakdown Time'] or '-'}) · Status: **{r.Status}**")
        st.caption(f"Schedule: {r.Schedule or '-'}")
        mh = c.wash_hourly(d, date, unit, nh, mc)
        plan_vs_out(mh); show(mh[["Slot", "Plan", "Output", "Variation", "Breakdown Min", "Cum Plan", "Cum Output"]], neg=["Variation"])
        a, b, q = st.columns(3)
        k = dict(Unit=unit)
        for box, tab, title in ((a, "Downtime", "Breakdown / reason"), (b, "Quality", "Quality & rewash"), (q, "Variation_Action", "Variation reason & action")):
            x = c.day(d[tab], date, **k); x = x[x.Machine == mc]
            box.markdown(f"**{title}**"); box.dataframe(x.drop(columns=["Date", "Unit", "Machine"]).assign(**({"From": x.From.map(c.fh), "To": x.To.map(c.fh)} if tab == "Downtime" else {})), hide_index=True, use_container_width=True)

# ---------------- DRY / HYDRO / DRYER ----------------
with tabs[1]:
    cat = st.radio("Process group", ["Dry", "Hydro", "Dryer"], horizontal=True)
    du = sorted(d["Processes"].query("Category==@cat").Unit.unique())
    unit = st.selectbox("Unit", du, key="du") if du else None
    if unit:
        s = c.dry_summary(d, date, cat, unit, nh)
        kpis([("Day capacity", s["Capacity/Day"].sum()), ("Today plan", s["Plan Pcs"].sum()), ("Output", s["Output Pcs"].sum()),
              ("Variation", s["Variation"].sum()), ("WIP", s["WIP (Plan-Out)"].sum())])
        show(s.sort_values("Plan Pcs", ascending=False), neg=["Variation"])
        pr = st.selectbox("Process hourly view", list(s.Process), key="dp")
        t = c.dry_hourly(d, date, unit, pr, nh)
        st.markdown(f"**{pr}** — capacity/hr **{t.Capacity.iloc[0]:,.0f}** · plan today **{t.Plan.sum():,.0f}** · output **{t.Output.sum():,.0f}**")
        plan_vs_out(t, "Capacity", "Output"); show(t[["Slot", "Capacity", "Plan", "Output", "Util %", "Variation", "Cum Plan", "Cum Output"]], neg=["Variation"])

# ---------------- STYLE ----------------
with tabs[2]:
    s = c.style_summary(d, date, nh)
    if s.empty: st.info("No Style_Plan rows for this date.")
    else:
        kpis([("Plan", s["Plan Pcs"].sum()), ("Output", s["Output Pcs"].sum()), ("WIP increase", s["WIP Increase"].sum()),
              ("Available input", s["Available Input"].sum()), ("Total WIP", s["Total WIP"].sum())])
        show(s, neg=["Variation"])
        lab = s.Buyer + " | " + s.Style + "/" + s.PO
        i = st.selectbox("Style / PO hourly", range(len(s)), format_func=lambda i: lab[i])
        r = s.iloc[i]; t = c.style_hourly(d, date, r.Buyer, r.Style, r.PO, nh)
        st.markdown(f"**Buyer {r.Buyer} · Style/PO {r.Style}/{r.PO}** — Plan **{r['Plan Pcs']:,.0f}** · Output **{r['Output Pcs']:,.0f}** · "
                    f"WIP increase **{r['WIP Increase']:,.0f}** · Available input **{r['Available Input']:,.0f}** · Total WIP **{r['Total WIP']:,.0f}**")
        plan_vs_out(t, "Hourly Plan", "Hourly Output"); show(t[["Slot", "Hourly Plan", "Hourly Output", "Variation", "Cum Plan", "Cum Output"]], neg=["Variation"])

# ---------------- SEWING ----------------
with tabs[3]:
    S = c.sewing_hourly(d, date)
    if S.empty: st.info("No Sewing rows for this date.")
    else:
        L = c.sewing_lines(S)
        kpis([("Target", L.Target.sum()), ("Actual", L.Actual.sum()), ("Variation", L.Variation.sum()), ("Avg Eff %", f"{L['Eff %'].mean():.1f}")])
        show(L, neg=["Variation"])
        st.markdown("**Hour-wise variation (all lines)**")
        show(S.pivot_table(index="Line", columns="Slot", values="Variation", aggfunc="sum").reset_index(), neg=list(S.Slot.unique()))
        ln = st.selectbox("Line detail", list(L.Line))
        x = S[S.Line == ln]
        plan_vs_out(x, "Target", "Actual"); show(x[["Slot", "Style", "Target", "Actual", "Variation", "Operators", "SMV", "Eff %", "Action"]], neg=["Variation"])

# ---------------- ARCHIVE ----------------
with tabs[4]:
    st.write("Month-end: download day-wise hourly records, then (Google Sheet mode) clear that month from the live sheet.")
    ym = st.text_input("Month (yyyy-mm)", (dt.datetime.now(c.TZ).date().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m"))
    n = sum(d[t]["Date"].map(lambda x: str(x).startswith(ym)).sum() for t in c.TXN)
    st.caption(f"{n:,} rows found for {ym}")
    got = st.download_button("⬇️ Download month records (Excel)", c.month_export(d, ym), f"Records_{ym}.xlsx",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", disabled=n == 0)
    if got: st.session_state["dl"] = ym
    got = st.session_state.get("dl") == ym
    st.warning("Clearing permanently removes those rows from the live Google Sheet. Keep the downloaded file safe first.")
    ok = st.text_input(f"Type  CLEAR {ym}  to confirm")
    if st.button("🗑️ Clear month from live sheet", disabled=(ok != f"CLEAR {ym}" or not got)):
        try: st.success(f"{store().drop_month(d, ym)} rows removed."); st.cache_data.clear()
        except Exception as e: st.error(e)
    if not got: st.caption("Download first — the clear button unlocks after the download is clicked.")
