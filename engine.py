# -*- coding: utf-8 -*-
"""
Canli veri + sinyal motoru (LUK_MODEL_V1 kurallari).
Veri: yfinance (kimliksiz, ~saniyeler-1dk gecikme). Karar YOK — durum uretir.
"""
import math, time
import pandas as pd
import yfinance as yf


def tv_snapshot(symbols, sessionid):
    """TradingView canli veri (kullanicinin oturum cerezi ile — gercek zamanli).
    Donus: (snaps, kaynak_durumu)  kaynak_durumu: 'tv' | 'tv_delayed' | 'tv_dead'
    """
    try:
        from tradingview_screener import Query, col
        q = (Query()
             .select('name', 'close', 'high', 'low', 'open', 'volume',
                     'update_mode', 'lp_time')
             .where(col('name').isin(list(symbols)))
             .limit(len(symbols) + 10))
        _, df = q.get_scanner_data(cookies={'sessionid': sessionid})
    except Exception:
        return {}, 'tv_dead'
    if df is None or not len(df):
        return {}, 'tv_dead'
    out, modes, lp_ages = {}, set(), []
    now = time.time()
    for _, r in df.iterrows():
        try:
            out[str(r['name'])] = dict(
                price=float(r['close']), day_high=float(r['high']),
                day_low=float(r['low']), day_open=float(r['open']),
                day_vol=float(r['volume'] or 0), asof=str(r.get('lp_time')))
            modes.add(str(r.get('update_mode', '')))
            if r.get('lp_time'):
                lp_ages.append(now - float(r['lp_time']))
        except Exception:
            continue
    if not out:
        return {}, 'tv_dead'
    delayed = any('delayed' in m for m in modes)
    # RTH icinde veri 3 dk'dan eskiyse bayat say (piyasa kapaliyken dogal olarak eski)
    stale = _market_open_now() and lp_ages and min(lp_ages) > 180
    if delayed or stale:
        return out, 'tv_delayed'
    return out, 'tv'


def _market_open_now():
    from zoneinfo import ZoneInfo
    from datetime import datetime
    et = datetime.now(ZoneInfo('America/New_York'))
    return et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) < (16, 0)


def get_orh(symbols):
    """Acilistan sonra: gunun ILK 1-dakikalik mumunun high/low'u (Luk ORH tanimi).
    yfinance 1dk verisi ~1dk gecikmeli — acilisin ilk 2-3 dakikasinda ORH gec
    gorulebilir (bilinen kisit)."""
    out = {}
    if not symbols or not _market_open_now():
        return out
    try:
        df = yf.download(list(symbols), period="1d", interval="1m",
                         auto_adjust=True, group_by="ticker", threads=True,
                         progress=False, prepost=False)
        for s in symbols:
            try:
                d = df[s].dropna(subset=["Close"]) if isinstance(df.columns, pd.MultiIndex) else df.dropna(subset=["Close"])
                if len(d):
                    out[s] = dict(orh=float(d["High"].iloc[0]),
                                  orl=float(d["Low"].iloc[0]))
            except Exception:
                continue
    except Exception:
        pass
    return out


def live_snapshot(symbols):
    """Semboller icin anlik fiyat + gun ici bilgi (1dk barlardan)."""
    if not symbols:
        return {}
    out = {}
    df = yf.download(list(symbols), period="1d", interval="1m",
                     auto_adjust=True, group_by="ticker", threads=True,
                     progress=False, prepost=False)
    for s in symbols:
        try:
            d = df[s].dropna(subset=["Close"]) if isinstance(df.columns, pd.MultiIndex) else df.dropna(subset=["Close"])
            if not len(d):
                continue
            out[s] = dict(price=float(d["Close"].iloc[-1]),
                          day_high=float(d["High"].max()),
                          day_low=float(d["Low"].min()),
                          day_open=float(d["Open"].iloc[0]),
                          day_vol=float(d["Volume"].sum()),
                          asof=str(d.index[-1]))
        except Exception:
            continue
    return out


def daily_context(symbols):
    """Gunluk baglam: EMA 9/21/50, 20g ort hacim, dun kapanis. (cache'lenir)"""
    out = {}
    if not symbols:
        return out
    from zoneinfo import ZoneInfo
    from datetime import datetime
    today_et = datetime.now(ZoneInfo('America/New_York')).date()
    df = yf.download(list(symbols), period="4mo", auto_adjust=True,
                     group_by="ticker", threads=True, progress=False)
    for s in symbols:
        try:
            d = df[s].dropna(subset=["Close"]) if isinstance(df.columns, pd.MultiIndex) else df.dropna(subset=["Close"])
            # BUGUNUN (NY) tamamlanmamis bari varsa dislari birak — prev_close her
            # zaman SON TAMAMLANMIS seansin kapanisi olsun (tarih karismasin kurali)
            d_hist = d[d.index.date < today_et] if len(d) and d.index[-1].date() == today_et else d
            c, v = d_hist["Close"], d_hist["Volume"]
            if len(c) < 55:
                continue
            out[s] = dict(
                e9=float(c.ewm(span=9, adjust=False).mean().iloc[-1]),
                e21=float(c.ewm(span=21, adjust=False).mean().iloc[-1]),
                e50=float(c.ewm(span=50, adjust=False).mean().iloc[-1]),
                avgvol20=float(v.rolling(20).mean().iloc[-1]),
                prev_close=float(c.iloc[-1]),
            )
        except Exception:
            continue
    return out


def watch_status(cand, snap):
    """Aday satiri durumu: tetik kirildi mi."""
    trig = cand.get("trigger")
    if not trig or not snap:
        return "IZLEMEDE", ""
    price, hi = snap["price"], snap["day_high"]
    if hi >= trig:
        if price >= trig:
            return "AL_ADAYI", f"tetik {trig} kirildi, fiyat ustunde"
        return "TETIK_GERI", f"tetik {trig} kirildi ama fiyat geri dustu (fakeout riski)"
    dist = (trig / price - 1) * 100
    return "IZLEMEDE", f"tetige %{dist:.1f}"


def position_status(row, snap, ctx):
    """Acik pozisyon: kar%, R, ve 4 mekanik SAT kosulu (LUK_MODEL_V1 §8)."""
    entry, stop = float(row["entry"]), float(row["stop"])
    if not snap:
        return dict(state="VERI_YOK", kar_pct=None, r=None, flags=[])
    p = snap["price"]
    runit = entry - stop
    kar = (p / entry - 1) * 100
    r = (p - entry) / runit if runit > 0 else 0
    flags = []
    if p <= stop:
        flags.append("STOP_YENDI")
    if r >= 3 and not row.get("partial_price"):
        flags.append("SAT_3R_TRIM")
    day_chg = (p / ctx["prev_close"] - 1) * 100 if ctx else 0
    vol_x = (snap["day_vol"] / ctx["avgvol20"]) if ctx and ctx["avgvol20"] else 0
    if r >= 4 and day_chg >= 8 and vol_x >= 2:
        flags.append("SAT_KLIMAKS")
    if ctx and p < ctx["e9"]:
        flags.append("SAT_9EMA_ALTI")
    state = flags[0] if flags else "TUT"
    return dict(state=state, kar_pct=round(kar, 2), r=round(r, 2),
                day_chg=round(day_chg, 1), vol_x=round(vol_x, 1),
                e9_dist=round((p / ctx["e9"] - 1) * 100, 1) if ctx else None,
                e21_dist=round((p / ctx["e21"] - 1) * 100, 1) if ctx else None,
                e50_dist=round((p / ctx["e50"] - 1) * 100, 1) if ctx else None,
                stop_dist=round((p / stop - 1) * 100, 1), flags=flags)


FLAG_TR = {
    "STOP_YENDI": "🔴 STOP YENDİ — broker emrin çalışmış olmalı, kontrol et",
    "SAT_3R_TRIM": "🟡 [SAT adayı: 3R] — güce %30 trim zamanı",
    "SAT_KLIMAKS": "🟠 [SAT adayı: KLİMAKS] — 4R+ ve gün +%8+ ve hacim 2x: hepsini güce sat",
    "SAT_9EMA_ALTI": "🟡 [SAT adayı: 9EMA] — günlük 9 EMA altında, kapanışa doğru kesinleşir",
    "TUT": "🟢 TUT",
    "VERI_YOK": "⚪ veri yok",
}
