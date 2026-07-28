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


# ---------- baslik + bekci seridi ----------
def market_open_now():
    now = datetime.now(ET)
    return now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)


now = datetime.now(ET)
durum = "🟢 PİYASA AÇIK" if market_open_now() else "🔴 PİYASA KAPALI"
st.title("🎡 borahodo-wheel — Opsiyon Satış Paneli")
st.caption(f"NY: {GUN_TR[now.weekday()]} {now.strftime('%d %b %H:%M')} · {durum} · "
           f"Depo: {wheel_store.backend_name()} · Veri: yfinance ~15dk gecikmeli · "
           "KARAR: BORA · Emirler IBKR'den")

if wheel_store.LAST_ERROR:
    st.error(f"⛔ Depo hatasi — schema_wheel.sql calistirildi mi? ({wheel_store.LAST_ERROR[:90]})")

open_pos = wheel_store.get_wheel(status="open")
used_collateral = float(open_pos["collateral"].fillna(0).sum()) if len(open_pos) else 0.0

c1, c2, c3, c4 = st.columns(4)
cash = c1.number_input("Hesap nakiti ($)", min_value=0, value=int(st.session_state.get("cash", 5100)), step=100)
st.session_state["cash"] = cash
c2.metric("Kullanılan teminat", f"${used_collateral:,.0f}")
oran = used_collateral / cash * 100 if cash else 0
c3.metric("Teminat / nakit", f"%{oran:.0f}")
c4.metric("Açık kontrat", len(open_pos))
if oran > 85:
    st.error("🚨 Teminat/nakit %85 üstü — yeni pozisyon YOK. Cash-secured disiplini: margin asla.")
elif oran > 60:
    st.warning("⚠️ Teminat/nakit %60 üstü — tampon inceliyor.")

tab_scan, tab_pos, tab_journal = st.tabs(["🔍 Tarama", "📌 Pozisyonlar", "📒 Journal"])


# ---------- TARAMA ----------
with tab_scan:
    with st.expander("Parametreler", expanded=False):
        p1, p2, p3, p4 = st.columns(4)
        delta_band = p1.slider("Delta bandı (mutlak)", 0.10, 0.40, (0.18, 0.32), 0.01)
        dte = p2.slider("Vade (gün)", 5, 35, (7, 24))
        max_spread = p3.slider("Max spread %", 3.0, 20.0, 10.0, 0.5)
        min_oi = p4.number_input("Min açık pozisyon (OI)", 0, 5000, 100, 50)
        uni_text = st.text_area("Evren (virgülle)", ", ".join(wheel_scan.UNIVERSE), height=100)
    universe = [s.strip().upper() for s in uni_text.split(",") if s.strip()]

    st.info("Kural hatırlatma: bilanço pencerede olan vade atlanır · sadece taşımaya razı "
            "olduğun hissede sat · limit emir, asla market · kırmızı gün = put satış günü.")

    open_syms = set(open_pos["sym"]) if len(open_pos) else set()
    st.caption(f"Serbest nakit (tarama filtresi): ${max(cash - used_collateral, 0):,.0f} "
               f"= nakit − açık teminat · Açık semboller: {', '.join(sorted(open_syms)) or 'yok'}")
    if st.button("🔍 TARA", type="primary", use_container_width=True):
        with st.spinner(f"{len(universe)} sembol taranıyor (~1-2 dk)..."):
            free_cash = max(cash - used_collateral, 0)
            df, notes = wheel_scan.scan(
                universe, free_cash,
                delta_lo=-delta_band[1], delta_hi=-delta_band[0],
                dte_lo=dte[0], dte_hi=dte[1],
                max_spread=max_spread, min_oi=min_oi)
        st.session_state["scan_df"] = df
        st.session_state["scan_notes"] = notes
        st.session_state["scan_ts"] = datetime.now(ET).strftime("%H:%M ET")
        # IV gecmisine yaz — zamanla kendi IV Rank verimiz olusur
        if len(df):
            wheel_store.log_iv([dict(sym=r.sym, spot=r.spot, atm_iv=r.atm_iv, rv20=r.rv20)
                                for r in df.itertuples() if r.atm_iv])

    df = st.session_state.get("scan_df")
    if df is not None and len(df):
        st.caption(f"Son tarama: {st.session_state.get('scan_ts','?')} · fiyatlar ~15dk "
                   "gecikmeli — emir girerken IBKR'deki canlı bid/ask esas")
        for r in df.itertuples():
            ivr = wheel_store.get_iv_rank(r.sym, r.atm_iv) if r.atm_iv else None
            ivr_txt = f" · IVR~{ivr}" if ivr is not None else ""
            renk = "🟢" if r.skor > 2 else "🟡"
            dup = " · 🔁 BU İSİMDE AÇIK POZİSYONUN VAR — üst üste bindirme" if r.sym in open_syms else ""
            with st.container(border=True):
                a, b = st.columns([3, 2])
                a.markdown(f"**{renk} {r.sym}** ${r.spot} · `{r.order}`{dup}")
                a.caption(f"getiri %{r.yield_pct} / {r.dte}g (haftalık %{r.wk_yield}) · "
                          f"Δ{r.delta} · IV {r.iv:.0%} / RV {r.rv20:.0%} (×{r.iv_rv}){ivr_txt}")
                b.caption(f"teminat ${r.collateral:,} · başabaş ${r.breakeven} · "
                          f"spread %{r.spread_pct} · OI {r.oi} · bilanço {r.earnings} {r.earn_flag}")
        with st.expander("Elenenler"):
            for n in st.session_state.get("scan_notes", []):
                st.text(n)
    elif df is not None:
        st.warning("Aday çıkmadı — filtreleri gevşet veya nakiti kontrol et.")


# ---------- POZISYONLAR ----------
def current_mid(sym, expiry, strike, kind):
    try:
        import yfinance as yf
        ch = yf.Ticker(sym).option_chain(str(expiry))
        leg = ch.puts if kind == "CSP" else ch.calls
        row = leg[leg["strike"] == float(strike)]
        if len(row):
            bid, ask = float(row["bid"].iloc[0]), float(row["ask"].iloc[0])
            if bid > 0 or ask > 0:
                return round((bid + ask) / 2, 2)
        return None
    except Exception:
        return None


with tab_pos:
    if not len(open_pos):
        st.info("Açık pozisyon yok.")
    for r in open_pos.itertuples():
        mid = current_mid(r.sym, r.expiry, r.strike, r.kind)
        fill = float(r.fill or 0)
        prog = max(0.0, min(1.0, 1 - mid / fill)) if (mid is not None and fill) else None
        dte_left = (pd.Timestamp(r.expiry).date() - date.today()).days if r.expiry else "?"
        with st.container(border=True):
            a, b, c = st.columns([3, 2, 2])
            a.markdown(f"**{r.sym} {r.expiry} ${float(r.strike):g} "
                       f"{'PUT' if r.kind == 'CSP' else 'CALL'}** × {r.qty}")
            a.caption(f"fill ${fill:.2f} · prim ${float(r.premium or 0):.0f} · "
                      f"başabaş ${float(r.strike) - fill:.2f} · vade {dte_left}g · {r.note or ''}")
            if prog is not None:
                b.progress(prog, text=f"kâr %{prog*100:.0f} (güncel ${mid:.2f})")
                if prog >= 0.75:
                    b.success("✂️ %75 doldu — KAPAT (buy-to-close)")
            else:
                b.caption("güncel fiyat alınamadı")
            with c.popover("İşlem"):
                cp = st.number_input("Kapanış fiyatı ($)", 0.0, 999.0,
                                     float(mid or 0), 0.01, key=f"cp{r.id}")
                reason = st.selectbox("Sebep", ["closed_75", "expired", "assigned", "manual"],
                                      key=f"rs{r.id}")
                if st.button("Pozisyonu kapat", key=f"cl{r.id}"):
                    pnl = round((fill - cp) * 100 * abs(int(r.qty or 1)) - 1.55, 2)
                    wheel_store.close_wheel(r.id, cp, reason, pnl)
                    if reason == "assigned":
                        st.toast(f"{r.sym} assign — pazartesi ~0.25Δ covered call planla!")
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
