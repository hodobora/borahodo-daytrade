# -*- coding: utf-8 -*-
"""
'Dostum yorumu' — tarama sonuclarini cumleye ceviren SALT BILGI satiri (user onayi 2026-09-10).
Filtre/siralama/secim DEGISMEZ; sadece kartlardaki sayilar + TV alt-sektoru okunur.
Olcutler (Claude'un 9-10 Eyl siralamalarinda kullandigi altili):
  Δ'nin 0.24'e uzakligi · strike tamponu · VRP · spread · alt-sektor cakismasi · beta yigini
Sayilarda olmayan sey (isim riski, haber) burada YOK — karar Bora'nin.
"""

D_OK = 0.04        # |Δ+0.24| bu kadar ise "hedefte"
TAMPON_GENIS = 5.0  # % — strike spotun bu kadar altinda ise genis
TAMPON_DAR = 3.5    # % — altinda dar
VRP_GUCLU = 1.5
SPREAD_DAR = 6.0
HI_BETA = 1.5
YENI_HISSE_GUN = 126  # islem gunu (~6 ay) — halka arzdan bu kadar gecmemisse Serhli (user onayi 2026-10-02, QNT dersi;
                      # backtest wheel_backtest_calls_2026-10-02_dusus.md: 2x cukur -40.5 -> -36.4, getiri ayni)
_YAS_CACHE = {}


def _yas_gunleri(syms):
    """Son 1 yildaki gunluk kapanis sayisi (islem gunu). <126 = halka arzdan 6 ay gecmemis. Hata -> None (isaret yok)."""
    need = [s for s in syms if s not in _YAS_CACHE]
    if need:
        try:
            import yfinance as yf
            d = yf.download(need, period="1y", auto_adjust=True, progress=False)["Close"]
            if not hasattr(d, "columns"):
                d = d.to_frame(need[0])
            for s in need:
                _YAS_CACHE[s] = int(d[s].notna().sum()) if s in d.columns else None
        except Exception:
            for s in need:
                _YAS_CACHE.setdefault(s, None)
    return {s: _YAS_CACHE.get(s) for s in syms}


MIN_PRIM = 25.0     # $ kontrat başı — altı GEÇ (user onayı 2026-09-20, backtest wheel_backtest_redday_2026-09-17.md
                    # Ek 6: $25 tabanı getiride zararsız, canlıda komisyon payını %9'dan ~%5'e indirir)


ATR_K = 1.75        # ATR tamponu — KURAL (Bora karari 2026-10-15; once bilgi notuydu): strike >= ATR_K x ATR14 uzak olmali, degilse
ATR_N = 14          # spot - ATR_K x ATR14 altindaki ilk listeli strike; onun primi < MIN_PRIM ise isim ATLA.
                    # Backtest: grid_bot wheel_piyasa_rejimi_2026-10-15.md (2x 42.2/-23.1 -> 40.0/-16.7; bagimsiz dogrulandi)
_ATR_CACHE = {}     # (sym, NY tarihi) -> (deger|None, zaman); None 15 dk sonra yeniden denenir
_EXP_CACHE = {}     # (sym, vade) -> (DataFrame|None, zaman); 15 dk — her yenilemede TV'ye gitmesin (TV 429 veriyor)
_TTL = 900


def _ny_today():
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _atr_map(syms):
    """ATR14, BACKTEST TANIMIYLA: TR = max(C_t, C_t-1) - min(L_t, C_t-1); son 14 TAMAMLANMIS gunun ortalamasi.
    Bugunun (New York) bari kullanilmaz -> Cuma taramasinda Persembe kapanisi ATR'si (backtest --g3atrlag ile ayni).
    TradingView/broker ATR'si DEGIL (gunun tepesini de icerir, ~1.35x buyuk). Hata -> None (not 'okunamadi' der)."""
    import time
    today, now = _ny_today(), time.time()

    def _fresh(s):
        v = _ATR_CACHE.get((s, today))
        return v is not None and (v[0] is not None or now - v[1] < _TTL)

    need = [s for s in syms if not _fresh(s)]
    if need:
        got = {}
        try:
            import pandas as pd
            import yfinance as yf
            d = yf.download(need, period="3mo", interval="1d", auto_adjust=True, progress=False)
            C, L = d["Close"], d["Low"]
            if not hasattr(C, "columns"):
                C, L = C.to_frame(need[0]), L.to_frame(need[0])
            for s in need:
                if s in C.columns and s in L.columns:
                    x = pd.DataFrame({"c": C[s], "l": L[s]}).dropna()
                    x = x[[ix.date() < today for ix in x.index]]
                    if len(x) >= ATR_N + 1:
                        pc = x["c"].shift(1)
                        tr = (pd.concat([x["c"], pc], axis=1).max(axis=1)
                              - pd.concat([x["l"], pc], axis=1).min(axis=1))
                        got[s] = float(tr.iloc[-ATR_N:].mean())
        except Exception:
            pass
        for s in need:
            _ATR_CACHE[(s, today)] = (got.get(s), now)
    return {s: (_ATR_CACHE.get((s, today)) or (None, 0))[0] for s in syms}


def _vade_zinciri(sym, expiry):
    """Adayin KENDI vadesindeki put zinciri (TV, tek vade -> 400 satir siniri kesmez). DataFrame(strike,bid,ask,mid) | None."""
    import time
    import pandas as pd
    key = (sym, str(expiry)[:10])
    hit = _EXP_CACHE.get(key)
    if hit is not None and time.time() - hit[1] < _TTL:
        return hit[0]
    df = None
    try:
        import tv_options
        full = tv_options.resolve(sym)
        if full:
            e = int(pd.Timestamp(expiry).strftime("%Y%m%d"))
            data = tv_options._scan({
                "columns": ["name", "option-type", "strike", "expiration", "bid", "ask", "delta", "iv"],
                "index_filters": [{"name": "underlying_symbol", "values": [full]}],
                "filter": [{"left": "option-type", "operation": "equal", "right": "put"},
                           {"left": "expiration", "operation": "in_range", "right": [e, e]}],
                "range": [0, 400]})
            rows = []
            for it in data:
                _n, _t, K, _e, bid, ask = it["d"][:6]
                if K is None:
                    continue
                bid = float(bid or 0.0)
                ask = float(ask or 0.0)
                rows.append(dict(strike=float(K), bid=bid, ask=ask, mid=(bid + ask) / 2))
            df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["strike", "bid", "ask", "mid"])
    except Exception:
        df = None
    _EXP_CACHE[key] = (df, time.time())
    return df


def atr_notu(r, atr):
    """Tek aday icin ATR notu. Iki durum: 'ATR ✓' (gerekirse kurala uygun strike + prim) / 'ATR: kurala gore ATLA'.
    Veri yoksa 'ATR okunamadi' / 'zincir okunamadi'. Secim/renk/grup DEGISMEZ.
    Canlida listedeki ilk strike <= sinir alinir (backtest 0.5'lik adim) -> canli biraz daha temkinli."""
    try:
        S, K = float(r.spot), float(r.strike)
    except Exception:
        return "ATR okunamadı"
    if atr is None or atr != atr or atr <= 0:
        return "ATR okunamadı"
    lim = S - ATR_K * atr
    if K <= lim + 1e-9:
        return "**ATR ✓**"
    ch = _vade_zinciri(r.sym, getattr(r, "expiry", None))
    if ch is None or not len(ch):
        return f"ATR: strike ≤ \\${lim:.2f} olmalı — zincir okunamadı"
    alt = ch[(ch["strike"] <= lim + 1e-9) & (ch["bid"] > 0)]
    if not len(alt):
        return "**ATR: kurala göre ATLA** (sınırın altında alıcılı strike yok)"
    best = alt.sort_values("strike").iloc[-1]
    prem = float(best["mid"]) * 100
    if prem < MIN_PRIM:
        return f"**ATR: kurala göre ATLA** ({float(best['strike']):g}P \\${prem:.0f})"
    return f"**ATR ✓ {float(best['strike']):g}P · \\${prem:.0f}**"


def _tampon(r):
    try:
        return (float(r.spot) - float(r.strike)) / float(r.spot) * 100
    except Exception:
        return None


RED_SPY = -1.0  # % — panel "Gun rengi" bannerı ile ayni esik


def gun_notu(spy_chg, weekday):
    """KIRMIZI GUN KURALI (user onayi 2026-09-17, backtest wheel_backtest_redday_2026-09-17.md):
    put satis gunu = SPY <= -1% olan gun; Pzt-Per kirmizi gelmezse CUMA gir. weekday: 0=Pzt..4=Cum."""
    if spy_chg is None or spy_chg != spy_chg:
        return "⚪ Gün rengi okunamadı — kırmızı gün kuralı için SPY'a bak."
    if spy_chg <= RED_SPY:
        return f"🔴 **Kırmızı gün (SPY {spy_chg:+.1f}%) — PUT SATIŞ GÜNÜ.**"
    if weekday == 4:
        return f"📅 **Cuma (SPY {spy_chg:+.1f}%)** — hafta içinde kırmızı gün gelmediyse **bugün gir**."
    if weekday is not None and weekday >= 5:
        return "⚪ Piyasa kapalı — ilk kırmızı günde ya da cuma."
    return (f"⏳ Bugün kırmızı değil (SPY {spy_chg:+.1f}%) — **kırmızı gün bekle**; "
            "cumaya kadar gelmezse cuma gir.")


def build(df, open_syms, ind_map, open_betas=None, spy_chg=None, weekday=None):
    """df: tarama DataFrame'i (skor sirali). open_syms: acik semboller.
    ind_map: {sym: industry} (adaylar + acik). open_betas: {sym: beta|None}.
    spy_chg/weekday: kirmizi gun kurali satiri icin. Donus: markdown metni ('' ise yorum yok)."""
    if df is None or not len(df):
        return ""
    open_syms = set(open_syms or [])
    open_ind = {}
    for s in open_syms:
        i = ind_map.get(s)
        if i:
            open_ind.setdefault(i, []).append(s)
    hi_beta_open = sum(1 for b in (open_betas or {}).values() if b is not None and b > HI_BETA)

    mantikli, serhli, gec = [], [], []
    yas = _yas_gunleri([str(x) for x in df["sym"]]) if "sym" in df.columns else {}
    atrs = _atr_map([str(x) for x in df["sym"]]) if "sym" in df.columns else {}
    for r in df.itertuples():
        if getattr(r, "earn_flag", ""):
            continue  # kartta zaten 🚫 / ⚠️
        arti, eksi, engel = [], [], []
        ind = ind_map.get(r.sym, "")
        # --- Δ
        dd = abs(float(r.delta) + 0.24)
        if dd <= D_OK:
            arti.append("Δ hedefte")
        elif dd > 0.05:
            eksi.append(f"Δ{abs(float(r.delta)):.2f} hedef dışı")
        # --- tampon
        t = _tampon(r)
        if t is not None:
            if t >= TAMPON_GENIS:
                arti.append(f"tampon %{t:.1f}")
            elif t < TAMPON_DAR:
                eksi.append(f"tampon %{t:.1f} dar")
        # --- yeni hisse (halka arzdan 6 ay gecmemis -> Serhli)
        g = yas.get(r.sym)
        if g is not None and g < YENI_HISSE_GUN:
            eksi.append(f"halka arzdan 6 ay geçmedi (~{max(1, round(g / 21))} ay)")
        # --- VRP / spread
        if float(r.iv_rv) >= VRP_GUCLU:
            arti.append(f"VRP ×{float(r.iv_rv):.2f}")
        if float(r.spread_pct) <= SPREAD_DAR:
            arti.append("spread dar")
        # --- tema
        if r.sym in open_syms:
            engel.append("🔁 bu isim zaten açık")
        elif ind and ind in open_ind:
            engel.append(f"🔁 {ind} portföyde ({', '.join(sorted(open_ind[ind]))})")
        elif ind:
            arti.append(f"{ind} — yeni tema")
        # --- beta yigini
        b = getattr(r, "beta", None)
        if b is not None and b == b and b > HI_BETA and hi_beta_open >= 2:
            eksi.append(f"β{b:.2f} — açıkta zaten {hi_beta_open} yüksek-beta")
        elif b is not None and b == b and b < 0.8:
            arti.append(f"β{b:.2f} 🐢")

        try:
            prim_val = float(r.mid) * 100
            prim = f"prim \\${prim_val:.0f}"  # \$ — Streamlit markdown'da $..$ LaTeX sayılır
            if prim_val < MIN_PRIM:
                engel.append(f"prim \\${prim_val:.0f} — \\${MIN_PRIM:.0f} altı, komisyon payı büyür")
        except Exception:
            prim = ""
        # user isteği 2026-09-30: satır sonunda çizgi + 1 kontratın teminatı (strike × 100)
        try:
            tem = f" | **1 put = \\${float(r.strike) * 100:,.0f} teminat**"
        except Exception:
            tem = ""
        # user isteği 2026-09-10: her ticker ayrı satır; önerilen yeşil, diğerleri kırmızı
        renk = "green" if not (engel or eksi) else "red"
        # 2026-10-15 (user onayi): ATR tamponu notu satir sonunda — SALT BILGI, renk/grup degismez
        try:
            atr_txt = f" | {atr_notu(r, atrs.get(r.sym))}"
        except Exception:
            atr_txt = " | ATR okunamadı"
        txt = f"- :{renk}[**{r.sym}**] {prim} — {', '.join(arti + eksi + engel)}{tem}{atr_txt}"
        if engel:
            gec.append(txt)
        elif eksi:
            serhli.append(txt)
        else:
            mantikli.append(txt)

    n = len(df)
    # 2026-09-30 (user): gun_notu (kirmizi gun / cuma kurali satiri) kaldirildi
    parts = [f"💬 **Dostum yorumu** · taramadan {n} aday.", ""]
    parts.append("**Mantıklı görünen**")
    parts += mantikli if mantikli else ["- bugün yok — temiz aday çıkmadı"]
    if serhli:
        parts += ["", "**Şerhli**"] + serhli
    if gec:
        parts += ["", "**Geç**"] + gec
    parts += ["", "_Sayılardan türetilmiş not — isim riski/haber burada yok. Karar: Bora._",
              f"_ATR KURALI (15 Eki): strike, spot − {ATR_K:g} × ATR14 altına çekilir ve o strike satılır; primi \\${MIN_PRIM:.0f} altıysa "
              "isim atlanır. (Panel listeyi yeniden sıralamaz; kuralı bu not gösterir.) ATR = son 14 tamamlanmış günün "
              "max(kapanış, önceki kapanış) − min(dip, önceki kapanış) ortalaması; TradingView'in hazır ATR'si değil. "
              "Strike/prim TradingView canlı zincirinden; ATR'nin 14 günlük geçmişi Yahoo günlük barlarından "
              "(TradingView bu geçmişi vermiyor; barlar TradingView ile birebir aynı)._"]
    return "\n".join(parts)
