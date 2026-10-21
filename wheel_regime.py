# -*- coding: utf-8 -*-
"""
Rejim satiri — acilan putlarin prim/teminat medyani (net prim / strike x 100 x kontrat).
Kural tanimi Bora onayi 2026-10-15 (wheel_journal.md 'REJIM SATIRI'); panelde Dostum yorumunun altinda
AYRI kutu olarak gosterim Bora onayi 2026-10-19 (tarama verisiyle karismasin diye ayri modul).
Taban = ATR doneminin ilk iki ayi (16 Eki - 18 Ara 2026). Taban sabitlendikten sonra son 3 ayin medyani
tabanin %30+ altina inerse uyari. SALT BILGI, kural degil: tarama/siralama/secim degismez.
"""
from datetime import date, datetime, timedelta

import pandas as pd

TABAN_BAS = date(2026, 10, 16)
TABAN_SON = date(2026, 12, 18)
ESIK = 0.70          # son 3 ay medyani < taban x 0.70 -> uyari ("tabanin %30+ altinda")
MIN_N = 4            # medyan icin en az islem (taban da, son 3 ay da)
PENCERE_GUN = 91     # "son 3 ay" = bugunden geriye 91 gun
AY_TR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


def _puts(df):
    """Put satislari: opened (tarih), ratio (prim/teminat). Hisse satirlari ve eksik veri atlanir."""
    if df is None or not len(df):
        return pd.DataFrame(columns=["opened", "ratio"])
    d = df[df["kind"].astype(str).str.upper().isin(["CSP", "PUT"])].copy()
    teminat = pd.to_numeric(d["collateral"], errors="coerce")
    yedek = (pd.to_numeric(d["strike"], errors="coerce") * 100 *
             pd.to_numeric(d["qty"], errors="coerce"))
    teminat = teminat.where(teminat > 0, yedek)
    d["ratio"] = pd.to_numeric(d["premium"], errors="coerce") / teminat
    d["opened"] = pd.to_datetime(d["opened_at"], errors="coerce", utc=True).dt.date
    return d.dropna(subset=["opened", "ratio"])[["opened", "ratio"]]


def summary(df, today=None):
    """Kutu icin ozet: taban, son 3 ay, ay ay medyan, uyari bayragi."""
    today = today or datetime.now().date()
    p = _puts(df)
    tb = p[(p["opened"] >= TABAN_BAS) & (p["opened"] <= TABAN_SON)]
    son3 = p[(p["opened"] > today - timedelta(days=PENCERE_GUN)) & (p["opened"] <= today)]
    taban_med = float(tb["ratio"].median()) if len(tb) else None
    son3_med = float(son3["ratio"].median()) if len(son3) else None
    taban_bitti = today > TABAN_SON
    oran = son3_med / taban_med if (taban_med and son3_med is not None) else None
    uyari = bool(taban_bitti and len(tb) >= MIN_N and len(son3) >= MIN_N
                 and oran is not None and oran < ESIK)
    aylar = []
    for k in range(2, -1, -1):  # son 3 takvim ayi, eskiden yeniye
        y, m = today.year, today.month - k
        while m <= 0:
            y, m = y - 1, m + 12
        a = p[[(o.year, o.month) == (y, m) for o in p["opened"]]]
        aylar.append((AY_TR[m - 1], float(a["ratio"].median()) if len(a) else None, len(a)))
    return dict(taban_med=taban_med, taban_n=len(tb), taban_bitti=taban_bitti,
                son3_med=son3_med, son3_n=len(son3), oran=oran, uyari=uyari, aylar=aylar)


def _pct(x):
    return "—" if x is None else f"%{x * 100:.2f}"


def line(s):
    """Tek satir (Bora 2026-10-19: 'boyle daha iyi'). (satir, uyari_metni|None).
    Uyari metni yalniz uyari varken doner; panel onu ayri kirmizi satir olarak gosterir."""
    taban = _pct(s["taban_med"]) + ("" if s["taban_bitti"] else " (oluşuyor)")
    if s["uyari"]:
        durum = "UYARI → tavan 1.5x"  # KURAL (Bora 2026-10-21)
    elif s["taban_bitti"] and (s["taban_n"] < MIN_N or s["son3_n"] < MIN_N):
        durum = "yetersiz veri"
    else:
        durum = "normal"  # taban olusurken uyari calismaz (18 Ara'dan sonra)
    satir = f"📉 Rejim: son 3 ay {_pct(s['son3_med'])} · taban {taban} · durum: {durum}"
    uyari = None
    if s["uyari"]:
        # KURAL (Bora 2026-10-21): uyari acikken kaldirac tavani 2.5x -> 1.5x (yalniz yeni girisler);
        # son 3 ay medyani taban x 0.70'i gecince 2.5x'e donulur
        uyari = (f"🟥 PRİMLER İNCELİYOR — KURAL: kaldıraç tavanı 1.5x (yalnız yeni girişler). "
                 f"Son 3 ayın prim/teminat medyanı tabanın %{(1 - s['oran']) * 100:.0f} altında (eşik %30). "
                 "Medyan tabanın %70'ini yeniden geçince 2.5x'e dönülür.")
    return satir, uyari
