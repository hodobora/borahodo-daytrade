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
        txt = f"- :{renk}[**{r.sym}**] {prim} — {', '.join(arti + eksi + engel)}{tem}"
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
    parts += ["", "_Sayılardan türetilmiş not — isim riski/haber burada yok. Karar: Bora._"]
    return "\n".join(parts)
