# borahodo-daytrade — LUK Model V1 Portalı

Sistem [AL adayı]/[SAT adayı] üretir; **karar ve emir her zaman Bora'da**
(TradingView + broker ekranı). Model: grid_bot_US_stocks/LUK_MODEL_V1.md.

## Mimari
- **Portal (bu repo, Streamlit Cloud):** CANLI (adaylar+tetik, 15 sn tazeleme,
  sayfa açıkken) · Pozisyonlar (kâr%, R, 4 SAT koşulu) · Plan · Log
- **Veri:** yfinance — kimliksiz, ~sn-1dk gecikme. TV penceren grafik/teyit ekranın.
- **Kayıt:** Supabase (`schema.sql`). AL tıkla → pozisyon izlemeye girer;
  SAT tıkla → R hesaplanır, log birikir. Secrets girilmemişse lokal CSV test modu.
- **Tarama (lokalde koşar):** `python scan.py evening` (akşam) / `premarket`
  (~14:45 CET). Supabase varsa planı buluta da yazar → portal her yerden görür.

## Kurulum (bir kez, ~10 dk — Bora)
1. **Supabase:** app.supabase.com → New project → SQL Editor → `schema.sql`'i
   çalıştır → Settings > API'den `URL` ve `anon key`'i al.
2. **Streamlit Cloud:** share.streamlit.io → New app → repo `hodobora/borahodo-daytrade`,
   dosya `streamlit_app.py`, App URL: **borahodo-daytrade** → Advanced settings >
   Secrets'a yapıştır:
   ```
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "eyJ..."
   ```
3. **Lokal tarama için** (planın buluta gitmesi): PC'de ortam değişkeni olarak aynı
   ikisini ayarla (`setx SUPABASE_URL ...` / `setx SUPABASE_KEY ...`) — bir kez.

## Günlük akış (CET)
- Akşam 22:05+ (veya sabah): `python scan.py evening` → plan portala düşer
- ~14:45: `python scan.py premarket` → EP/potent listesi
- 15:30+: portal telefonda/işte AÇIK sekme — [AL adayı] belirince TV'de teyit,
  emri TV/broker'dan ver, portalda **AL kaydet** (giriş+stop) → izleme başlar
- Pozisyonlar sekmesi: kâr%, R, stopa uzaklık, 9/21/50 EMA mesafeleri canlı;
  koşul oluşunca [SAT adayı] uyarısı — ister 1 saat ister 3 hafta sonra
- SAT-KISMİ %30 (3R trim) / SAT-HEPSİ → log otomatik

## Sert kurallar
Günde max ~5 isim senin seçimin · tereddüt = pas · endeks düşen 21/50'ye bounce →
giriş yok · binary ~2 hafta içinde → giriş yok · **stop girişle aynı anda broker'da
EMİR** (portal uyarısı emrin yerine geçmez) · piramitleme yok.
