# -*- coding: utf-8 -*-
"""
한국(KRX)·미국(NYSE/Nasdaq) 거래일 캘린더

특정 API에 의존하지 않고 정적 테이블로 관리한다.
이유: KRX 실시간 조회는 이미 여러 번 IP 차단을 겪었고, 휴장일은 매년 정해지는
고정된 사실이라 API 호출보다 정적 테이블이 훨씬 안정적이다.

매년 초 KR_HOLIDAYS_2027 등을 추가해 갱신해야 한다.
"""

import datetime as dt

# ─────────────────────────────────────────────────────────────
# 한국 (KRX) 휴장일 — 공휴일 + 대체공휴일 + 임시공휴일 + 연말휴장
# ─────────────────────────────────────────────────────────────
KR_HOLIDAYS_2026 = {
    "2026-01-01": "신정",
    "2026-02-16": "설날 연휴",
    "2026-02-17": "설날",
    "2026-02-18": "설날 연휴",
    "2026-03-02": "삼일절 대체공휴일",
    "2026-05-01": "노동절",
    "2026-05-05": "어린이날",
    "2026-05-25": "부처님오신날 대체공휴일",
    "2026-06-03": "지방선거(임시공휴일)",
    "2026-07-17": "제헌절",
    "2026-08-17": "광복절 대체공휴일",
    "2026-09-24": "추석 연휴",
    "2026-09-25": "추석",
    "2026-10-05": "개천절 대체공휴일",
    "2026-10-09": "한글날",
    "2026-12-25": "성탄절",
    "2026-12-31": "연말 휴장일",
}

# 정규장이 열리지만 마감/개장 시각이 평소와 다른 날 (선택적 안내용)
KR_IRREGULAR_2026 = {
    "2026-01-02": "증시 개장식 — 개장시각 1시간 지연(10:00 개장)",
}

# ─────────────────────────────────────────────────────────────
# 미국 (NYSE/Nasdaq) 휴장일
# ─────────────────────────────────────────────────────────────
US_HOLIDAYS_2026 = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day (관찰일, 7/4가 토요일)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
}

# 조기 폐장일 (13:00 ET 마감)
US_HALF_DAYS_2026 = {
    "2026-11-27": "추수감사절 다음날 조기폐장 (13:00 ET)",
    "2026-12-24": "크리스마스이브 조기폐장 (13:00 ET)",
}

_TABLES = {
    2026: (KR_HOLIDAYS_2026, KR_IRREGULAR_2026, US_HOLIDAYS_2026, US_HALF_DAYS_2026),
}


def _tables_for(year: int):
    if year in _TABLES:
        return _TABLES[year]
    # 알려지지 않은 연도는 주말만 반영 (공휴일 정보 없음을 알 수 있게 빈 테이블 반환)
    return {}, {}, {}, {}


def _iso(d) -> str:
    if isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


def kr_status(d) -> dict:
    """
    한국 시장 상태.
    반환: {"open": bool, "label": str, "reason": str|None}
    """
    ds = _iso(d)
    date = d if isinstance(d, dt.date) else dt.datetime.strptime(ds, "%Y-%m-%d").date()
    kr_hol, kr_irr, _, _ = _tables_for(date.year)

    if date.weekday() >= 5:
        return {"open": False, "label": "휴장 (주말)", "reason": "주말"}
    if ds in kr_hol:
        return {"open": False, "label": f"휴장 ({kr_hol[ds]})", "reason": kr_hol[ds]}
    if ds in kr_irr:
        return {"open": True, "label": "정규장 (시간 변경)", "reason": kr_irr[ds]}
    return {"open": True, "label": "정규장", "reason": None}


def us_status(d) -> dict:
    """미국 시장 상태."""
    ds = _iso(d)
    date = d if isinstance(d, dt.date) else dt.datetime.strptime(ds, "%Y-%m-%d").date()
    _, _, us_hol, us_half = _tables_for(date.year)

    if date.weekday() >= 5:
        return {"open": False, "label": "휴장 (주말)", "reason": "주말"}
    if ds in us_hol:
        return {"open": False, "label": f"휴장 ({us_hol[ds]})", "reason": us_hol[ds]}
    if ds in us_half:
        return {"open": True, "label": "조기폐장", "reason": us_half[ds]}
    return {"open": True, "label": "정규장", "reason": None}


def is_kr_trading_day(d) -> bool:
    return kr_status(d)["open"]


def is_us_trading_day(d) -> bool:
    return us_status(d)["open"]


def trading_days_between(start, end, market: str) -> int:
    """
    start(포함)부터 end(포함) 사이의 거래일 수 - 1을 반환.
    같은 날이면 0 (= "D"), 다음 거래일이면 1 (= "D+1") 식으로 쓰기 위함.
    """
    if isinstance(start, str):
        start = dt.datetime.strptime(start, "%Y-%m-%d").date()
    if isinstance(end, str):
        end = dt.datetime.strptime(end, "%Y-%m-%d").date()
    if end < start:
        return 0

    is_open = is_kr_trading_day if market == "KR" else is_us_trading_day
    n = -1
    d = start
    while d <= end:
        if is_open(d):
            n += 1
        d += dt.timedelta(days=1)
    return max(n, 0)


def format_elapsed(n: int) -> str:
    return "D" if n <= 0 else f"D+{n}"
