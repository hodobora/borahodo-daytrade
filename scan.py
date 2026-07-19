# -*- coding: utf-8 -*-
"""
LUK PIPELINE — tarama (LUK_MODEL_V1.md'nin kod hali).

Iki mod:
  python scan.py evening    -> aksam taramasi (22:05+ veya elle): yarinin plani
  python scan.py premarket  -> 14:45-15:25 CET: pre-market gap/EP adaylari + potent

Cikti: plans/plan_YYYY-MM-DD.json + .md  (Streamlit app bunlari okur)
Bildirim kanali YOK — tetikler TradingView alarmi (kullanici kurar, plan .md'de
alarm listesi hazir), plan goruntusu Streamlit'te.

Asama 1: tradingview-screener ile genis eleme (fiyat/mktcap/hacim/volatilite/RS)
Asama 2: kisa liste icin yfinance gunluk bar -> EMA 9/21/50 dizilimi, inside/dar gun,
         tetik + stop referanslari (model kurallari)
"""
import os, sys, json, math
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

ET = ZoneInfo("America/New_York")


def target_trading_day(kind):
    """Planin UYGULANACAGI ABD islem gunu (tarih karismasin — 2026-07-19 kurali).
    premarket: bugunun seansi (hafta sonuysa sonraki pazartesi).
    evening  : SONRAKI islem gunu (aksam kosusu ertesi gunun planidir)."""
    d = datetime.now(ET).date()
    if kind == "evening":
        d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return str(d)

BASE = os.path.dirname(os.path.abspath(__file__))
PLANS = os.path.join(BASE, "plans")
os.makedirs(PLANS, exist_ok=True)

# Model esikleri (LUK_MODEL_V1.md)
PRICE_MIN = 5.0
MKTCAP_MIN = 300e6
AVGVOL_MIN = 500_000
VOLA_MIN = 5.0        # Volatility.M >= %5  (ADR>=%5 vekili)
RS_1M_MIN = 15.0      # lider RS: 1 ay perf >= %15
GAP_MIN = 8.0         # EP: pre-market gap >= %8
TIGHT_K = 0.7         # dar gun: range < 0.7 * ADR20


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def broad_scan(extra_cols=(), extra_where=()):
    from tradingview_screener import Query, col
    q = (Query()
         .select('name', 'exchange', 'close', 'market_cap_basic',
                 'average_volume_30d_calc', 'Volatility.M', 'Perf.1M',
                 'change', 'relative_volume_10d_calc', 'sector', *extra_cols)
         .where(
             col('exchange').isin(['NASDAQ', 'NYSE']),
             col('type') == 'stock',
             col('subtype') == 'common',
             col('is_primary') == True,
             col('close') > PRICE_MIN,
             col('market_cap_basic') > MKTCAP_MIN,
             col('average_volume_30d_calc') > AVGVOL_MIN,
             col('Volatility.M') > VOLA_MIN,
             *extra_where)
         .limit(3000))
    _, df = q.get_scanner_data()
    df["sym"] = df["name"].astype(str).str.replace(".", "-", regex=False)
    return df


def refine_daily(symbols):
    """Kisa liste icin gunluk bar -> model kosullari + seviyeler."""
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        df = yf.download(chunk, period="6mo", auto_adjust=True,
                         group_by="ticker", threads=True, progress=False)
        for t in chunk:
            try:
                d = df[t].dropna(subset=["Close"]) if isinstance(df.columns, pd.MultiIndex) else df.dropna(subset=["Close"])
            except Exception:
                continue
            if len(d) < 60:
                continue
            c, h, l, v = d["Close"], d["High"], d["Low"], d["Volume"]
            e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
            adr20 = float((h / l - 1).rolling(20).mean().iloc[-1])
            rng = float(h.iloc[-1] - l.iloc[-1])
            out[t] = dict(
                close=float(c.iloc[-1]), pdh=float(h.iloc[-1]), pdl=float(l.iloc[-1]),
                adr=adr20,
                leading=bool(c.iloc[-1] > e9.iloc[-1] > e21.iloc[-1] > e50.iloc[-1]),
                inside=bool(h.iloc[-1] < h.iloc[-2] and l.iloc[-1] > l.iloc[-2]),
                tight=bool(rng < adr20 * float(c.iloc[-1]) * TIGHT_K),
                e9=float(e9.iloc[-1]), e21=float(e21.iloc[-1]), e50=float(e50.iloc[-1]),
                conv=bool(abs(e9.iloc[-1] / e21.iloc[-1] - 1) < 0.015),
                touch21=bool(l.iloc[-1] <= e21.iloc[-1] * 1.01),
                rvol=float((v.iloc[-1] / v.rolling(20).mean().iloc[-1])),
                hh60=float(c.rolling(60).max().iloc[-1]),
            )
    return out


def evening():
    """Aksam taramasi: yarinin 5a/5c adaylari + Leading watchlist saglik sayisi."""
    df = broad_scan()
    leaders = df[df["Perf.1M"] >= RS_1M_MIN]
    info = refine_daily(sorted(leaders["sym"].unique()))
    plan = dict(kind="evening", created=str(datetime.now()), for_day=target_trading_day("evening"),
                leading_count=0, candidates=[])
    for _, r in leaders.iterrows():
        t = r["sym"]
        k = info.get(t)
        if not k or not k["leading"]:
            continue
        plan["leading_count"] += 1
        setup = None
        if k["inside"] or k["tight"]:
            setup = "5a PDH kirilimi"
            trigger = k["pdh"]
            stop_ref = k["pdl"]
        elif k["conv"] and k["touch21"]:
            setup = "5c pullback (EMA9/21 confluence)"
            trigger = None   # tetik 5dk'da olusur; bolge verilir
            stop_ref = k["e21"] * 0.99
        if not setup:
            continue
        max_stop = min(k["adr"] * 0.5, 0.05)
        plan["candidates"].append(dict(
            sym=t, sector=str(r.get("sector", "")), setup=setup,
            close=round(k["close"], 2),
            trigger=round(trigger, 2) if trigger else None,
            zone=None if trigger else f"{k['e21']:.2f}-{k['e9']:.2f}",
            stop_ref=round(stop_ref, 2), adr_pct=round(k["adr"] * 100, 1),
            max_stop_pct=round(max_stop * 100, 1),
            perf1m=round(float(r["Perf.1M"]), 1), rvol_yesterday=round(k["rvol"], 2),
        ))
    plan["candidates"].sort(key=lambda x: -x["perf1m"])
    save(plan)


def premarket():
    """Pre-market: potent (dunun en iyileri) + gap/EP adaylari."""
    from tradingview_screener import col
    df = broad_scan(extra_cols=("premarket_change", "premarket_volume"))
    potent = (df.sort_values("change", ascending=False).head(15)
                [["sym", "sector", "change", "relative_volume_10d_calc"]])
    gappers = df[(df["premarket_change"] >= GAP_MIN)].copy()
    info = refine_daily(sorted(gappers["sym"].unique())) if len(gappers) else {}
    eps = []
    for _, r in gappers.iterrows():
        t = r["sym"]
        k = info.get(t)
        if not k:
            continue
        open_est = k["close"] * (1 + r["premarket_change"] / 100)
        eps.append(dict(
            sym=t, sector=str(r.get("sector", "")),
            pm_gap_pct=round(float(r["premarket_change"]), 1),
            pm_vol=int(r.get("premarket_volume") or 0),
            clears_60d_high=bool(open_est > k["hh60"]),
            setup="5d EP -> ORH girisi" if open_est > k["hh60"] else "gap (60g tepe ALTINDA — dikkat)",
            adr_pct=round(k["adr"] * 100, 1),
        ))
    eps.sort(key=lambda x: -x["pm_gap_pct"])
    plan = dict(kind="premarket", created=str(datetime.now()), for_day=target_trading_day("premarket"),
                potent=potent.to_dict("records"), ep_candidates=eps)
    save(plan)


def save(plan):
    import storage
    storage.save_plan(plan)   # Supabase (varsa) + lokal JSON
    day = plan["for_day"]
    kind = plan["kind"]
    jp = os.path.join(PLANS, f"plan_{day}_{kind}.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    # insan-okur ozet + TV alarm listesi
    lines = [f"# LUK PLAN — {day} ({kind})", ""]
    if kind == "evening":
        lines.append(f"Leading watchlist: {plan['leading_count']} isim (piyasa termometresi)")
        lines.append("")
        lines.append("| # | Sembol | Setup | Tetik | Bolge | Stop ref | ADR% | Max stop% | 1M% |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(plan["candidates"], 1):
            lines.append(f"| {i} | {c['sym']} | {c['setup']} | {c['trigger'] or '-'} | "
                         f"{c['zone'] or '-'} | {c['stop_ref']} | {c['adr_pct']} | "
                         f"{c['max_stop_pct']} | {c['perf1m']} |")
        lines += ["", "## TV ALARM LISTESI (kur ve yat)",
                  "Alarm kurarken 'Message' alanina asagidaki satiri YAPISTIR — telefona",
                  "dusen push karar bagl amiyla gelsin:"]
        for c in plan["candidates"]:
            if c["trigger"]:
                msg = (f"[LUK SISTEMI: AL adayi] {c['sym']} tetik {c['trigger']} kirildi | "
                       f"stop ref {c['stop_ref']} (max %{c['max_stop_pct']}) | "
                       f"{c['setup']} | teyit: hacim + 5dk 9EMA | KARAR: BORA")
                lines.append(f"- **{c['sym']}** — Crossing Up `{c['trigger']}`")
                lines.append(f"  - Mesaj: `{msg}`")
            else:
                lines.append(f"- **{c['sym']}** — bolge {c['zone']}, 5dk onceki mum high kirilimi (gozle izle)")
        lines += ["", "Kurallar: gunde max ~5 isim SENIN secimin; tereddut = pas; "
                  "endeks dusen 21/50'ye bounce ediyorsa giris yok; stop girisle ayni anda EMIR."]
    else:
        lines.append("## Potent (dunun en iyileri — tema ipucu)")
        for p in plan["potent"]:
            lines.append(f"- {p['sym']} ({p['sector']}) +%{p['change']:.1f}")
        lines += ["", "## EP adaylari (pre-market gap >= %8)"]
        for e in plan["ep_candidates"]:
            lines.append(f"- {e['sym']} +%{e['pm_gap_pct']} pmVol={e['pm_vol']:,} "
                         f"ADR%{e['adr_pct']} -> {e['setup']}")
    mp = os.path.join(PLANS, f"plan_{day}_{kind}.md")
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"OK: {jp}")
    print(f"OK: {mp}")
    if kind == "evening":
        print(f"Aday: {len(plan['candidates'])} | Leading: {plan['leading_count']}")
    else:
        print(f"EP aday: {len(plan['ep_candidates'])}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "evening"
    if mode == "premarket":
        premarket()
    else:
        evening()
