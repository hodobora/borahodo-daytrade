# -*- coding: utf-8 -*-
"""
GitHub Actions bekcisi: NY saatine bakar, dogru moddaysa taramayi kosar.
Cron hem EDT hem EST saatine kurulu (12:45/13:45 ve 20:10/21:10 UTC) —
bu bekci sayesinde yaz/kis gecisinde HICBIR ayar gerekmez:
yanlis saatte uyanan kosu sessizce cikar.
  NY 08:xx  -> premarket taramasi
  NY 16:xx  -> evening taramasi (ertesi islem gununun plani)
"""
import sys, os
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

et = datetime.now(ZoneInfo("America/New_York"))
if et.weekday() >= 5:
    print(f"NY {et:%a %H:%M} — hafta sonu, cikiliyor")
    sys.exit(0)
if et.hour == 8:
    mode = "premarket"
elif et.hour == 16:
    mode = "evening"
else:
    print(f"NY {et:%a %H:%M} — pencere disi (DST ikizi), sessiz cikis")
    sys.exit(0)

print(f"NY {et:%a %H:%M} — {mode} taramasi basliyor")
import scan
(scan.premarket if mode == "premarket" else scan.evening)()
print("bitti")
