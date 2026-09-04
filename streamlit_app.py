# -*- coding: utf-8 -*-
"""
BORAHODO-DAYTRADE — WHEEL PANELI (opsiyon satis sistemi).
Tarama: yfinance (~15dk gecikme) — kontrat SECIMI icin yeterli, nihai fiyat IBKR'de.
Emir gonderme YOK — panel sinyal + kayit + takip. KARAR: BORA.
Onceki LUK Model V1 portali rafa kalkti (git gecmisinde duruyor).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

import wheel_scan
import wheel_store

ET = ZoneInfo("America/New_York")
GUN_TR = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]

st.set_page_config(page_title="borahodo-wheel", page_icon="🎡", layout="wide")


# ---------- giris kapisi ----------
def _gate():
    try:
        pw = st.secrets.get("PANEL_PASS")
    except Exception:
        pw = None
    if not pw:
        st.warning("PANEL_PASS secret'i tanimli degil — panel herkese acik! "
                   "Streamlit Cloud → Settings → Secrets'a PANEL_PASS ekle.")
        return True
    if st.session_state.get("auth_ok"):
        return True
    with st.form("giris"):
        given = st.text_input("Parola", type="password")
        if st.form_submit_button("Gir") and given == pw:
            st.session_state["auth_ok"] = True
            st.rerun()
    return False


if not _gate():
    st.stop()


# panel acikken 15 dk'da bir kendini yeniler (pozisyon fiyatlari/%75 barlari)
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=15 * 60 * 1000, key="auto15")
except Exception:
    pass

# ---------- baslik + bekci seridi ----------
def market_open_now():
    now = datetime.now(ET)
    return now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)


now = datetime.now(ET)
durum = "🟢 PİYASA AÇIK" if market_open_now() else "🔴 PİYASA KAPALI"
st.title("🎡 borahodo-wheel — Opsiyon Satış Paneli")
st.caption(f"NY: {GUN_TR[now.weekday()]} {now.strftime('%d %b %H:%M')} · {durum} · "
           f"Depo: {wheel_store.backend_name()} · Veri: TradingView CANLI "
           "(oturum düşerse yfinance ~15dk) · KARAR: BORA · Emirler IBKR'den")

if wheel_store.LAST_ERROR:
    with st.expander("⚠️ Kalıcı depo kurulu değil — kayıtlar geçici! (kurulum: 60 saniye)", expanded=False):
        st.markdown("1. [supabase.com](https://supabase.com) → projen → sol menü "
                    "**SQL Editor** → **New query**  \n"
                    "2. Aşağıyı yapıştır → **Run**  \n"
                    "3. Bu paneli yenile — uyarı kalkar.")
        try:
            st.code(open("schema_wheel.sql", encoding="utf-8").read(), language="sql")
        except Exception:
            st.text("schema_wheel.sql repo kökünde")

open_pos = wheel_store.get_wheel(status="open")
used_collateral = float(open_pos["collateral"].fillna(0).sum()) if len(open_pos) else 0.0

c1, c2, c3, c4 = st.columns(4)
stored_cash = wheel_store.get_cash()
cash = c1.number_input("Hesap nakiti ($)", min_value=0,
                       value=int(st.session_state.get("cash", stored_cash)), step=100)
st.session_state["cash"] = cash
if cash != stored_cash:
    wheel_store.set_cash(cash)  # kalici — her cihazda ayni deger acilir
c2.metric("Kullanılan teminat", f"${used_collateral:,.0f}")
oran = used_collateral / cash * 100 if cash else 0
c3.metric("Teminat / nakit", f"%{oran:.0f}")
c4.metric("Açık kontrat", len(open_pos))
if oran > 85:
    st.error("🚨 Teminat/nakit %85 üstü — yeni pozisyon YOK. Cash-secured disiplini: margin asla.")
elif oran > 60:
    st.warning("⚠️ Teminat/nakit %60 üstü — tampon inceliyor.")


@st.cache_data(ttl=900, show_spinner=False)
def spy_day():
    try:
        import yfinance as yf
        px = yf.Ticker("SPY").history(period="5d")["Close"]
        return float(px.iloc[-1] / px.iloc[-2] - 1) * 100
    except Exception:
        return None

# ---------- POZISYONLAR (ana ekran) ----------
def current_mid(sym, expiry, strike, kind):
    """(mid, kaynak) dondurur: ('tv' canli | 'yf' ~15dk gecikmeli | None)."""
    try:
        import tv_options
        m = tv_options.quote(sym, expiry, strike, kind)
        if m is not None:
            return m, "tv"
    except Exception:
        pass
    try:
        import yfinance as yf
        ch = yf.Ticker(sym).option_chain(str(expiry))
        leg = ch.puts if kind == "CSP" else ch.calls
        row = leg[leg["strike"] == float(strike)]
        if len(row):
            bid, ask = float(row["bid"].iloc[0]), float(row["ask"].iloc[0])
            if bid > 0 or ask > 0:
                return round((bid + ask) / 2, 2), "yf"
        return None, None
    except Exception:
        return None, None


st.subheader("📌 Pozisyonlar")
if not len(open_pos):
    st.info("Açık pozisyon yok.")
def spot_price(sym):
    try:
        import yfinance as yf
        return float(yf.Ticker(sym).history(period="1d")["Close"].iloc[-1])
    except Exception:
        return None


def verdict(kind, spot, strike, prog):
    """Karar etiketi: KAPAT / TUT / SINIRDA / ITM. Stop yok — ITM'de bile kural TUT."""
    if prog is not None and prog >= 0.75:
        return "success", "✂️ KAPAT — %75 kâr doldu (GTC emrin yoksa buy-to-close gir)"
    if spot is None:
        return "caption", "spot alınamadı"
    if kind == "CSP":
        if spot < strike:
            return "error", (f"🔴 ITM (spot ${spot:.2f} < strike) — panik satışı YOK, "
                             "vade gününe kadar TUT ve izle; karar vade günü (öneri: kapat)")
        if spot < strike * 1.03:
            return "warning", f"🟠 SINIRDA — spot ${spot:.2f}, strike'a <%3. TUT ve izle"
        return "success", f"🟢 TUT — spot ${spot:.2f}, plan çalışıyor"
    else:  # CC
        if spot > strike:
            return "warning", (f"🟠 ITM (spot ${spot:.2f} > strike) — çağrılma muhtemel; "
                               "hisse strike'tan gider, bu plan dahili. TUT")
        return "success", f"🟢 TUT — spot ${spot:.2f}, prim eriyor lehine"


@st.cache_data(ttl=900, show_spinner=False)
def cc_suggest(sym):
    """Eldeki hisse icin 0.15-0.35 delta call adaylari (TV canli, 15dk cache)."""
    try:
        import tv_options
        df = tv_options.chain(sym, "call", 7, 21)
        if df is None:
            return []
        df = df[(df["delta"] >= 0.15) & (df["delta"] <= 0.35) & (df["spread_pct"] <= 12)]
        return [dict(expiry=str(x.expiry), strike=float(x.strike), mid=float(x.mid),
                     delta=float(x.delta), spread=float(x.spread_pct))
                for x in df.sort_values("delta", ascending=False).head(3).itertuples()]
    except Exception:
        return []


for r in open_pos.itertuples():
    if r.kind == "STOCK":
        # --- 2. vites: elde hisse karti — CC adaylari + KURTARMA PROTOKOLU (onay 2026-08-05) ---
        spot = spot_price(r.sym)
        cost = float(r.fill or 0)
        try:
            age_d = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(r.opened_at)).days
        except Exception:
            age_d = 0
        age_w = age_d // 7 + 1
        depth = (spot / cost - 1) * 100 if (spot and cost) else None
        exit_mode = False
        with st.container(border=True):
            a, b = st.columns([2, 3])
            a.markdown(f"**📦 {int(r.qty or 100)} {r.sym} hisse** · maliyet ${cost:.2f} · "
                       f"{age_w}. hafta")
            if spot:
                stk_pnl = (spot - cost) * int(r.qty or 100)
                a.caption(f"spot \\${spot:.2f} · hisse P&L {stk_pnl:+.0f} USD · {r.note or ''}")
            if depth is None:
                a.caption("spot alınamadı — kurtarma protokolü hesaplanamıyor")
            elif depth <= -15:
                a.error(f"🔴 KURTARMA — DERİNLİK RAYI: maliyetten {depth:.1f}% (ray -%15). "
                        "Sisteme göre bu hafta HİSSEYİ SAT — panik değil, takvim.")
            elif age_d > 84 and depth < 0:
                a.error("🔴 KURTARMA — ZAMAN STOPU: 12 hafta doldu, hâlâ maliyet altı. "
                        "Sisteme göre HİSSEYİ SAT.")
            elif age_d > 35 and depth < 0:
                exit_mode = True
                a.warning(f"🟠 ÇIKIŞ MODU ({age_w}. hafta · maliyetten {depth:.1f}%): "
                          "CC'yi spota YAKIN yaz — maliyet-altı strike bu fazda SERBEST, "
                          "hedef çağrılmak. Zaman stopu: 12. hafta.")
            elif depth < 0:
                a.info(f"🟡 Kurtarma takvimi: {age_w}. hafta (normal faz — strike ≥ maliyet) · "
                       f"derinlik {depth:.1f}% (ray -%15) · çıkış modu 6. haftada başlar. "
                       "TA kırılımı görürsen erkene çekebilirsin — ertelemek yasak.")
            else:
                a.success(f"🟢 maliyet üstü ({depth:+.1f}%) — protokol uykuda. "
                          "CC satmaya uygun (yeşil günde sat)")
            b.markdown("**📞 Güncel CC adayları** (0.15-0.35Δ, canlı):")
            sugg = cc_suggest(r.sym)
            if not sugg:
                b.caption("şu an bantta aday yok / zincir alınamadı")
            for sg in sugg:
                if exit_mode:
                    flag = (" 🎯 çıkış-modu adayı"
                            if (spot and abs(sg["strike"] - spot) / spot < 0.06) else "")
                else:
                    flag = " 🚨 maliyet altı!" if sg["strike"] < cost else ""
                b.markdown(f"`SELL 1 {r.sym} {sg['expiry']} {sg['strike']:g}C @ limit "
                           f"{sg['mid']:.2f}` · Δ{sg['delta']:.2f}{flag}")
            with st.popover("İşlem (hisse satıldı/çağrıldı)"):
                sp_ = st.number_input("Satış fiyatı ($/hisse)", 0.0, 9999.0,
                                      float(spot or cost), 0.01, key=f"sp{r.id}")
                if st.button("Hisse kartını kapat", key=f"sc{r.id}"):
                    pnl = round((sp_ - cost) * int(r.qty or 100), 2)
                    wheel_store.close_wheel(r.id, sp_, "stock_sold", pnl)
                    st.rerun()
        continue
    mid, mid_src = current_mid(r.sym, r.expiry, r.strike, r.kind)
    spot = spot_price(r.sym)
    fill = float(r.fill or 0)
    prog = max(0.0, min(1.0, 1 - mid / fill)) if (mid is not None and fill) else None
    dte_left = (pd.Timestamp(r.expiry).date() - date.today()).days if r.expiry else "?"
    with st.container(border=True):
        a, b, c = st.columns([3, 2, 2])
        a.markdown(f"**{r.sym} {r.expiry} ${float(r.strike):g} "
                   f"{'PUT' if r.kind == 'CSP' else 'CALL'}** × {r.qty}")
        a.caption(f"fill ${fill:.2f} · prim ${float(r.premium or 0):.0f} · "
                  f"başabaş ${float(r.strike) - fill:.2f} · vade {dte_left}g · {r.note or ''}")
        level, msg = verdict(r.kind, spot, float(r.strike), prog)
        itm = spot is not None and ((r.kind == "CSP" and spot < float(r.strike))
                                    or (r.kind == "CC" and spot > float(r.strike)))
        vade_gunu = isinstance(dte_left, int) and dte_left <= 1
        # VADE GUNU (user onayi 2026-09-04): kirmizi ITM kutusu vade gununde GOSTERILMEZ,
        # yerine tek karar notu. ROLL yok (backtest: sistematik roll hesabi sifirladi).
        if vade_gunu and itm:
            a.warning(f"⚖️ VADE GÜNÜ (ITM, {dte_left}g) — **ÖNERİ: KAPAT** (GTC dolmaz, "
                      "buy-to-close gir) · alternatif: assign kabul · Karar: Bora")
        elif vade_gunu and not (prog is not None and prog >= 0.75):
            # OTM + vade gunu: degersiz sonlanir, islem gerekmez
            getattr(a, level if level != "caption" else "caption")(msg)
            a.info(f"⏳ VADE GÜNÜ (OTM, {dte_left}g) — kapanışta değersiz sonlanır, primin "
                   "tamamı kalır. İşlem gerekmez; GTC varsa kendiliğinden dolabilir.")
        else:
            getattr(a, level if level != "caption" else "caption")(msg)
            if vade_gunu and prog is not None and prog >= 0.75:
                a.caption("⏳ vade günü — GTC dolmadıysa kapanışta değersiz sonlanır, fark yok")
        # Erken kapama/roll ipucu: %60-75 arasi kar + vakit varsa
        if prog is not None and 0.60 <= prog < 0.75 and isinstance(dte_left, int) and dte_left >= 4:
            a.caption(f"💡 Erken kapama düşünülebilir: kârın %{prog*100:.0f}'i cepte, kalan "
                      f"${mid:.2f} için {dte_left} gün beklemek şart değil — kapat, "
                      "uygun günde yeni vade sat (yeşil gün CALL, kırmızı gün PUT).")
        # Kiskac hatirlatmasi: CC tasirken nakit varsa ayni isimde CSP
        if r.kind == "CC" and used_collateral < cash:
            a.caption("💡 Kıskaç: hisse taşırken boşta nakit varsa aynı isimde OTM PUT da "
                      "satılabilir (çift taraflı prim — teminatlı strangle).")
        if mid is not None and mid_src == "yf":
            b.error("⚠️ CANLI VERİ YOK — TradingView oturumu düşmüş, fiyat yfinance "
                    "~15dk gecikmeli. Secrets'ta TV_SESSIONID'yi yenile!")
        if prog is not None:
            mtm = (fill - mid) * 100 * abs(int(r.qty or 1))
            src_txt = "canlı" if mid_src == "tv" else "~15dk"
            b.progress(prog, text=f"%75 hedefe ilerleme (hedef ${fill*0.25:.2f})")
            b.markdown(f"güncel **\\${mid:.2f}** ({src_txt}) · MTM **{mtm:+.0f} USD**"
                       + (" — eksi = dalga, karar noktası değil" if mtm < 0 else ""))
        else:
            b.caption("güncel opsiyon fiyatı alınamadı (TV oturumu + yfinance ikisi de yanıtsız)")
        with c.popover("İşlem"):
            cp = st.number_input("Kapanış fiyatı ($)", 0.0, 999.0,
                                 float(mid or 0), 0.01, key=f"cp{r.id}")
            reason = st.selectbox("Sebep", ["closed_75", "expired", "assigned", "manual"],
                                  key=f"rs{r.id}")
            if st.button("Pozisyonu kapat", key=f"cl{r.id}"):
                pnl = round((fill - cp) * 100 * abs(int(r.qty or 1)) - 1.55, 2)
                wheel_store.close_wheel(r.id, cp, reason, pnl)
                if reason == "assigned" and r.kind == "CSP":
                    # hisse otomatik olarak ayri kart olarak acilir (2. vites)
                    eff = round(float(r.strike) - fill, 2)
                    wheel_store.add_wheel(dict(
                        sym=r.sym, kind="STOCK", expiry=None, strike=float(r.strike),
                        qty=100, fill=eff, premium=0,
                        collateral=round(float(r.strike) * 100, 2),
                        note=f"assign kaynağı: {r.expiry} {float(r.strike):g}P"))
                    st.toast(f"{r.sym}: 100 hisse kartı açıldı (maliyet ${eff}) — "
                             "CC adayları kartın içinde!")
                elif reason == "assigned" and r.kind == "CC":
                    st.toast(f"{r.sym} hisseler çağrıldı — STOCK kartını da kapat "
                             f"(satış fiyatı = strike).")
                st.rerun()

with st.expander("➕ Yeni pozisyon kaydet (IBKR'de fill olduktan sonra)"):
    f1, f2, f3, f4, f5 = st.columns(5)
    sym = f1.text_input("Sembol").upper()
    kind = f2.selectbox("Tip", ["CSP", "CC"])
    expiry = f3.date_input("Vade")
    strike = f4.number_input("Strike", 0.0, 9999.0, 30.0, 0.5)
    fill = f5.number_input("Fill ($)", 0.0, 999.0, 1.0, 0.01)
    note = st.text_input("Not", "")
    if st.button("Kaydet") and sym:
        wheel_store.add_wheel(dict(
            sym=sym, kind=kind, expiry=str(expiry), strike=strike, qty=-1,
            fill=fill, premium=round(fill * 100, 2),
            collateral=round(strike * 100, 2) if kind == "CSP" else 0,
            note=note))
        st.rerun()


st.divider()
tab_scan, tab_journal = st.tabs(["🔍 Tarama", "📒 Journal"])



@st.cache_data(ttl=3600, show_spinner=False)
def _leg_betas(syms):
    """Açık bacakların betası — düşük-beta bilgi notu için (saatte 1 tazelenir)."""
    return wheel_scan.beta_of(list(syms))


# ---------- TARAMA ----------
with tab_scan:
    with st.expander("Parametreler", expanded=False):
        p1, p2, p3, p4 = st.columns(4)
        delta_band = p1.slider("Delta bandı (mutlak)", 0.10, 0.40, (0.18, 0.32), 0.01)
        dte = p2.slider("Vade (gün)", 5, 35, (7, 24))
        max_spread = p3.slider("Max spread %", 3.0, 20.0, 10.0, 0.5)
        min_oi = p4.number_input("Min açık pozisyon (OI)", 0, 5000, 100, 50)
        margin_mult = st.slider("Teminat çarpanı (1.0 = cash-secured · üstü = MARGIN/naked bölgesi)",
                                1.0, 3.0, 1.0, 0.5)
        min_vrp = st.slider("Min VRP (IV/RV) — edge eşiği; altı listeye giremez",
                            1.0, 1.5, 1.10, 0.05)
        dyn = st.toggle("🌐 Dinamik evren — tüm ABD piyasası (TV screener: fiyat bandı + "
                        "hacim>2M + mktcap>2B, volatiliteye göre ilk N)", value=True)
        top_n = st.slider("Dinamik evren boyutu (derin taranacak isim)", 20, 100, 50, 5,
                          disabled=not dyn)
        uni_text = st.text_area("Sabit evren (virgülle)", ", ".join(wheel_scan.UNIVERSE),
                                height=100, disabled=dyn)
    if dyn:
        universe = None  # tarama aninda cekilir
    else:
        universe = [s.strip().upper() for s in uni_text.split(",") if s.strip()]

    st.info("Kural hatırlatma: bilanço pencerede olan vade atlanır · sadece taşımaya razı "
            "olduğun hissede sat · limit emir, asla market · kırmızı gün = put satış günü.")

    _spy = spy_day()
    if _spy is not None:
        if _spy <= -1.0:
            st.error(f"🔴 Gün rengi: SPY {_spy:+.1f}% — PUT SATIŞ GÜNÜ, primler şişkin. "
                     "(Kırmızı sabahta CALL satma)")
        elif _spy >= 1.0:
            st.success(f"🟢 Gün rengi: SPY {_spy:+.1f}% — primler ucuz; acele etme. "
                       "(Hisse varken CALL günü)")
        else:
            st.info(f"⚪ Gün rengi: SPY {_spy:+.1f}% — yatay/nötr; aday kalitesi belirleyici.")
    open_syms = set(open_pos["sym"]) if len(open_pos) else set()
    # İsim sayacı (user onayı 2026-09-02): max 4 farklı isim kuralı — SALT BİLGİ, filtre yok
    MAX_NAMES = 4
    st.caption(f"Serbest nakit (tarama filtresi): ${max(cash - used_collateral, 0):,.0f} "
               f"= nakit − açık teminat · Açık semboller: {', '.join(sorted(open_syms)) or 'yok'} "
               f"· **{len(open_syms)}/{MAX_NAMES} isim**")
    if len(open_syms) >= MAX_NAMES:
        st.warning(f"⚠️ İsim tavanı dolu ({len(open_syms)}/{MAX_NAMES}) — kural: yeni isim açma, "
                   "mevcutların kapanmasını bekle. (Aynı isme ek kontrat bütçeye göre serbest.)")
    run_normal = st.button("🔍 TARA", type="primary", use_container_width=True)
    st.markdown("""<style>
    .st-key-deep_btn button{background-color:#1c83e1;border-color:#1c83e1;color:white;}
    .st-key-deep_btn button:hover{background-color:#1668b0;border-color:#1668b0;color:white;}
    .st-key-deep_btn button:active,.st-key-deep_btn button:focus{background-color:#1668b0;border-color:#1668b0;color:white;}
    </style>""", unsafe_allow_html=True)
    run_deep = st.button("🔬 DERİN TARA — tüm uygun evren (~490 isim, 2-4 dk)",
                         key="deep_btn", use_container_width=True)
    if run_normal or run_deep:
        deep = run_deep
        free_cash = max(cash - used_collateral, 0) * margin_mult
        if margin_mult > 1.0:
            st.error(f"🟥 MARGIN MODU ×{margin_mult:g} — bu taramadaki büyük kontratlar naked put "
                     f"olur: assign anında strike×100 tutarı MARGIN BORCUYLA karşılanır. "
                     "27 Temmuz'daki tabloyu hatırla. Boyutu sen seçtin.")
        if universe is None:
            with st.spinner("Aşama 1: TV screener ile tüm ABD piyasası eleniyor..."):
                try:
                    universe = wheel_scan.dynamic_universe(free_cash,
                                                           top_n=500 if deep else top_n)
                    st.caption(f"Dinamik evren ({len(universe)}): {', '.join(universe)}")
                except Exception as ex:
                    st.error(f"TV screener hatası: {ex} — sabit evrene dönüldü")
                    universe = wheel_scan.UNIVERSE
        if deep:
            prog = st.progress(0, text=f"🔬 Derin tarama: 0/{len(universe)}")
            df, notes = wheel_scan.scan_deep(
                universe, free_cash,
                delta_lo=-delta_band[1], delta_hi=-delta_band[0],
                dte_lo=dte[0], dte_hi=dte[1],
                max_spread=max_spread, min_vrp=min_vrp,
                progress=lambda d, t: prog.progress(d / t, text=f"🔬 Derin tarama: {d}/{t}"))
            prog.empty()
        else:
            with st.spinner(f"Aşama 2: {len(universe)} sembolün opsiyon zinciri taranıyor (~1-3 dk)..."):
                df, notes = wheel_scan.scan(
                    universe, free_cash,
                    delta_lo=-delta_band[1], delta_hi=-delta_band[0],
                    dte_lo=dte[0], dte_hi=dte[1],
                    max_spread=max_spread, min_oi=min_oi, min_vrp=min_vrp)
        st.session_state["scan_df"] = df
        st.session_state["scan_notes"] = notes
        st.session_state["scan_ts"] = datetime.now(ET).strftime("%H:%M ET")
        # IV gecmisine yaz — zamanla kendi IV Rank verimiz olusur
        if len(df):
            wheel_store.log_iv([dict(sym=r.sym, spot=r.spot, atm_iv=r.atm_iv, rv20=r.rv20)
                                for r in df.itertuples() if r.atm_iv])

    df = st.session_state.get("scan_df")
    if df is not None and len(df):
        srcs = set(df["src"]) if "src" in df.columns else set()
        if srcs == {"tv"}:
            veri_txt = "🟢 CANLI (TradingView streaming)"
        elif srcs == {"yf"}:
            veri_txt = "⚠️ ~15dk gecikmeli (TV oturumu düşmüş — yfinance yedek)"
        else:
            veri_txt = "karışık: TV canlı + bazı satırlar yfinance ~15dk"
        st.caption(f"Son tarama: {st.session_state.get('scan_ts','?')} · fiyatlar {veri_txt} "
                   "— emir girerken IBKR'deki canlı bid/ask esas")
        for r in df.itertuples():
            ivr = wheel_store.get_iv_rank(r.sym, r.atm_iv) if r.atm_iv else None
            ivr_txt = f" · IVR~{ivr}" if ivr is not None else ""
            renk = "🔴" if r.earn_flag else ("🟢" if r.skor > 2 else "🟡")
            dup = " · 🔁 BU İSİMDE AÇIK POZİSYONUN VAR — üst üste bindirme" if r.sym in open_syms else ""
            with st.container(border=True):
                a, b = st.columns([3, 2])
                if r.earn_flag and r.earnings == "?":
                    emir = ("⚠️ bilanço tarihi DOĞRULANAMADI — satılabilir sayma, "
                            "önce tarihi kontrol et")
                elif r.earn_flag:
                    emir = (f"🚫 SATILMAZ — bilanço {r.earnings} pozisyon penceresinde "
                            f"(bilanço sonrası tekrar bak)")
                else:
                    emir = f"`{r.order}`"
                dchg = getattr(r, "day_chg", None)
                gun = ""
                if dchg is not None:
                    dot = "🔴" if dchg <= -1.5 else ("🟢" if dchg >= 1.5 else "⚪")
                    gun = f" {dot}{dchg:+.1f}%"
                a.markdown(f"**{renk} {r.sym}** ${r.spot}{gun} · {emir}{dup}")
                if not r.earn_flag:
                    a.caption(f"✂️ satış dolunca hemen ekle → BUY 1 aynı kontrat "
                              f"@ limit {getattr(r, 'gtc_target', 0):.2f} · TIF: GTC (%75 kuralı)")
                _b = getattr(r, "beta", None)
                _btxt = (f" · β{_b:.2f}" + (" 🐢" if _b < 0.8 else "")) if _b is not None and _b == _b else ""
                a.caption(f"getiri %{r.yield_pct} / {r.dte}g (haftalık %{r.wk_yield}) · "
                          f"Δ{r.delta} · IV {r.iv:.0%} / RV {r.rv20:.0%} (×{r.iv_rv}){ivr_txt}{_btxt}")
                crush = getattr(r, "crush_flag", "")
                b.caption(f"teminat ${r.collateral:,} · başabaş ${r.breakeven} · "
                          f"spread %{r.spread_pct} · OI {r.oi} · bilanço {r.earnings} "
                          f"{r.earn_flag} {crush}")
        # Düşük-beta bilgi notu (user onayı 2026-08-24): SADECE NOT — filtre/sıralama
        # değişmez. Koşul: açık bacak >= 2 (sıradaki 3.+) VE hepsi yüksek-beta.
        # Dayanak: backtest "3+ bacakta >=1 düşük-beta" MaxDD -17.9 -> -14.4.
        if len(open_syms) >= 2:
            _lb = _leg_betas(tuple(sorted(open_syms)))
            if not any(b is not None and b < 0.8 for b in _lb.values()):
                _lbtxt = ", ".join(f"{s} β{b:.2f}" if b is not None else f"{s} β?"
                                   for s, b in _lb.items())
                st.warning(f"⚠️ Sıradaki bacak 3.+ ve açık pozisyonların hepsi yüksek-beta "
                           f"({_lbtxt}). Düşük-betalı (β<0.8 🐢) adaya öncelik düşün — "
                           "kural defteri: '3+ bacakta ≥1 düşük-beta' "
                           "(backtest: MaxDD -17.9% → -14.4%). Karar senin.")
        with st.expander("Elenenler"):
            for n in st.session_state.get("scan_notes", []):
                st.text(n)
    elif df is not None:
        st.warning("Aday çıkmadı — filtreleri gevşet veya nakiti kontrol et.")


# ---------- JOURNAL ----------
with tab_journal:
    closed = wheel_store.get_wheel(status="closed")
    if len(closed):
        tot_prem = closed["premium"].fillna(0).sum()
        tot_pnl = closed["pnl"].fillna(0).sum()
        wins = (closed["pnl"].fillna(0) > 0).mean() * 100
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Kapanan işlem", len(closed))
        m2.metric("Toplam prim", f"${tot_prem:,.0f}")
        m3.metric("Net P&L", f"${tot_pnl:,.0f}")
        m4.metric("Kazanma oranı", f"%{wins:.0f}")
        st.dataframe(closed[["opened_at", "sym", "kind", "expiry", "strike", "fill",
                             "close_price", "close_reason", "closed_at", "pnl", "note"]],
                     use_container_width=True, hide_index=True)
    else:
        st.info("Kapanan işlem yok henüz.")
    st.caption("Kurallar: prim %75'te kapat · kontrat sayısı nakitin izin verdiği kadar "
               "(margin asla) · 3+ bacakta en az 1 düşük-beta · strike = 'o fiyattan "
               "taşımaya razıyım' testi · bilanço vadeli kontrat satılmaz.")
