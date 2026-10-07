"""Data layer + all calculations (no Streamlit here, so it is easy to test)."""
import datetime as dt, io, re
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

TZ = ZoneInfo("Asia/Dhaka")
SCHEMA = {
    "Settings": ["Key", "Value"],
    "Machines": ["Unit", "Machine", "Type", "Capacity/Day"],
    "Processes": ["Unit", "Category", "Process", "Machines", "Capacity/Day"],
    "Wash_Plan": ["Date", "Unit", "Machine", "Lot No", "Buyer", "Style", "PO", "Plan Pcs", "Sched From", "Sched To"],
    "Wash_Output": ["Date", "Unit", "Machine", "Lot No", "Hour", "Output Pcs"],
    "Downtime": ["Date", "Unit", "Machine", "From", "To", "Reason"],
    "Quality": ["Date", "Unit", "Machine", "Lot No", "Issue", "Pcs", "Rewash"],
    "Variation_Action": ["Date", "Unit", "Machine", "Reason", "Action", "Owner"],
    "Dry_Plan": ["Date", "Unit", "Process", "Plan Pcs"],
    "Dry_Output": ["Date", "Unit", "Process", "Hour", "Output Pcs"],
    "Style_Plan": ["Date", "Buyer", "Style", "PO", "Plan Pcs", "Available Input", "Opening WIP"],
    "Style_Hourly": ["Date", "Buyer", "Style", "PO", "Hour", "Hourly Plan", "Hourly Output"],
    "Sewing": ["Date", "Line", "Style", "Hour", "Target", "Actual", "Operators", "SMV", "Action"],
}
HM = {"Hour", "Sched From", "Sched To", "From", "To"}
NUM = {"Capacity/Day", "Machines", "Plan Pcs", "Output Pcs", "Pcs", "Hourly Plan", "Hourly Output",
       "Available Input", "Opening WIP", "Target", "Actual", "Operators", "SMV"}
TXN = [t for t, c in SCHEMA.items() if "Date" in c]


def hm(v):
    """'13:00', '1:00 PM', time(), 0.54 (sheet fraction) or 13 -> decimal hour."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v == "":
        return np.nan
    if isinstance(v, (dt.time, dt.datetime)):
        return v.hour + v.minute / 60
    if isinstance(v, (int, float)):
        return float(v) if v >= 1 else v * 24
    m = re.match(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)?", str(v).strip().lower())
    if not m:
        return np.nan
    h, mi = int(m[1]), int(m[2] or 0)
    if m[3] == "pm" and h < 12: h += 12
    if m[3] == "am" and h == 12: h = 0
    return h + mi / 60


def fh(x):
    return "?" if pd.isna(x) else f"{int(x):02d}:{int(round((x % 1) * 60)):02d}"


def _s(v):
    if pd.isna(v): return ""
    return str(int(v)) if isinstance(v, float) and v == int(v) else str(v).strip()


def clean(df, tab):
    cols = SCHEMA[tab]
    df = df.copy()
    df.columns = [str(x).strip() for x in df.columns]
    df = df.reindex(columns=cols).dropna(how="all")
    for k in cols:
        if k == "Date": df[k] = pd.to_datetime(df[k], errors="coerce", format="mixed").dt.date
        elif k in HM: df[k] = df[k].map(hm)
        elif k in NUM: df[k] = pd.to_numeric(df[k].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
        else: df[k] = df[k].map(_s).astype(str)
    if "Date" in cols: df = df[df["Date"].notna()]
    return df.reset_index(drop=True)


class Store:
    """Google Sheet if secrets are set, otherwise a local Excel file (demo/offline)."""
    def __init__(self, secrets=None, local="Wash_Dashboard_Template.xlsx"):
        self.local, self.sh, self.sid = local, None, None
        if secrets and "sheet_id" in secrets and "gcp_service_account" not in secrets:
            self.sid = str(secrets["sheet_id"]).strip()      # simple mode: link-shared sheet, read-only
        if secrets and "gcp_service_account" in secrets and "sheet_id" in secrets:
            import gspread
            gc = gspread.service_account_from_dict(dict(secrets["gcp_service_account"]))
            self.sh = gc.open_by_key(secrets["sheet_id"])
    @property
    def mode(self): return "Google Sheet (live)" if (self.sh or self.sid) else f"Local file: {self.local}"

    def _raw(self, tab):
        if self.sid:
            try: return pd.read_csv(f"https://docs.google.com/spreadsheets/d/{self.sid}/gviz/tq?tqx=out:csv&sheet={tab}", dtype=str)
            except Exception: return pd.DataFrame()
        if self.sh:
            try: return pd.DataFrame(self.sh.worksheet(tab).get_all_records())
            except Exception: return pd.DataFrame()
        try: return pd.read_excel(self.local, sheet_name=tab)
        except Exception: return pd.DataFrame()

    def read_all(self):
        return {t: clean(self._raw(t), t) for t in SCHEMA}

    def drop_month(self, d, ym):
        """Remove rows of month ym ('2026-09') from the live Google Sheet tabs. Returns rows removed."""
        if not self.sh: raise RuntimeError("Auto-clear needs service-account mode. Download first, then delete that month's rows in Google Sheet manually.")
        n = 0
        for t in TXN:
            ws = self.sh.worksheet(t)
            rows = ws.get_all_values()
            if len(rows) < 2: continue
            keep = [r for r in rows[1:] if not str(r[0]).strip().startswith(ym)]
            n += len(rows) - 1 - len(keep)
            ws.clear()
            ws.update(range_name="A1", values=[rows[0]] + keep, value_input_option="USER_ENTERED")
        return n


def settings(d):
    s = dict(zip(d["Settings"]["Key"], d["Settings"]["Value"]))
    return int(s.get("Start Hour", 8) or 8), int(s.get("End Hour", 20) or 20)


def now_hour(date):
    """Decimal 'now' for the chosen date: today -> clock, past -> all elapsed, future -> none."""
    n = dt.datetime.now(TZ)
    if date == n.date(): return n.hour + n.minute / 60
    return 99.0 if date < n.date() else 0.0


def hlabel(h): return f"{int(h):02d}-{int(h) + 1:02d}"
def day(df, date, **eq):
    m = df["Date"] == date
    for k, v in eq.items(): m &= df[k] == v
    return df[m]


def plan_hourly(P, H0, H1, extra=()):
    """Spread each plan row's pcs evenly over its scheduled hours."""
    rows = []
    for r in P.to_dict("records"):
        f = H0 if pd.isna(r["Sched From"]) else r["Sched From"]
        t = H1 if pd.isna(r["Sched To"]) else r["Sched To"]
        if t <= f: t = f + 1
        hrs = list(range(int(f), int(np.ceil(t))))
        for h in hrs:
            rows.append({**{k: r[k] for k in ("Machine", "Lot No") + tuple(extra)}, "Hour": h, "Plan": r["Plan Pcs"] / len(hrs)})
    return pd.DataFrame(rows, columns=["Machine", "Lot No", *extra, "Hour", "Plan"])


def _down(D, h):
    return sum(max(0, min(t, h + 1) - max(f, h)) for f, t in zip(D["From"], D["To"])) * 60


def wash_summary(d, date, unit, now_h):
    H0, H1 = settings(d)
    m = d["Machines"].query("Unit==@unit").drop_duplicates("Machine")
    P, O = day(d["Wash_Plan"], date, Unit=unit), day(d["Wash_Output"], date, Unit=unit)
    D = day(d["Downtime"], date, Unit=unit).dropna(subset=["From", "To"])
    Q = day(d["Quality"], date, Unit=unit)
    ph = plan_hourly(P, H0, H1)
    lots = pd.concat([P.groupby(["Machine", "Lot No"])["Plan Pcs"].sum().rename("p"),
                      O.groupby(["Machine", "Lot No"])["Output Pcs"].sum().rename("o")], axis=1).fillna(0).astype(float)
    done = lots[(lots.p > 0) & (lots.o >= lots.p)]
    D = D.assign(dur=(D["To"] - D["From"]).clip(lower=0))
    Qr = Q[Q["Rewash"].astype(str).str.lower().isin(["yes", "y", "true", "1"])]
    sched = {mc: "; ".join(f"{fh(r['Sched From'])}-{fh(r['Sched To'])} {r['Lot No']}:{r['Plan Pcs']:,.0f}" for r in g.to_dict("records"))
             for mc, g in P.groupby("Machine")}
    dtxt = {mc: ", ".join(f"{fh(a)}-{fh(b)}" for a, b in zip(g["From"], g["To"])) for mc, g in D.groupby("Machine")}
    names = list(dict.fromkeys(list(m.Machine) + [x for f in (P, O, D, Q) for x in f.Machine]))
    df = pd.DataFrame({"Machine": names})
    mp = lambda s, fill=0: df.Machine.map(s).fillna(fill)
    df["Cap/Day"] = mp(m.set_index("Machine")["Capacity/Day"])
    df["Plan Lots"] = mp(P.groupby("Machine")["Lot No"].nunique())
    df["Plan Pcs"] = mp(P.groupby("Machine")["Plan Pcs"].sum())
    df["Done Lots"] = mp(done.groupby(level=0).size())
    df["Done Pcs"] = mp(done.groupby(level=0)["o"].sum())
    df["Output Pcs"] = mp(O.groupby("Machine")["Output Pcs"].sum())
    pn = ph.assign(v=ph.Plan * np.clip(now_h - ph.Hour, 0, 1)).groupby("Machine")["v"].sum()
    df["Plan Till Now"] = mp(pn).round(0)
    df["Variation"] = df["Output Pcs"] - df["Plan Till Now"]
    df["WIP (Plan-Out)"] = (df["Plan Pcs"] - df["Output Pcs"]).clip(lower=0)
    df["Breakdown Hrs"] = mp(D.groupby("Machine")["dur"].sum()).round(2)
    df["Breakdown Time"] = df.Machine.map(dtxt).fillna("")
    df["Quality Pcs"] = mp(Q.groupby("Machine")["Pcs"].sum())
    df["Rewash Lots"] = mp(Qr.groupby("Machine")["Lot No"].nunique())
    df["Schedule"] = df.Machine.map(sched).fillna("")
    down_now = {r["Machine"] for r in D.to_dict("records") if r["From"] <= now_h < r["To"]}
    def status(r):
        if r.Machine in down_now: return "BREAKDOWN"
        if r["Plan Pcs"] == 0 and r["Output Pcs"] == 0: return "No plan"
        if r["Output Pcs"] >= r["Plan Pcs"] > 0: return "Complete"
        return "On track" if r["Output Pcs"] >= r["Plan Till Now"] - 0.5 else "Behind"
    df["Status"] = df.apply(status, axis=1)
    return df


def wash_hourly(d, date, unit, now_h, machine=None):
    H0, H1 = settings(d)
    P, O = day(d["Wash_Plan"], date, Unit=unit), day(d["Wash_Output"], date, Unit=unit)
    D = day(d["Downtime"], date, Unit=unit).dropna(subset=["From", "To"])
    if machine:
        P, O, D = (x[x.Machine == machine] for x in (P, O, D))
    O = O[O.Hour.notna()]
    ph = plan_hourly(P, H0, H1)
    t = pd.DataFrame({"Hour": range(H0, H1)})
    t["Plan"] = t.Hour.map(ph.groupby("Hour")["Plan"].sum()).fillna(0).round(0)
    t["Output"] = t.Hour.map(O.groupby(O.Hour.astype(int))["Output Pcs"].sum()).fillna(0)
    t["Breakdown Min"] = [round(_down(D, h)) for h in t.Hour]
    return _finish(t, now_h)


def _finish(t, now_h, plan="Plan", out="Output"):
    t = t.copy()
    t["Variation"] = t[out] - t[plan]
    t.loc[t.Hour > now_h, "Variation"] = np.nan          # future hours: no variation yet
    t["Cum Plan"], t["Cum Output"] = t[plan].cumsum(), t[out].cumsum()
    t["Slot"] = t.Hour.map(hlabel)
    return t


def dry_summary(d, date, category, unit, now_h):
    H0, H1 = settings(d)
    pr = d["Processes"].query("Unit==@unit and Category==@category").drop_duplicates("Process")
    P, O = day(d["Dry_Plan"], date, Unit=unit), day(d["Dry_Output"], date, Unit=unit)
    df = pr[["Process", "Machines", "Capacity/Day"]].reset_index(drop=True)
    df["Cap/Hr"] = (df["Capacity/Day"] / (H1 - H0)).round(0)
    df["Plan Pcs"] = df.Process.map(P.groupby("Process")["Plan Pcs"].sum()).fillna(0)
    df["Output Pcs"] = df.Process.map(O.groupby("Process")["Output Pcs"].sum()).fillna(0)
    el = float(np.clip(now_h - H0, 0, H1 - H0))
    df["Cap Till Now"] = (df["Cap/Hr"] * el).round(0)
    df["Util % (till now)"] = np.where(df["Cap Till Now"] > 0, df["Output Pcs"] / df["Cap Till Now"] * 100, 0).round(0)
    df["Plan Till Now"] = (df["Plan Pcs"] * el / (H1 - H0)).round(0)
    df["Variation"] = df["Output Pcs"] - df["Plan Till Now"]
    df["WIP (Plan-Out)"] = (df["Plan Pcs"] - df["Output Pcs"]).clip(lower=0)
    return df


def dry_hourly(d, date, unit, process, now_h):
    H0, H1 = settings(d)
    cap = d["Processes"].query("Unit==@unit and Process==@process")["Capacity/Day"].sum()
    plan = day(d["Dry_Plan"], date, Unit=unit, Process=process)["Plan Pcs"].sum()
    O = day(d["Dry_Output"], date, Unit=unit, Process=process)
    O = O[O.Hour.notna()]
    t = pd.DataFrame({"Hour": range(H0, H1)})
    t["Capacity"] = round(cap / (H1 - H0))
    t["Plan"] = round(plan / (H1 - H0))
    t["Output"] = t.Hour.map(O.groupby(O.Hour.astype(int))["Output Pcs"].sum()).fillna(0)
    t = _finish(t, now_h)
    t["Util %"] = np.where(t.Capacity > 0, t.Output / t.Capacity * 100, 0).round(0)
    t.loc[t.Hour > now_h, "Util %"] = np.nan
    return t


def style_summary(d, date, now_h):
    H0, H1 = settings(d)
    key = ["Buyer", "Style", "PO"]
    P, S = day(d["Style_Plan"], date), day(d["Style_Hourly"], date)
    df = P.groupby(key, as_index=False)[["Plan Pcs", "Available Input", "Opening WIP"]].sum()
    s = S.assign(pn=S["Hourly Plan"] * np.clip(now_h - S.Hour, 0, 1)).groupby(key)[["Hourly Output", "pn"]].sum()
    df = df.merge(s, on=key, how="left").fillna(0).rename(columns={"Hourly Output": "Output Pcs", "pn": "Hourly Plan Till Now"})
    df["Hourly Plan Till Now"] = df["Hourly Plan Till Now"].round(0)
    df["Variation"] = df["Output Pcs"] - df["Hourly Plan Till Now"]
    df["WIP Increase"] = df["Plan Pcs"] - df["Output Pcs"]
    df["Total WIP"] = df["Opening WIP"] + df["WIP Increase"]
    return df


def style_hourly(d, date, buyer, style, po, now_h):
    H0, H1 = settings(d)
    S = day(d["Style_Hourly"], date, Buyer=buyer, Style=style, PO=po)
    S = S[S.Hour.notna()]
    t = pd.DataFrame({"Hour": range(H0, H1)})
    t["Hourly Plan"] = t.Hour.map(S.groupby(S.Hour.astype(int))["Hourly Plan"].sum()).fillna(0)
    t["Hourly Output"] = t.Hour.map(S.groupby(S.Hour.astype(int))["Hourly Output"].sum()).fillna(0)
    return _finish(t, now_h, "Hourly Plan", "Hourly Output")


def sewing_hourly(d, date):
    S = day(d["Sewing"], date)
    S = S[S.Hour.notna()].copy()
    S["Eff %"] = np.where(S.Operators > 0, S.Actual * S.SMV / (S.Operators * 60) * 100, np.nan).round(1)
    S["Variation"] = S.Actual - S.Target
    S["Slot"] = S.Hour.map(hlabel)
    return S.sort_values(["Line", "Hour"])


def sewing_lines(S):
    g = S.assign(m=S.Actual * S.SMV, av=S.Operators * 60).groupby("Line")
    df = g.agg(Style=("Style", "last"), Target=("Target", "sum"), Actual=("Actual", "sum"), m=("m", "sum"), av=("av", "sum")).reset_index()
    df["Variation"] = df.Actual - df.Target
    df["Eff %"] = np.where(df.av > 0, df.m / df.av * 100, np.nan).round(1)
    return df.drop(columns=["m", "av"])


def month_export(d, ym):
    """Excel bytes: every entry tab filtered to month ym (day-wise, hourly rows)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for t in TXN:
            df = d[t][d[t]["Date"].map(lambda x: str(x).startswith(ym))].sort_values("Date")
            df.to_excel(w, sheet_name=t, index=False)
    return buf.getvalue()
