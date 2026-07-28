# -*- coding: utf-8 -*-
"""
WHEEL taramasi — cash-secured put adaylari.
Veri: TradingView CANLI opsiyon zinciri (tv_options, oturum varsa) -> yfinance yedek.
Spot/RV/bilanço tarihi: yfinance gunluk bar (hafif, rate-limit riski dusuk).
"""
import math
from datetime import date
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm

R = 0.035

# Evren: yuksek-IV wheel adaylari (fintwit/onestoploss listesi) + likit orta-fiyat
# isimler + dusuk-beta denge bacaklari. Panelden duzenlenebilir.
UNIVERSE = [
    # yuksek IV / prim dolgun
    "IREN", "SOFI", "HIMS", "HOOD", "AAOI", "BROS", "RBLX", "RIVN", "MARA", "CLSK",
    # orta band
    "INTC", "UBER", "PYPL", "DKNG", "SBUX", "NKE", "GM", "DAL", "OXY", "CCJ",
    "CCL", "WBD", "SNAP", "F", "AAL", "T", "HPE", "KVUE",
    # dusuk-beta denge
    "BAC", "KO", "PFE", "VZ", "CSCO", "XOM",
]


def scan_one(tk, cash, delta_lo=-0.32, delta_hi=-0.18, dte_lo=7, dte_hi=24,
             max_spread=10.0, min_oi=100):
    """Tek sembol icin en iyi CSP adayini dondurur (dict) veya str(elenme sebebi)."""
    t = yf.Ticker(tk)
    px = t.history(period="4mo")["Close"]
    if len(px) < 40:
        return f"{tk}: fiyat verisi yok"
    S = float(px.iloc[-1])
    lr = np.log(px / px.shift(1)).dropna()
    rv20 = float(lr.tail(20).std() * np.sqrt(252))
    try:
        ed = t.calendar.get("Earnings Date")
        edate = ed[0] if isinstance(ed, list) and ed else None
    except Exception:
        edate = None
    today = date.today()

    # --- zincir: once TradingView canli, olmazsa yfinance ---
    p, src = None, None
    try:
        import tv_options
        tvdf = tv_options.chain(tk, "put", dte_lo, dte_hi)
    except Exception:
        tvdf = None
    if tvdf is not None and len(tvdf):
        exps = sorted(tvdf["expiry"].unique())
        clean = [e for e in exps if edate is None or edate > e]
        earn_in_win = not clean
        expd = (clean or exps)[0]
        p = tvdf[tvdf["expiry"] == expd].copy()
        p = p[p["delta"].notna() & p["impliedVolatility"].notna()]
        p["openInterest"] = np.nan  # TV bu uctan OI vermiyor; likidite = spread
        exp = str(expd)
        T = max((expd - today).days, 1) / 365
        src = "tv"
    else:
        exps = [e for e in t.options
                if dte_lo <= (pd.Timestamp(e).date() - today).days <= dte_hi]
        if not exps:
            return f"{tk}: uygun vade yok"
        clean = [e for e in exps if edate is None or edate > pd.Timestamp(e).date()]
        earn_in_win = not clean
        exp = (clean or exps)[0]
        T = (pd.Timestamp(exp).date() - today).days / 365
        try:
            p = t.option_chain(exp).puts
        except Exception as ex:
            return f"{tk}: zincir hatasi {ex}"
        p = p[(p["bid"] > 0) & (p["ask"] > 0)].copy()
        if p.empty:
            return f"{tk}: bos zincir"
        p["mid"] = (p["bid"] + p["ask"]) / 2
        iv = p["impliedVolatility"].astype(float)
        K = p["strike"].astype(float)
        d1 = (np.log(S / K) + (R + iv**2 / 2) * T) / (iv * np.sqrt(T))
        p["delta"] = norm.cdf(d1) - 1
        p["spread_pct"] = (p["ask"] - p["bid"]) / p["mid"] * 100
        src = "yf"

    # --- ortak filtreler ---
    p = p[(p["delta"] >= delta_lo) & (p["delta"] <= delta_hi)]
    p = p[p["strike"] * 100 <= cash]
    if p.empty:
        return f"{tk}: filtre sonrasi aday yok (nakit/delta)"
    oi = p["openInterest"]
    p = p[oi.isna() | (oi.fillna(0) >= min_oi)]
    p = p[p["spread_pct"] <= max_spread]
    if p.empty:
        return f"{tk}: spread genis"
    p["yield_pct"] = p["mid"] / p["strike"] * 100
    dte = max(T * 365, 1)
    p["wk_yield"] = p["yield_pct"] / dte * 7
    best = p.nlargest(1, "wk_yield").iloc[0]
    ivrv = float(best["impliedVolatility"]) / rv20 if rv20 > 0 else np.nan
    K_all = p["strike"].astype(float)
    atm_iv = float(p["impliedVolatility"].iloc[(K_all - S).abs().argmin()]) if len(p) else None
    return dict(
        sym=tk, spot=round(S, 2), expiry=str(exp)[:10], dte=int(dte),
        strike=float(best["strike"]), bid=float(best["bid"]), ask=float(best["ask"]),
        mid=round(float(best["mid"]), 2), delta=round(float(best["delta"]), 2),
        yield_pct=round(float(best["yield_pct"]), 2),
        wk_yield=round(float(best["wk_yield"]), 2),
        iv=round(float(best["impliedVolatility"]), 2), rv20=round(rv20, 2),
        iv_rv=round(ivrv, 2), spread_pct=round(float(best["spread_pct"]), 1),
        oi=int(best["openInterest"]) if not pd.isna(best["openInterest"]) else -1,
        collateral=int(best["strike"] * 100),
        earnings=str(edate) if edate else "?",
        earn_flag="⚠️ bilanço pencerede" if earn_in_win else "",
        crush_flag=("🎯 IV-crush penceresi" if (edate is not None
                    and (edate - today).days > 75 and ivrv > 1.1) else ""),
        breakeven=round(float(best["strike"]) - float(best["mid"]), 2),
        order=f"SELL 1 {tk} {str(exp)[:10]} {best['strike']:g}P @ limit {float(best['mid']):.2f}",
        atm_iv=round(atm_iv, 3) if atm_iv else None,
        src=src,
    )


def scan(universe, cash, **kw):
    """Evreni tarar; (DataFrame, notlar) dondurur. skor = VRP + getiri - spread cezasi."""
    rows, notes = [], []
    for tk in universe:
        try:
            r = scan_one(tk, cash, **kw)
        except Exception as ex:
            r = f"{tk}: {ex}"
        if isinstance(r, dict):
            rows.append(r)
        elif r:
            notes.append(r)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["skor"] = (df["iv_rv"].fillna(1) * 2 + df["wk_yield"] * 0.8
                      - df["spread_pct"] * 0.15
                      - np.where(df["earn_flag"] != "", 2.0, 0)
                      + np.where(df["crush_flag"] != "", 0.5, 0))
        df = df.sort_values("skor", ascending=False).reset_index(drop=True)
    return df, notes


def dynamic_universe(free_cash, min_avg_vol=2e6, min_mcap=2e9, top_n=50):
    """TradingView screener ile tum ABD piyasasindan kaba eleme (asama 1).
    Fiyat bandi serbest nakde gore: strike*100 <= nakit olabilmeli.
    Opsiyonu olmayan isimler asama 2'de kendiliginden elenir."""
    from tradingview_screener import Query, col
    price_hi = max(min(free_cash / 100 * 1.15, 400), 12)
    q = (Query().set_markets("america")
         .select("name", "close", "Volatility.M")
         .where(col("close").between(8, price_hi),
                col("average_volume_10d_calc") > min_avg_vol,
                col("market_cap_basic") > min_mcap,
                col("type") == "stock",
                col("is_primary") == True)
         .order_by("Volatility.M", ascending=False)
         .limit(top_n))
    _, df = q.get_scanner_data()
    return df["name"].tolist()
