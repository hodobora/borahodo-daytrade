# -*- coding: utf-8 -*-
"""
Wheel depolama: Supabase varsa Supabase (wheel_trades, iv_history), yoksa lokal CSV.
Supabase kurulumunda schema_wheel.sql calistirilmali.
"""
import os
from datetime import datetime, timezone
import pandas as pd

LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_WHEEL = os.path.join(LOCAL_DIR, "local_wheel.csv")
LOCAL_IV = os.path.join(LOCAL_DIR, "local_iv.csv")

WHEEL_COLS = ["id", "opened_at", "sym", "kind", "expiry", "strike", "qty", "fill",
              "premium", "collateral", "status", "close_price", "close_reason",
              "closed_at", "pnl", "note"]
LAST_ERROR = None


def _sb():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not (url and key):
        try:
            import streamlit as st
            url = st.secrets.get("SUPABASE_URL")
            key = st.secrets.get("SUPABASE_KEY")
        except Exception:
            return None
    if not (url and key):
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_wheel(status=None):
    global LAST_ERROR
    sb = _sb()
    if sb:
        try:
            q = sb.table("wheel_trades").select("*")
            if status:
                q = q.eq("status", status)
            data = q.order("opened_at", desc=True).execute().data
            LAST_ERROR = None
            return pd.DataFrame(data, columns=WHEEL_COLS if not data else None)
        except Exception as ex:
            LAST_ERROR = str(ex)
    if os.path.exists(LOCAL_WHEEL):
        df = pd.read_csv(LOCAL_WHEEL)
        if status is not None and "status" in df:
            df = df[df["status"] == status]
        return df
    return pd.DataFrame(columns=WHEEL_COLS)


def add_wheel(row: dict):
    global LAST_ERROR
    row = {**row, "opened_at": row.get("opened_at") or _now(), "status": "open"}
    sb = _sb()
    if sb:
        try:
            sb.table("wheel_trades").insert(row).execute()
            LAST_ERROR = None
            return True
        except Exception as ex:
            LAST_ERROR = str(ex)
    df = get_wheel()
    row["id"] = (df["id"].max() + 1) if len(df) and df["id"].notna().any() else 1
    pd.concat([df, pd.DataFrame([row])], ignore_index=True).to_csv(LOCAL_WHEEL, index=False)
    return True


def close_wheel(trade_id, close_price, reason, pnl):
    global LAST_ERROR
    patch = {"status": "closed", "close_price": close_price, "close_reason": reason,
             "closed_at": _now(), "pnl": pnl}
    sb = _sb()
    if sb:
        try:
            sb.table("wheel_trades").update(patch).eq("id", trade_id).execute()
            LAST_ERROR = None
            return True
        except Exception as ex:
            LAST_ERROR = str(ex)
    df = get_wheel()
    for k, v in patch.items():
        df.loc[df["id"] == trade_id, k] = v
    df.to_csv(LOCAL_WHEEL, index=False)
    return True


def log_iv(rows):
    """Tarama sirasinda ATM IV kaydi — zamanla kendi IV Rank verimiz olusur."""
    if not rows:
        return
    stamped = [{**r, "ts": _now()} for r in rows]
    sb = _sb()
    if sb:
        try:
            sb.table("iv_history").insert(stamped).execute()
            return
        except Exception:
            pass
    df = pd.DataFrame(stamped)
    header = not os.path.exists(LOCAL_IV)
    df.to_csv(LOCAL_IV, mode="a", index=False, header=header)


def get_iv_rank(sym, current_iv):
    """Biriken veriden IV Rank (yüzdelik). Veri azsa None."""
    sb = _sb()
    hist = None
    if sb:
        try:
            data = sb.table("iv_history").select("atm_iv").eq("sym", sym).execute().data
            hist = pd.DataFrame(data)
        except Exception:
            pass
    if hist is None and os.path.exists(LOCAL_IV):
        df = pd.read_csv(LOCAL_IV)
        hist = df[df["sym"] == sym]
    if hist is None or len(hist) < 20:
        return None
    vals = hist["atm_iv"].dropna().astype(float)
    return round(float((vals < current_iv).mean() * 100))


def backend_name():
    return "Supabase" if _sb() else "lokal CSV (gecici!)"
