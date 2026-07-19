# -*- coding: utf-8 -*-
"""
BORAHODO-DAYTRADE — LUK Model V1 portali.
Sekmeler: CANLI (adaylar+tetikler) · POZISYONLAR (kar% + SAT adaylari) · PLAN · LOG
Kural: sistem [AL adayi]/[SAT adayi] der — KARAR VE EMIR: BORA (TradingView/broker).
"""
import time
from datetime import date
import pandas as pd
import streamlit as st

import storage, engine

st.set_page_config(page_title="borahodo-daytrade", page_icon="📈", layout="wide")
st.title("📈 borahodo-daytrade — LUK Model V1")
st.caption(f"Depo: {storage.backend_name()} · Veri: yfinance (~sn-1dk gecikme) · "
           "Sistem aday söyler, KARAR: BORA · Emirler TradingView/broker ekranından")

REFRESH_SEC = 15


@st.cache_data(ttl=1800, show_spinner=False)
def cached_daily(symbols):
    return engine.daily_context(tuple(sorted(symbols)))


def latest_evening_candidates():
    for p in storage.get_latest_plans(6):
        if p.get("kind") == "evening":
            return p
    return None


tab_live, tab_pos, tab_plan, tab_log = st.tabs(
    ["🔴 CANLI", "💼 Pozisyonlar", "📋 Plan", "📊 Log"])

# ---------------- CANLI ----------------
with tab_live:
    plan = latest_evening_candidates()
    if not plan:
        st.info("Plan yok. Lokalde çalıştır: `python scan.py evening`")
    else:
        st.caption(f"Plan: {plan['for_day']} · Leading termometresi: {plan.get('leading_count')} isim")
        extra = st.text_input("İzlemeye elle isim ekle (virgülle)", key="extra_syms",
                              placeholder="ATAI, TXG")
        cands = list(plan.get("candidates", []))[:15]
        if extra.strip():
            have = {c["sym"] for c in cands}
            for s in [x.strip().upper() for x in extra.split(",") if x.strip()]:
                if s not in have:
                    cands.append(dict(sym=s, setup="elle", trigger=None,
                                      stop_ref=None, max_stop_pct=None))

        @st.fragment(run_every=REFRESH_SEC)
        def live_frag():
            syms = [c["sym"] for c in cands]
            snaps = engine.live_snapshot(syms)
            rows = []
            for c in cands:
                snap = snaps.get(c["sym"])
                state, info = engine.watch_status(c, snap)
                rows.append(dict(
                    Sembol=c["sym"], Setup=c.get("setup", ""),
                    Fiyat=snap["price"] if snap else None,
                    Tetik=c.get("trigger"), Durum=state, Bilgi=info,
                    StopRef=c.get("stop_ref"), MaxStop=c.get("max_stop_pct")))
            df = pd.DataFrame(rows)
            al_rows = df[df["Durum"] == "AL_ADAYI"]
            for _, r in al_rows.iterrows():
                st.warning(f"**[LUK SİSTEMİ: AL adayı] {r.Sembol}** — tetik {r.Tetik} kırıldı "
                           f"| fiyat {r.Fiyat} | stop ref {r.StopRef} (max %{r.MaxStop}) "
                           f"| teyit: hacim + 5dk 9EMA + endeks | KARAR: BORA")
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"Son tazeleme: {time.strftime('%H:%M:%S')} (her {REFRESH_SEC} sn)")

            st.markdown("**AL kaydı** (girdiysen — emri TV/broker'dan verdikten sonra):")
            c1, c2, c3, c4 = st.columns(4)
            sym = c1.selectbox("Sembol", [c["sym"] for c in cands], key="al_sym")
            snap = snaps.get(sym)
            entry = c2.number_input("Giriş", value=float(snap["price"]) if snap else 0.0,
                                    step=0.01, key="al_entry")
            stop = c3.number_input("Stop (girdiğin emir)", value=0.0, step=0.01, key="al_stop")
            if c4.button("AL kaydet", type="primary", key="al_btn"):
                if sym and entry > 0 and 0 < stop < entry:
                    storage.open_position(sym, entry, stop)
                    st.success(f"{sym} kaydedildi — Pozisyonlar sekmesinde canlı izlenecek. "
                               "Stop emrini broker'a girmeyi UNUTMA.")
                else:
                    st.error("Stop, girişin altında ve > 0 olmalı.")
        live_frag()

# ---------------- POZISYONLAR ----------------
with tab_pos:
    @st.fragment(run_every=REFRESH_SEC)
    def pos_frag():
        pos = storage.get_trades(status="open")
        if not len(pos):
            st.info("Açık pozisyon yok. CANLI sekmesinden AL kaydı düşünce burada izlenir.")
            return
        syms = list(pos["sym"].unique())
        snaps = engine.live_snapshot(syms)
        ctxs = cached_daily(syms)
        for _, row in pos.iterrows():
            s = row["sym"]
            stt = engine.position_status(row, snaps.get(s), ctxs.get(s))
            head = engine.FLAG_TR.get(stt["state"], stt["state"])
            with st.container(border=True):
                a, b, c, d, e = st.columns([2, 1, 1, 1, 2])
                a.markdown(f"### {s}  \n{head}")
                b.metric("Kâr %", f"%{stt['kar_pct']}" if stt["kar_pct"] is not None else "—")
                c.metric("R", stt["r"] if stt["r"] is not None else "—")
                d.metric("Stopa uzaklık", f"%{stt.get('stop_dist','—')}")
                e.markdown(f"Giriş {row['entry']} · Stop {row['stop']}  \n"
                           f"9EMA'ya %{stt.get('e9_dist','—')} · 21: %{stt.get('e21_dist','—')} · "
                           f"50: %{stt.get('e50_dist','—')}  \n"
                           f"Gün: %{stt.get('day_chg','—')} · Hacim: {stt.get('vol_x','—')}x")
                for f in stt["flags"]:
                    st.warning(engine.FLAG_TR[f] + " — **KARAR: BORA**")
                x1, x2, x3, x4 = st.columns(4)
                px = snaps.get(s, {}).get("price", 0.0)
                reason = x1.selectbox("Sebep", ["3R trim", "klimaks", "9EMA", "stop",
                                                "direnç/his", "diğer"], key=f"rs{row['id']}")
                if x2.button("SAT-KISMİ %30", key=f"p{row['id']}"):
                    storage.partial_exit(row["id"], px, 30)
                    st.success(f"{s}: %30 çıkış {px} kaydedildi")
                if x3.button("SAT-HEPSİ", key=f"c{row['id']}"):
                    storage.close_position(row["id"], px, reason)
                    st.success(f"{s} kapandı ({reason})")
                x4.caption(f"Anlık: {px}")
        st.caption(f"Son tazeleme: {time.strftime('%H:%M:%S')}")
    pos_frag()

# ---------------- PLAN ----------------
with tab_plan:
    for p in storage.get_latest_plans(4):
        st.subheader(f"{p['for_day']} — {p['kind'].upper()}")
        if p["kind"] == "evening":
            st.dataframe(pd.DataFrame(p.get("candidates", [])),
                         use_container_width=True, hide_index=True)
        else:
            c1, c2 = st.columns(2)
            c1.markdown("**Potent (dünün en iyileri)**")
            c1.dataframe(pd.DataFrame(p.get("potent", [])), hide_index=True)
            c2.markdown("**EP adayları**")
            c2.dataframe(pd.DataFrame(p.get("ep_candidates", [])), hide_index=True)
        st.divider()
    st.caption("Kurallar: günde max ~5 isim SENİN seçimin · tereddüt=pas · endeks düşen "
               "21/50'ye bounce ise giriş yok · binary ~2 hafta içinde ise giriş yok · "
               "stop girişle aynı anda EMİR · piramitleme yok.")

# ---------------- LOG ----------------
with tab_log:
    closed = storage.get_trades(status="closed")
    if len(closed):
        r = closed["r"].astype(float)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Kapanan", len(closed))
        c2.metric("İsabet", f"%{(r > 0).mean() * 100:.0f}")
        c3.metric("Beklenti", f"{r.mean():+.2f}R")
        c4.metric("Toplam", f"{r.sum():+.1f}R")
        st.dataframe(closed, use_container_width=True, hide_index=True)
        st.bar_chart(r)
    else:
        st.info("Kapanmış trade yok.")
