# -*- coding: utf-8 -*-
"""
VCP 레그 분해 진단 — 특정 종목이 왜 그렇게 판정됐는지 상세히 본다.
auto_sepa_new 폴더에서 실행: python3 diagnose_vcp.py
"""
import os
import datetime as dt
import numpy as np


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

import sepa_scanner
import vcp

TICKERS = ["XOM", "AAPL", "BAC", "JNJ"]

end = dt.date.today().strftime("%Y%m%d")
start = (dt.date.today() - dt.timedelta(days=420)).strftime("%Y%m%d")
data = sepa_scanner.fetch_us(TICKERS, start=start, end=end)
close, high, low, value = data["close"], data["high"], data["low"], data["value"]

for t in TICKERS:
    print("=" * 70)
    print(f"{t}")
    c = close[t].dropna().to_numpy(dtype=float)
    h = high[t].dropna().to_numpy(dtype=float)
    l = low[t].dropna().to_numpy(dtype=float)
    v = value[t].dropna().to_numpy(dtype=float)
    n = min(len(c), len(h), len(l), len(v))
    c, h, l, v = c[-n:], h[-n:], l[-n:], v[-n:]

    res = vcp.analyze_ticker(h, l, c, v)
    if not res:
        print("  데이터 부족")
        continue

    print(f"  레그 수: {res['contraction_count']}")
    print(f"  레그별 조정폭(%): {res['legs']}")
    print(f"  피벗: {res['pivot_price']} / 손절가: {res['stop_price']} / "
          f"현재가: {round(c[-1],2)}")
    print(f"  피벗 대비: {res['pct_from_pivot']}% / 리스크: {res['risk_pct']}%")
    print(f"  상태: {res['vcp_status']} / 수축중: {res['is_tightening']} / "
          f"거래량감소: {res['vol_dryup']}")

    # ATR 크기감(threshold)도 같이 보여준다 — 레그가 지나치게 잘게
    # 쪼개지는지 눈으로 판단하기 위함
    tr = vcp._true_range(h, l, c)
    import pandas as pd
    atr = pd.Series(tr).rolling(vcp.ATR_WINDOW).mean().to_numpy()
    recent_atr_pct = (atr[-1] / c[-1]) * 100
    print(f"  최근 ATR: {round(atr[-1],2)} (현재가의 {round(recent_atr_pct,2)}%) "
          f"→ 레그 판정 임계값: 이 값의 {vcp.ATR_MULT}배 = "
          f"약 {round(recent_atr_pct*vcp.ATR_MULT,2)}% 변동")
