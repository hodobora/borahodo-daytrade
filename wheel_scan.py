# -*- coding: utf-8 -*-
"""
WHEEL taramasi — cash-secured put adaylari.
Veri: yfinance (opsiyon zinciri ~15dk gecikmeli). Nihai fiyat IBKR'de emir aninda teyit edilir.
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


def _bs_put(S, K, T, sig):
    if T <= 0 or sig <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (R + sig * sig / 2) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    return K * math.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def scan_one(tk, cash, delta_lo=-0.32, delta_hi=-0.18, dte_lo=7, dte_hi=24,
             max_spread=10.0, min_oi=100):
    """Tek sembol icin en iyi CSP adayini dondurur (dict) veya None/str(hata)."""
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
    exps = [e for e in t.options
            if dte_lo <= (pd.Timestamp(e).date() - today).days <= dte_hi]
    if not exps:
        return f"{tk}: uygun vade yok"
    # bilanço vadeden önceyse o vadeyi atla
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
    p = p[(p["delta"] >= delta_lo) & (p["delta"] <= delta_hi)]
    p = p[p["strike"] * 100 <= cash]
    p = p[p["openInterest"].fillna(0) >= min_oi]
    if p.empty:
        return f"{tk}: filtre sonrasi aday yok (nakit/OI/delta)"
    p["spread_pct"] = (p["ask"] - p["bid"]) / p["mid"] * 100
    p = p[p["spread_pct"] <= max_spread]
    if p.empty:
        return f"{tk}: spread genis"
    p["yield_pct"] = p["mid"] / p["strike"] * 100
    dte = max(T * 365, 1)
    p["wk_yield"] = p["yield_pct"] / dte * 7
    best = p.nlargest(1, "wk_yield").iloc[0]
    ivrv = float(best["impliedVolatility"]) / rv20 if rv20 > 0 else np.nan
    return dict(
        sym=tk, spot=round(S, 2), expiry=exp, dte=int(dte),
        strike=float(best["strike"]), bid=float(best["bid"]), ask=float(best["ask"]),
        mid=round(float(best["mid"]), 2), delta=round(float(best["delta"]), 2),
        yield_pct=round(float(best["yield_pct"]), 2),
        wk_yield=round(float(best["wk_yield"]), 2),
        iv=round(float(best["impliedVolatility"]), 2), rv20=round(rv20, 2),
        iv_rv=round(ivrv, 2), spread_pct=round(float(best["spread_pct"]), 1),
        oi=int(best["openInterest"]), collateral=int(best["strike"] * 100),
        earnings=str(edate) if edate else "?",
        earn_flag="⚠️ bilanço pencerede" if earn_in_win else "",
        # bir sonraki bilanço 75+ gün uzaktaysa bir önceki yeni geçmiş demektir (çeyrek ~91g):
        # IV hâlâ şişkinse crush sonrası prim penceresi
        crush_flag=("🎯 IV-crush penceresi" if (edate is not None
                    and (edate - today).days > 75 and ivrv > 1.1) else ""),
        breakeven=round(float(best["strike"]) - float(best["mid"]), 2),
        order=f"SELL 1 {tk} {exp} {best['strike']:g}P @ limit {float(best['mid']):.2f}",
        atm_iv=round(float(iv.iloc[(K - S).abs().argmin()]), 3) if len(K) else None,
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
                      + np.where(df.get("crush_flag", "") != "", 0.5, 0))
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
