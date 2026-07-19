# -*- coding: utf-8 -*-
"""
Depolama katmani: Supabase varsa Supabase, yoksa lokal CSV (test modu).
Tablolar: trades (pozisyonlar + kapanmislar), plans (tarama ciktilari).
"""
import os, json
from datetime import datetime, timezone
import pandas as pd

LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_TRADES = os.path.join(LOCAL_DIR, "local_trades.csv")
LOCAL_PLANS = os.path.join(LOCAL_DIR, "plans")

TRADE_COLS = ["id", "sym", "entry", "stop", "opened_at", "status",
              "partial_price", "partial_pct", "exit_price", "exit_reason",
              "closed_at", "r", "note"]


def _sb():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not (url and key):
        try:
            import streamlit as st
            url = st.secrets.get("SUPABASE_URL")
            key = st.secrets.get("SUPABASE_KEY")
        except Exception:
            pass
    if url and key:
        from supabase import create_client
        return create_client(url, key)
    return None


def backend_name():
    return "Supabase" if _sb() else "LOKAL CSV (test modu — Supabase secrets girilmemis)"


# ---------- TRADES ----------

def open_position(sym, entry, stop, note=""):
    row = dict(sym=sym.upper(), entry=float(entry), stop=float(stop),
               opened_at=datetime.now(timezone.utc).isoformat(), status="open",
               note=note)
    sb = _sb()
    if sb:
        sb.table("trades").insert(row).execute()
    else:
        df = _local_trades()
        row["id"] = (df["id"].max() + 1) if len(df) else 1
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(LOCAL_TRADES, index=False)


def get_trades(status=None):
    sb = _sb()
    if sb:
        q = sb.table("trades").select("*")
        if status:
            q = q.eq("status", status)
        df = pd.DataFrame(q.order("opened_at", desc=True).execute().data)
    else:
        df = _local_trades()
        if status and len(df):
            df = df[df["status"] == status]
    return df if len(df) else pd.DataFrame(columns=TRADE_COLS)


def partial_exit(trade_id, price, pct):
    _update(trade_id, dict(partial_price=float(price), partial_pct=float(pct)))


def close_position(trade_id, price, reason):
    df = get_trades()
    row = df[df["id"] == trade_id].iloc[0]
    entry, stop = float(row["entry"]), float(row["stop"])
    runit = entry - stop
    rem = 1.0
    r = 0.0
    if pd.notna(row.get("partial_price")) and row.get("partial_price"):
        pct = float(row["partial_pct"]) / 100.0
        r += pct * (float(row["partial_price"]) - entry) / runit
        rem -= pct
    r += rem * (float(price) - entry) / runit
    _update(trade_id, dict(status="closed", exit_price=float(price),
                           exit_reason=reason, r=round(r, 3),
                           closed_at=datetime.now(timezone.utc).isoformat()))


def _update(trade_id, fields):
    sb = _sb()
    if sb:
        sb.table("trades").update(fields).eq("id", int(trade_id)).execute()
    else:
        df = _local_trades()
        for k, v in fields.items():
            df.loc[df["id"] == trade_id, k] = v
        df.to_csv(LOCAL_TRADES, index=False)


def _local_trades():
    if os.path.exists(LOCAL_TRADES):
        return pd.read_csv(LOCAL_TRADES)
    return pd.DataFrame(columns=TRADE_COLS)


# ---------- PLANS ----------

def save_plan(plan):
    sb = _sb()
    if sb:
        sb.table("plans").upsert(
            dict(for_day=plan["for_day"], kind=plan["kind"],
                 payload=json.dumps(plan, ensure_ascii=False)),
            on_conflict="for_day,kind").execute()
    os.makedirs(LOCAL_PLANS, exist_ok=True)
    with open(os.path.join(LOCAL_PLANS, f"plan_{plan['for_day']}_{plan['kind']}.json"),
              "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)


def get_latest_plans(n=4):
    sb = _sb()
    out = []
    if sb:
        rows = (sb.table("plans").select("*").order("for_day", desc=True)
                .limit(n).execute().data)
        out = [json.loads(r["payload"]) for r in rows]
    else:
        import glob
        for f in sorted(glob.glob(os.path.join(LOCAL_PLANS, "plan_*.json")),
                        reverse=True)[:n]:
            try:
                out.append(json.load(open(f, encoding="utf-8")))
            except Exception:
                pass
    return out
