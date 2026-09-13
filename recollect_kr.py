# -*- coding: utf-8 -*-
"""
한국 시세 캐시를 처음부터 다시 받는다 (open/high/low/volume/market_cap/
shares 컬럼 추가 후 첫 실행용).

auto_sepa_new 폴더에서 실행: python3 recollect_kr.py
"""
import os
import datetime as dt


# ── .env 자동 로딩 ────────────────────────────────────────────
def _load_env():
    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        from dotenv import load_dotenv
        load_dotenv(_path)
        return
    except ImportError:
        pass
    if not os.path.exists(_path):
        return
    with open(_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


_load_env()

if not os.environ.get("KRX_API_KEY"):
    raise SystemExit("KRX_API_KEY가 여전히 없습니다. .env 파일 위치와 내용을 확인해주세요.")

from kr_data_fdr import fetch_kr_krx_open

end = dt.date.today().strftime("%Y%m%d")
start = (dt.date.today() - dt.timedelta(days=420)).strftime("%Y%m%d")

print(f"수집 범위: {start} ~ {end}")
data = fetch_kr_krx_open(start, end)

print()
print("완료. 반환된 키:", list(data.keys()))
print("high 있음:", "high" in data)
print("volume 있음:", "volume" in data)
if "high" in data:
    print("close 종목수:", data["close"].shape[1], " / high 종목수:", data["high"].shape[1])
