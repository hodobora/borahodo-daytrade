# -*- coding: utf-8 -*-
"""
TradingView opsiyon verisi — CANLI (kullanicinin TV oturumu ile, update_mode: streaming).
scanner.tradingview.com/options/scan · index_filters: underlying_symbol
yfinance'e gore avantaj: sembol basina TEK istek, rate limit yok, delta/IV hazir.
Oturum duserse cagiran taraf yfinance'e geri doner.
"""
import os
from datetime import date, datetime
import pandas as pd
import requests

_UA = {"user-agent": "Mozilla/5.0"}
_resolve_cache = {}


def sessionid():
    v = os.environ.get("TV_SESSIONID")
    if not v:
        try:
            import streamlit as st
            v = st.secrets.get("TV_SESSIONID")
        except Exception:
            v = None
    return v


def resolve(sym):
    """IREN -> NASDAQ:IREN (TV tam sembol). Hisse taramasindan cozer, cache'ler."""
    if sym in _resolve_cache:
        return _resolve_cache[sym]
    from tradingview_screener import Query, col
    try:
        _, df = (Query().set_markets("america").select("name")
                 .where(col("name") == sym).limit(3)
                 .get_scanner_data(cookies={"sessionid": sessionid()} if sessionid() else None))
        full = df["ticker"].iloc[0] if len(df) else None
    except Exception:
        full = None
    _resolve_cache[sym] = full
    return full


def _scan(payload):
    sid = sessionid()
    if not sid:
        raise RuntimeError("TV_SESSIONID yok")
    r = requests.post("https://scanner.tradingview.com/options/scan", json=payload,
                      cookies={"sessionid": sid}, headers=_UA, timeout=20)
    r.raise_for_status()
    return r.json().get("data") or []


def chain(sym, kind="put", dte_lo=1, dte_hi=40):
    """Underlying'in kontratlari (DTE penceresi) — DataFrame veya None.
    Kolonlar: strike, expiry(date), bid, ask, mid, delta, impliedVolatility, spread_pct
    """
    full = resolve(sym)
    if not full:
        return None
    today = date.today()
    lo = int((today.strftime("%Y%m%d")))
    hi_d = today.toordinal() + dte_hi
    hi = int(date.fromordinal(hi_d).strftime("%Y%m%d"))
    payload = {
        "columns": ["name", "option-type", "strike", "expiration", "bid", "ask", "delta", "iv"],
        "index_filters": [{"name": "underlying_symbol", "values": [full]}],
        "filter": [
            {"left": "option-type", "operation": "equal", "right": "put" if kind in ("put", "CSP") else "call"},
            {"left": "expiration", "operation": "in_range", "right": [lo, hi]},
        ],
        "range": [0, 400],
    }
    try:
        data = _scan(payload)
    except Exception:
        return None
    rows = []
    for it in data:
        n, typ, K, exp, bid, ask, delta, iv = it["d"]
        if bid is None or ask is None or not bid and not ask:
            continue
        expd = datetime.strptime(str(exp), "%Y%m%d").date()
        dte = (expd - today).days
        if not (dte_lo <= dte <= dte_hi):
            continue
        mid = (float(bid) + float(ask)) / 2
        if mid <= 0:
            continue
        rows.append(dict(strike=float(K), expiry=expd, bid=float(bid), ask=float(ask),
                         mid=mid, delta=float(delta) if delta is not None else None,
                         impliedVolatility=float(iv) if iv is not None else None,
                         spread_pct=(float(ask) - float(bid)) / mid * 100))
    if not rows:
        return None
    return pd.DataFrame(rows)


def quote(sym, expiry, strike, kind="CSP"):
    """Tek kontratin canli mid'i — pozisyon takibi icin. None donerse cagiran yf'e duser."""
    try:
        df = chain(sym, "put" if kind == "CSP" else "call", 0, 60)
        if df is None:
            return None
        expd = pd.Timestamp(expiry).date()
        row = df[(df["expiry"] == expd) & (df["strike"] == float(strike))]
        if len(row):
            return round(float(row["mid"].iloc[0]), 2)
    except Exception:
        pass
    return None
