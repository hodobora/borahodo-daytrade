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


def _tampon(r):
    try:
        return (float(r.spot) - float(r.strike)) / float(r.spot) * 100
    except Exception:
        return None


def build(df, open_syms, ind_map, open_betas=None):
    """df: tarama DataFrame'i (skor sirali). open_syms: acik semboller.
    ind_map: {sym: industry} (adaylar + acik). open_betas: {sym: beta|None}.
    Donus: markdown metni ('' ise yorum yok)."""
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
            prim = f"prim \\${float(r.mid) * 100:.0f}"  # \$ — Streamlit markdown'da $..$ LaTeX sayılır
        except Exception:
            prim = ""
        # user isteği 2026-09-10: her ticker ayrı satır; önerilen yeşil, diğerleri kırmızı
        renk = "green" if not (engel or eksi) else "red"
        txt = f"- :{renk}[**{r.sym}**] {prim} — {', '.join(arti + eksi + engel)}"
        if engel:
            gec.append(txt)
        elif eksi:
            serhli.append(txt)
        else:
            mantikli.append(txt)

    n = len(df)
    parts = [f"💬 **Dostum yorumu** · taramadan {n} aday.", ""]
    parts.append("**Mantıklı görünen**")
    parts += mantikli if mantikli else ["- bugün yok — temiz aday çıkmadı"]
    if serhli:
        parts += ["", "**Şerhli**"] + serhli
    if gec:
        parts += ["", "**Geç**"] + gec
    parts += ["", "_Sayılardan türetilmiş not — isim riski/haber burada yok. Karar: Bora._"]
    return "\n".join(parts)
