# -*- coding: utf-8 -*-
"""
매크로 지표 — 대시보드 "매크로 지표" 패널용 스냅샷.

[2026-09-12] 첫 버전. 전부 yfinance 하나로 통일해서 받는다. 이미 US 종목
스캔(sepa_scanner.fetch_us)에 쓰고 있는 라이브러리라 새 API 키가 필요 없고,
소스가 하나라 지수 값들 사이에 타이밍이 어긋날 일도 없다.

원래는 지수(코스피·S&P500 등)와 리스크 신호(VIX 등)를 따로 뒀었는데,
CNN Fear&Greed·AAII·BofA FMS는 안정적인 무료 API가 없어 자동화를
포기했다. 남는 건 전부 '시장 가격' 성격이라 굳이 나눌 이유가 없어져서
하나의 "매크로 지표" 패널로 합쳤다.

값 하나가 실패해도 나머지는 정상 표시되도록 종목별로 개별 처리한다.
"""

import datetime as dt

TICKERS = [
    # (표시 이름, yfinance 티커, 값 배율(1이면 그대로))
    # [2026-09-12] 대시보드 카드 2줄 배치와 순서를 맞춤 — 1~6번째가 1행,
    # 7~11번째가 2행으로 그대로 렌더링된다 (make_dashboard.py 참고).
    ("나스닥", "^IXIC", 1),
    ("S&P500", "^GSPC", 1),
    ("코스피", "^KS11", 1),
    ("코스닥", "^KQ11", 1),
    ("원/달러", "KRW=X", 1),
    ("VIX", "^VIX", 1),
    ("미국채10년(%)", "^TNX", 1),   # [2026-09-12] yfinance가 이미 %값 그대로 줌 (예전 *10 관행 아님, 실측 확인함)
    ("미국채30년(%)", "^TYX", 1),
    ("달러인덱스", "DX-Y.NYB", 1),   # [2026-09-12] DX=F가 야후에서 404 → 현물 인덱스로 교체
    ("WTI($)", "CL=F", 1),
    ("금($)", "GC=F", 1),
]


def _fetch_one(name: str, ticker: str, scale: float) -> dict:
    import yfinance as yf
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return {"name": name, "ok": False}
        last, prev = closes.iloc[-1] * scale, closes.iloc[-2] * scale
        change_pct = round((last / prev - 1) * 100, 2)
        return {
            "name": name,
            "value": round(float(last), 2),
            "change_pct": change_pct,
            "as_of": closes.index[-1].strftime("%Y-%m-%d"),
            "ok": True,
        }
    except Exception as e:
        print(f"[매크로] {name}({ticker}) 조회 실패: {str(e)[:100]}")
        return {"name": name, "ok": False}


def get_macro_snapshot() -> list:
    """
    make_dashboard.build(macro_snapshot=...) 에 그대로 넘길 리스트.
    항목 하나가 실패해도({"ok": False}) 전체 호출은 계속 진행한다.
    """
    out = []
    for name, ticker, scale in TICKERS:
        out.append(_fetch_one(name, ticker, scale))
    n_ok = sum(1 for r in out if r["ok"])
    print(f"[매크로] {n_ok}/{len(out)}개 지표 조회 성공")
    return out


if __name__ == "__main__":
    import json
    snap = get_macro_snapshot()
    print(json.dumps(snap, ensure_ascii=False, indent=2))
