# -*- coding: utf-8 -*-
"""
BORAHODO-DAYTRADE — LUK Model V1 portali.
CANLI: SADECE tetiklenen adaylar (izleme arka planda) + tek tik AL + altta portfoy.
Kayitli pozisyonda fiyat stopa degerse portal kaydi OTOMATIK kapatir (stop).
Gercek koruma broker'daki stop emridir — portal kayit/izleme katmanidir.
"""
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

import storage, engine

ET = ZoneInfo("America/New_York")
GUN_TR = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def ny_clock_line():
    now = datetime.now(ET)
    durum = "🟢 PİYASA AÇIK" if engine._market_open_now() else "🔴 PİYASA KAPALI"
    return f"NY: {GUN_TR[now.weekday()]} {now.strftime('%d %b %H:%M')} · {durum}"


def et_today():
    return datetime.now(ET).date()

st.set_page_config(page_title="borahodo-daytrade", page_icon="📈", layout="wide")
st.title("📈 borahodo-daytrade — LUK Model V1")
st.caption(f"**{ny_clock_line()}** · Depo: {storage.backend_name()} · "
           "KARAR: BORA · Emirler TradingView/broker ekranından")
storage.get_trades(status="closed")   # tablo varligini yokla (LAST_ERROR'u doldurur)
if storage.LAST_ERROR:
    st.error("⛔ KAYIT KALICI DEĞİL — Supabase tabloları kurulmamış! "
             "SQL Editor'da schema.sql'i çalıştırmadan AL kaydı yapma. "
             f"({storage.LAST_ERROR[:90]})")

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
        # ---- arka plan izleme (TV-oncelikli veri; SPY/QQQ endeks filtresi dahil) ----
        IDX = ["SPY", "QQQ"]
        watch_syms = [c["sym"] for c in cands]
        pos = storage.get_trades(status="open")
        pos_syms = list(pos["sym"].unique()) if len(pos) else []
        snaps, banner = get_live(sorted(set(watch_syms + pos_syms + IDX)))
        banner()
        ctx_all = cached_daily(watch_syms) if watch_syms else {}
        orh_map = cached_orh(watch_syms, str(date.today())) if watch_syms else {}

        # ---- ENDEKS FILTRESI (Luk 117:58 — dusen 21/50'ye bounce = fakeout bolgesi) ----
        idx_ctx = cached_daily(tuple(IDX))
        idx_warns = []
        for ix in IDX:
            sx, kx = snaps.get(ix), idx_ctx.get(ix)
            if not sx or not kx:
                continue
            for nm, ev, down in (("21", kx["e21"], kx["e21_down"]),
                                 ("50", kx["e50"], kx["e50_down"])):
                if down and abs(sx["price"] / ev - 1) < 0.02:
                    idx_warns.append(f"{ix} düşen EMA{nm} bölgesinde ({sx['price']:.0f} vs {ev:.0f})")
        if idx_warns:
            st.warning("🟠 **ENDEKS UYARISI:** " + " · ".join(idx_warns) +
                       " — Luk bu bölgede iyi setup'ı bile pas geçer (fakeout alanı). KARAR: BORA")
        else:
            st.caption("🟢 Endeks temiz (SPY ✓ QQQ ✓ — düşen 21/50 bounce bölgesi yok)")

        # ---- SADECE "LUK ALIRDI" DURUMU: fiyat tetigin USTUNDE ----
        # (tetiklenip geri dusenler EKRANA GELMEZ — arka planda izlenir,
        #  tekrar keserse tekrar belirir)
        st.subheader("⚡ Tetiklenenler")
        rth_open = engine._market_open_now()
        if not rth_open:
            st.caption("🔴 Piyasa kapalı — tetikler yalnızca normal seansta (15:30-22:00 CET) "
                       "değerlendirilir; pre/after-market fiyatı AL sinyali ÜRETMEZ (Luk kuralı). "
                       f"{len([c for c in cands if c.get('trigger')])} isim planda hazır bekliyor.")
        # LUK KURALI (2026-07-20 Bora: "sadece Luk alacagi zaman ciksin"):
        #  - Acilis tetigin ALTINDA/ESITINDE -> giris ani = PDH kirilimi
        #  - Acilis tetigin USTUNDE (gap) -> PDH GECERSIZ; giris ani = ORH kirilimi
        #    (Luk 38:18; ORH verisi henuz yoksa satir GOSTERILMEZ — erken dakikalar)
        triggered = []
        for c in cands if rth_open else []:
            snap = snaps.get(c["sym"])
            trig = c.get("trigger")
            if not snap or not trig:
                continue
            price, day_open = snap["price"], snap.get("day_open")
            nm = lv = None
            if day_open is not None and day_open > float(trig):
                orh = orh_map.get(c["sym"])
                if orh and price >= orh["orh"]:
                    nm, lv = "5b ORH (gap)", round(orh["orh"], 2)
            elif price >= float(trig):
                nm, lv = "5a PDH", float(trig)
            if nm:
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
                durum = "🟢 [LUK: AL adayı]" + (" ⚠ endeks" if idx_warns else "")
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
            rth = engine._market_open_now()
            for _, row in pos.iterrows():
                s = row["sym"]
                snap = snaps.get(s)
                stt = engine.position_status(row, snap, ctxs.get(s))
                # UZATILMIS SEANS (pre/after): sadece IZLEME + uyari — otomatik kapatma YOK
                if not rth and snap and snap.get("ext_price"):
                    ep, ec = snap["ext_price"], snap.get("ext_chg") or 0
                    ext_kar = (ep / float(row["entry"]) - 1) * 100
                    st.info(f"🌙 **{s}** uzatılmış seans: {ep:.2f} ({ec:+.1f}%) · "
                            f"pozisyon kârı %{ext_kar:+.1f}")
                    r_unit = float(row["entry"]) - float(row["stop"])
                    if r_unit > 0 and (ep - float(row["entry"])) / r_unit >= 4 and ec >= 8:
                        st.warning(f"🟠 {s}: uzatılmış seansta PARABOLİK (+{ec:.1f}%) — "
                                   "Luk burada güce satardı (GME/AMC pre-market çıkışları). "
                                   "IBKR'de uzatılmış seans emri verebilirsin. KARAR: BORA")
                    if ep <= float(row["stop"]):
                        # 2026-07-19 Bora karari: IBKR stoplari 'outside RTH' isaretli —
                        # uzatilmis seansta da stop gercekten calisir, kayit da kapanir
                        storage.close_position(row["id"], float(row["stop"]),
                                               "stop (otomatik, uzatılmış seans)")
                        st.error(f"🔴 {s}: uzatılmış seansta stopa değdi ({row['stop']}) — kayıt "
                                 "otomatik kapatıldı. IBKR emrinin 'outside RTH' işaretli olduğunu "
                                 "ve gerçekten dolduğunu kontrol et!")
                        continue
                # OTOMATIK STOP: normal seansta fiyat stopa degdiyse kaydi kapat
                if rth and snap and snap["price"] <= float(row["stop"]):
                    storage.close_position(row["id"], float(row["stop"]), "stop (otomatik)")
                    st.error(f"🔴 {s}: fiyat stopa değdi ({row['stop']}) — kayıt otomatik "
                             "kapatıldı. Broker emrinin çalıştığını kontrol et!")
                    continue
                # ---- OTOMATIK SATIS KATMANI (2026-07-20 Bora: 'TUT ve SAT'i kendisi
                # yapsin, %100 Luk'a gore — sadece AL bende') ----
                if rth and snap:
                    px_now = snap["price"]
                    # KLIMAKS: 4R+ ve gun >= +%8 ve hacim 2x -> hepsini guce sat (Luk 77:20)
                    if "SAT_KLIMAKS" in stt["flags"]:
                        storage.close_position(row["id"], px_now, "klimaks (otomatik)")
                        st.warning(f"🟠 {s}: KLİMAKS — sistem tümünü {px_now:.2f}'den kapattı "
                                   "(Luk: parabolik hacimde güce satış). Broker'da da sat!")
                        continue
                    # 3R TRIM: %30 guce sat, bir kez (Luk 41:00)
                    if "SAT_3R_TRIM" in stt["flags"]:
                        storage.partial_exit(row["id"], px_now, 30)
                        st.warning(f"🟡 {s}: 3R — sistem %30'u {px_now:.2f}'den trimledi "
                                   "(Luk kuralı). Broker'da da %30 sat!")
                    # 9EMA: Luk'ta GUNLUK KAPANIS kurali -> kapanis penceresinde (15:45-16 ET)
                    if "SAT_9EMA_ALTI" in stt["flags"] and engine._near_close_now():
                        storage.close_position(row["id"], px_now, "9EMA kapanis (otomatik)")
                        st.warning(f"🟡 {s}: kapanışa doğru günlük 9EMA altında — sistem "
                                   f"{px_now:.2f}'den kapattı (Luk trailing). Broker'da da sat!")
                        continue
                    # GUN SONU TASFIYE — C varyanti (Bora 2026-07-20): test yasa degil
                    # DAVRANISA bakar. "Calisiyor" = fiyat tetiginin ustunde tutunuyor
                    # (tetik kayitta yok; giris fiyati vekil — AL tetik aninda basildigi
                    # icin giris ≈ tetik). Tutunanlar yasina bakilmaksizin geceye gider;
                    # tetigin ALTINA sarkmis + kararsiz (-0.3<R<0) olan kesilir.
                    if engine._flat_cut_window_now() and not row.get("partial_price"):
                        opened_et = pd.to_datetime(row["opened_at"], utc=True).tz_convert(
                            "America/New_York").date()
                        holding = px_now >= float(row["entry"])
                        if (opened_et == et_today() and not holding
                                and stt["r"] is not None and abs(stt["r"]) < 0.3):
                            storage.close_position(row["id"], px_now,
                                                   "gün sonu tasfiye (tetik altı, çalışmadı)")
                            st.info(f"✂️ {s}: kapanışa giderken tetiğinin altında ve kararsız "
                                    f"(R {stt['r']:+.2f}) — {px_now:.2f}'den tasfiye "
                                    "(Luk 27:13). Broker'da da kapat!")
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
        # ---- BUGUN KAPANANLAR (kalici serit; "bugun" = NY islem gunu) ----
        closed = storage.get_trades(status="closed")
        if len(closed):
            et_dates = pd.to_datetime(closed["closed_at"], utc=True).dt.tz_convert(
                "America/New_York").dt.date
            closed_today = closed[et_dates == et_today()]
            if len(closed_today):
                st.subheader("📕 Bugün kapananlar")
                for _, r in closed_today.iterrows():
                    icon = "🔴" if "stop" in str(r["exit_reason"]) else "💰"
                    st.markdown(f"{icon} **{r['sym']}** — {r['exit_reason']} · "
                                f"çıkış {r['exit_price']} · **R {r['r']:+.2f}**")
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
def _trade_pct(row):
    """Esit-boyut varsayimiyla trade'in % getirisi (kismi cikis dahil, karma)."""
    try:
        entry = float(row["entry"])
        rem, pct = 1.0, 0.0
        if row.get("partial_price") and float(row.get("partial_pct") or 0) > 0:
            w = float(row["partial_pct"]) / 100.0
            pct += w * (float(row["partial_price"]) / entry - 1)
            rem -= w
        pct += rem * (float(row["exit_price"]) / entry - 1)
        return round(pct * 100, 2)
    except Exception:
        return None


with tab_log:
    closed = storage.get_trades(status="closed")
    if len(closed):
        closed = closed.copy()
        closed["kar_%"] = closed.apply(_trade_pct, axis=1)
        r = closed["r"].astype(float)
        pct = closed["kar_%"].astype(float)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Kapanan", len(closed))
        c2.metric("İsabet", f"%{(r > 0).mean() * 100:.0f}")
        c3.metric("Ort. trade", f"%{pct.mean():+.2f}")
        c4.metric("Toplam (eşit boyut)", f"%{pct.sum():+.1f}")
        c5.metric("Toplam R", f"{r.sum():+.1f}R")
        st.caption("Yüzdeler EŞİT pozisyon boyutu varsayımıyla — 'Toplam', trade "
                   "yüzdelerinin basit toplamı (pozisyon başına eşit sermaye dilimi).")

        # Haftalık özet
        closed["hafta"] = pd.to_datetime(closed["closed_at"]).dt.strftime("%G-W%V")
        wk = (closed.groupby("hafta")
              .agg(n=("sym", "count"),
                   isabet=("kar_%", lambda x: round((x > 0).mean() * 100)),
                   ort_pct=("kar_%", lambda x: round(x.mean(), 2)),
                   toplam_pct=("kar_%", lambda x: round(x.sum(), 1)),
                   toplam_R=("r", lambda x: round(x.sum(), 1)))
              .reset_index().sort_values("hafta", ascending=False))
        wk.columns = ["Hafta", "Trade", "İsabet %", "Ort %", "Toplam %", "Toplam R"]
        st.subheader("📅 Haftalık özet")
        st.dataframe(wk, use_container_width=True, hide_index=True)

        st.subheader("Tüm kayıtlar")
        st.dataframe(closed, use_container_width=True, hide_index=True)
        st.bar_chart(closed.set_index("sym")["kar_%"])
    else:
        st.info("Kapanmış trade yok.")
