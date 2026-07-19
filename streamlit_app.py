# -*- coding: utf-8 -*-
"""
BORAHODO-DAYTRADE — LUK Model V1 portali.
CANLI: SADECE tetiklenen adaylar (izleme arka planda) + tek tik AL + altta portfoy.
Kayitli pozisyonda fiyat stopa degerse portal kaydi OTOMATIK kapatir (stop).
Gercek koruma broker'daki stop emridir — portal kayit/izleme katmanidir.
"""
import time
from datetime import date
import pandas as pd
import streamlit as st

import storage, engine

st.set_page_config(page_title="borahodo-daytrade", page_icon="📈", layout="wide")
st.title("📈 borahodo-daytrade — LUK Model V1")
st.caption(f"Depo: {storage.backend_name()} · Veri: yfinance (~sn-1dk gecikme) · "
           "KARAR: BORA · Emirler TradingView/broker ekranından")
if storage.LAST_ERROR:
    st.warning(storage.LAST_ERROR)

REFRESH_SEC = 15


def _tv_sessionid():
    import os
    v = os.environ.get("TV_SESSIONID")
    if not v:
        try:
            v = st.secrets.get("TV_SESSIONID")
        except Exception:
            v = None
    return v


def get_live(symbols):
    """Canli veri = SADECE TradingView (2026-07-19 Bora karari: yedek yok).
    TV olur/bayatlarsa portal KOR kalir + kirmizi alarm — duzeltilene kadar
    hicbir fiyat gosterilmez (yanlis fiyat > hic fiyat DEGILDIR)."""
    sid = _tv_sessionid()
    if not sid:
        return {}, lambda: st.error(
            "⛔ CANLI İZLEME KAPALI — TV_SESSIONID girilmemiş. "
            "Secrets'a ekle (F12 → Application → Cookies → tradingview.com → sessionid).")
    snaps, state = engine.tv_snapshot(symbols, sid)
    if state == 'tv':
        return snaps, lambda: st.success(
            "🟢 Veri: TradingView CANLI (gerçek zamanlı, senin aboneliğin)")
    if state == 'tv_delayed':
        return {}, lambda: st.error(
            "🔴 TV verisi GECİKMELİ/BAYAT — çerez ölmüş olabilir. İzleme DURDU; "
            "TV_SESSIONID'yi yenile (F12 → Cookies → sessionid → Secrets'i güncelle). "
            "Yenileyene kadar fiyat GÖSTERİLMEZ.")
    return {}, lambda: st.error(
        "⛔ TV BAĞLANTISI KOPTU — izleme DURDU. TV_SESSIONID'yi yenile; "
        "düzelene kadar fiyat GÖSTERİLMEZ. (Pozisyon korumam broker'daki stop emrin!)")


@st.cache_data(ttl=1800, show_spinner=False)
def cached_daily(symbols):
    return engine.daily_context(tuple(sorted(symbols)))


@st.cache_data(ttl=240, show_spinner=False)
def cached_orh(symbols, day):
    return engine.get_orh(tuple(sorted(symbols)))


def latest_evening_candidates():
    for p in storage.get_latest_plans(6):
        if p.get("kind") == "evening":
            return p
    return None


def compute_stop(price, day_low, max_stop_pct):
    """Tek-tik AL icin stop: gunun low'u; cok uzaksa max-stop tavani (model kurali)."""
    ms = (max_stop_pct or 5.0) / 100.0
    if day_low and day_low < price and (price / day_low - 1) <= ms:
        return round(day_low, 2)
    return round(price * (1 - ms), 2)


tab_live, tab_plan, tab_log = st.tabs(["🔴 CANLI", "📋 Plan", "📊 Log"])

# ---------------- CANLI ----------------
with tab_live:
    plan = latest_evening_candidates()
    # Plan ne urettiyse HEPSI izlenir (teknik guvenlik: 60 ustu tek istekte yavaslar,
    # asilirsa en guclu 60 alinir ve ekranda soylenir)
    cands = list(plan.get("candidates", [])) if plan else []
    if len(cands) > 60:
        st.warning(f"Plan {len(cands)} aday uretti — tazeleme hizi icin en guclu 60'i izleniyor.")
        cands = cands[:60]
    extra = st.text_input("İzlemeye elle isim ekle (virgülle)", key="extra_syms",
                          placeholder="ATAI, TXG")
    if extra.strip():
        have = {c["sym"] for c in cands}
        for s in [x.strip().upper() for x in extra.split(",") if x.strip()]:
            if s not in have:
                cands.append(dict(sym=s, setup="elle", trigger=None,
                                  stop_ref=None, max_stop_pct=None))

    @st.fragment(run_every=REFRESH_SEC)
    def live_frag():
        # ---- arka plan izleme (TV-oncelikli veri) ----
        watch_syms = [c["sym"] for c in cands]
        pos = storage.get_trades(status="open")
        pos_syms = list(pos["sym"].unique()) if len(pos) else []
        snaps, banner = get_live(sorted(set(watch_syms + pos_syms)))
        banner()
        ctx_all = cached_daily(watch_syms) if watch_syms else {}
        orh_map = cached_orh(watch_syms, str(date.today())) if watch_syms else {}

        # ---- SADECE "LUK ALIRDI" DURUMU: fiyat tetigin USTUNDE ----
        # (tetiklenip geri dusenler EKRANA GELMEZ — arka planda izlenir,
        #  tekrar keserse tekrar belirir)
        st.subheader("⚡ Tetiklenenler")
        triggered = []
        for c in cands:
            snap = snaps.get(c["sym"])
            if not snap:
                continue
            price = snap["price"]
            levels = []
            if c.get("trigger"):
                levels.append(("5a PDH", float(c["trigger"])))
            # 5b ORH: gap acilista (acilis > dun kapanis) ilk 1dk mumun high'i (Luk 38:18)
            orh = orh_map.get(c["sym"])
            ctx = ctx_all.get(c["sym"])
            if orh and ctx and snap.get("day_open") and snap["day_open"] > ctx["prev_close"]:
                levels.append(("5b ORH", round(orh["orh"], 2)))
            crossed = [(nm, lv) for nm, lv in levels if price >= lv]
            if crossed:
                nm, lv = max(crossed, key=lambda x: x[1])  # kirilan en yuksek seviye esas
                c2 = dict(c); c2["setup"] = nm; c2["trigger"] = lv
                triggered.append((c2, snap))
        if not triggered:
            n = len([c for c in cands if c.get("trigger")])
            st.caption(f"Tetik yok — {n} isim arka planda izleniyor · "
                       f"son tazeleme {time.strftime('%H:%M:%S')} (her {REFRESH_SEC} sn)")
        else:
            h = st.columns([1.2, 1.6, 1, 1, 1.6, 1, 0.9])
            for col, t in zip(h, ["**Ticker**", "**Setup**", "**Fiyat**", "**Tetik**",
                                  "**Durum**", "**Stop**", ""]):
                col.markdown(t)
            for c, snap in triggered:
                price = snap["price"]
                stop_now = compute_stop(price, snap["day_low"], c.get("max_stop_pct"))
                durum = "🟢 [LUK: AL adayı]"
                r = st.columns([1.2, 1.6, 1, 1, 1.6, 1, 0.9])
                r[0].markdown(f"**{c['sym']}**")
                r[1].write(c.get("setup", ""))
                r[2].write(f"{price:.2f}")
                r[3].write(f"{c['trigger']}")
                r[4].write(durum)
                r[5].write(f"{stop_now:.2f}")
                if r[6].button("AL", key=f"al_{c['sym']}", type="primary",
                               disabled=c["sym"] in pos_syms):
                    storage.open_position(c["sym"], price, stop_now,
                                          note=c.get("setup", ""))
                    st.toast(f"{c['sym']} kaydedildi: giriş {price:.2f}, stop {stop_now:.2f}. "
                             "Broker'a stop emrini girmeyi unutma!", icon="✅")
                    st.rerun(scope="fragment")

        st.divider()

        # ---- PORTFOY ----
        st.subheader("💼 Portföy")
        pos = storage.get_trades(status="open")
        if not len(pos):
            st.caption("Açık pozisyon yok.")
        else:
            ctxs = cached_daily(list(pos["sym"].unique()))
            h = st.columns([1.2, 1, 1, 1, 1, 2.2, 0.8, 0.8])
            for col, t in zip(h, ["**Ticker**", "**Giriş**", "**Stop**", "**Kâr %**",
                                  "**R**", "**Durum**", "", ""]):
                col.markdown(t)
            for _, row in pos.iterrows():
                s = row["sym"]
                snap = snaps.get(s)
                stt = engine.position_status(row, snap, ctxs.get(s))
                # OTOMATIK STOP: fiyat stopa degdiyse kaydi kapat
                if snap and snap["price"] <= float(row["stop"]):
                    storage.close_position(row["id"], float(row["stop"]), "stop (otomatik)")
                    st.error(f"🔴 {s}: fiyat stopa değdi ({row['stop']}) — kayıt otomatik "
                             "kapatıldı. Broker emrinin çalıştığını kontrol et!")
                    continue
                r = st.columns([1.2, 1, 1, 1, 1, 2.2, 0.8, 0.8])
                r[0].markdown(f"**{s}**")
                r[1].write(f"{float(row['entry']):.2f}")
                r[2].write(f"{float(row['stop']):.2f}")
                kar = stt["kar_pct"]
                r[3].markdown(f"**{'🟢' if (kar or 0) >= 0 else '🔴'} %{kar}**"
                              if kar is not None else "—")
                r[4].write(stt["r"] if stt["r"] is not None else "—")
                flags = [engine.FLAG_TR[f] for f in stt["flags"] if f != "STOP_YENDI"]
                r[5].write(" · ".join(flags) if flags else "🟢 TUT")
                if r[6].button("TUT", key=f"tut_{row['id']}"):
                    st.toast(f"{s}: TUT — pozisyon devam", icon="🟢")
                if r[7].button("SAT", key=f"sat_{row['id']}"):
                    px = snap["price"] if snap else float(row["entry"])
                    storage.close_position(row["id"], px, "manuel SAT")
                    st.toast(f"{s} kapatıldı @ {px:.2f}", icon="💰")
                    st.rerun(scope="fragment")
        st.caption(f"Son tazeleme: {time.strftime('%H:%M:%S')} · Stop otomatiği yalnızca "
                   "sayfa açıkken çalışır — asıl koruma broker'daki stop emrin.")
    live_frag()

# ---------------- PLAN ----------------
with tab_plan:
    for p in storage.get_latest_plans(4):
        st.subheader(f"{p['for_day']} — {p['kind'].upper()}")
        if p["kind"] == "evening":
            st.caption(f"Leading termometresi: {p.get('leading_count')} isim")
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
               "stop girişle aynı anda broker'da EMİR · piramitleme yok.")

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
