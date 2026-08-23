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


def earnings_map(symbols):
    """Bilanço tarihleri — BİRİNCİL kaynak TradingView (tek toplu istek, cloud'da sağlam)."""
    from tradingview_screener import Query, col
    from datetime import datetime, timezone
    out = {}
    try:
        _, df = (Query().set_markets("america")
                 .select("name", "earnings_release_next_date")
                 .where(col("name").isin(list(symbols)))
                 .limit(len(symbols) + 10)
                 .get_scanner_data())
        for r in df.itertuples():
            v = r.earnings_release_next_date
            if v == v and v:  # NaN kontrolu
                out[r.name] = datetime.fromtimestamp(int(v), timezone.utc).date()
    except Exception:
        pass
    return out


def scan_one(tk, cash, delta_lo=-0.32, delta_hi=-0.18, dte_lo=7, dte_hi=24,
             max_spread=10.0, min_oi=100, edate=None, min_vrp=1.10, spy_lr=None):
    """Tek sembol icin en iyi CSP adayini dondurur (dict) veya str(elenme sebebi)."""
    t = yf.Ticker(tk)
    px = t.history(period="4mo")["Close"]
    if len(px) < 40:
        return f"{tk}: fiyat verisi yok"
    S = float(px.iloc[-1])
    day_chg = float(px.iloc[-1] / px.iloc[-2] - 1) * 100 if len(px) >= 2 else 0.0
    lr = np.log(px / px.shift(1)).dropna()
    rv20 = float(lr.tail(20).std() * np.sqrt(252))
    # beta (SPY'a karsi, ~son 4 ay) — SALT BILGI NOTU, hicbir filtreye girmez
    # (2026-08-24 user onayi: dusuk-beta bacak hatirlatmasi icin)
    beta = None
    if spy_lr is not None:
        j = pd.concat([lr, spy_lr], axis=1, join="inner").dropna()
        if len(j) >= 40:
            v = float(j.iloc[:, 1].var())
            if v > 0:
                b = float(j.iloc[:, 0].cov(j.iloc[:, 1])) / v
                beta = round(b, 2) if b == b else None
    if edate is None:  # TV'den gelmediyse yedek: yfinance
        try:
            ed = t.calendar.get("Earnings Date")
            edate = ed[0] if isinstance(ed, list) and ed else None
        except Exception:
            edate = None
    earn_unknown = edate is None
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
    if ivrv == ivrv and ivrv < min_vrp:  # edge süzgeci (user kurali 2026-08-03)
        return f"{tk}: VRP yok (x{ivrv:.2f} < {min_vrp:g})"
    # bilanço çapraz doğrulaması (user onayı 2026-08-06): TV küçük hisselerde bayat
    # kalabiliyor (USAR vakası) — aday listeye girmeden yf ile karşılaştır, ERKEN tarih esas
    if not earn_in_win:
        try:
            ed2 = t.calendar.get("Earnings Date")
            y2 = ed2[0] if isinstance(ed2, list) and ed2 else None
        except Exception:
            y2 = None
        if y2 is not None and (edate is None or y2 < edate):
            edate = y2
            if today <= edate <= pd.Timestamp(exp).date():
                earn_in_win = True
            earn_unknown = False
    K_all = p["strike"].astype(float)
    atm_iv = float(p["impliedVolatility"].iloc[(K_all - S).abs().argmin()]) if len(p) else None
    return dict(
        sym=tk, spot=round(S, 2), day_chg=round(day_chg, 1), expiry=str(exp)[:10], dte=int(dte),
        strike=float(best["strike"]), bid=float(best["bid"]), ask=float(best["ask"]),
        mid=round(float(best["mid"]), 2), delta=round(float(best["delta"]), 2),
        yield_pct=round(float(best["yield_pct"]), 2),
        wk_yield=round(float(best["wk_yield"]), 2),
        iv=round(float(best["impliedVolatility"]), 2), rv20=round(rv20, 2),
        iv_rv=round(ivrv, 2), beta=beta, spread_pct=round(float(best["spread_pct"]), 1),
        oi=int(best["openInterest"]) if not pd.isna(best["openInterest"]) else -1,
        collateral=int(best["strike"] * 100),
        earnings=str(edate) if edate else "?",
        earn_flag=("⚠️ bilanço pencerede" if earn_in_win
                   else ("⚠️ bilanço tarihi doğrulanamadı" if earn_unknown else "")),
        crush_flag=("🎯 IV-crush penceresi" if (edate is not None
                    and (edate - today).days > 75 and ivrv > 1.1) else ""),
        breakeven=round(float(best["strike"]) - float(best["mid"]), 2),
        order=f"SELL 1 {tk} {str(exp)[:10]} {best['strike']:g}P @ limit {float(best['mid']):.2f}",
        gtc_target=round(float(best["mid"]) * 0.25, 2),
        atm_iv=round(atm_iv, 3) if atm_iv else None,
        src=src,
    )


def beta_of(symbols):
    """Acik bacaklarin SPY betasi (~6 ay) — panel bilgi notu icin. {sym: beta|None}."""
    symbols = list(symbols)
    try:
        data = yf.download(symbols + ["SPY"], period="6mo",
                           auto_adjust=True, progress=False)["Close"]
        lr = np.log(data / data.shift(1))
        v = float(lr["SPY"].var())
        out = {}
        for s in symbols:
            b = float(lr[s].cov(lr["SPY"])) / v if s in lr and v > 0 else float("nan")
            out[s] = round(b, 2) if b == b else None
        return out
    except Exception:
        return {s: None for s in symbols}


def scan(universe, cash, **kw):
    """Evreni tarar; (DataFrame, notlar) dondurur. skor = VRP + getiri - spread cezasi."""
    rows, notes = [], []
    emap = earnings_map(universe)
    try:  # beta bilgi notu icin SPY getirileri (tek istek; hata olursa beta=None kalir)
        spy_px = yf.Ticker("SPY").history(period="4mo")["Close"]
        spy_lr = np.log(spy_px / spy_px.shift(1)).dropna()
    except Exception:
        spy_lr = None
    for tk in universe:
        try:
            r = scan_one(tk, cash, edate=emap.get(tk), spy_lr=spy_lr, **kw)
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
                col("is_primary") == True,
                # biyotek/ilac harici (user onayi 2026-08-18, SLS vakasi):
                # klinik/FDA binary riski bilanco takviminde gorunmez
                col("sector") != "Health Technology")
         .order_by("Volatility.M", ascending=False)
         .limit(top_n))
    _, df = q.get_scanner_data()
    return df["name"].tolist()
