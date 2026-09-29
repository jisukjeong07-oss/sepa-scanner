# -*- coding: utf-8 -*-
"""
us_data_cache.py — sepa_scanner.fetch_us()를 감싸서 하루 안에서는
재사용하는 캐시 래퍼.

[2026-09-20] 만든 이유
- run_daily.py 한 번 실행 안에서, 메인 스캔(sepa_scanner.run)과 "놓친
  패턴" 계산(missed_patterns_data.py, backtest.py 경유)이 각각 독립적으로
  미국 500종목 2년치를 yfinance에 요청하고 있었다. 두 배로 느린 건
  둘째치고, 실제로 이 중복 요청 때문에 yfinance 내부 sqlite 캐시가
  충돌해 일부 종목이 실패하는 문제(OperationalError: unable to open
  database file)가 실제 로그에서 확인됐다.
- 해결: "오늘 하루 안에 한 번 받았으면, 그 결과를 파일로 남겨두고
  재사용"한다. 한국(kr_data_fdr.py)의 parquet 캐시와 같은 방식이다.
- max_age_hours=20으로 둔 이유: 장전·장마감 두 세션이 하루 안에 있는데,
  20시간이면 "오늘 받은 건 재사용"이 자연스럽게 되면서도 다음날 첫
  실행에서는 새로 받는다.
- start/end를 지정해서 부르는 경우(과거 특정 구간 소급 조회 등)는 캐시
  대상이 아니다 — "오늘 기준 2년치 전체"와 성격이 다른 조회라 섞이면
  안 되므로, 그럴 때는 매번 그대로 fetch_us를 통과시킨다.
"""
import os
import time
import datetime as dt

import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CACHE_PREFIX = os.path.join(CACHE_DIR, "us_prices")


def _expected_last_session(now: dt.datetime = None) -> dt.date:
    """
    [2026-09-29] 지금 시점에 "이미 끝났어야 하는" 가장 최근 미국 정규장 날짜.
    뉴욕 현지 시각 기준으로 계산한다 — GitHub Actions 러너는 UTC, 맥북은
    KST라 dt.date.today()로는 날짜가 하루씩 어긋난다.
    16:30(마감 30분 후) 전이면 전 영업일, 주말이면 금요일로 되돌린다.
    미국 공휴일은 고려하지 않는다 — 공휴일엔 이 값이 실제보다 하루 늦어
    '캐시가 오래됐다'로 판정될 수 있지만, 그 경우 한 번 더 받을 뿐 결과는 정확하다.
    """
    from zoneinfo import ZoneInfo
    now = now or dt.datetime.now(ZoneInfo("America/New_York"))
    d = now.date()
    if now.weekday() >= 5 or now.time() < dt.time(16, 30):
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _last_row_complete(close: pd.DataFrame, value: pd.DataFrame,
                       min_ratio: float = 0.5) -> bool:
    """
    [2026-09-29] 마지막 날짜 행이 제대로 채워져 있는지 확인한다.
    실사례: 9/29 장전 실행 때 yfinance가 미국 9/28 행을 가격·거래량이
    빈 미완성 봉으로 내려줬고, 그게 캐시에 저장돼 20시간 동안 재사용됐다.
    종목 절반 이상의 가격 또는 거래대금이 비어 있으면 "불완전"으로 본다.
    """
    if close is None or close.empty or value is None or value.empty:
        return False
    last = close.index[-1]
    c_ok = close.loc[last].notna().mean() >= min_ratio
    v_ok = value.reindex(index=[last]).iloc[0].notna().mean() >= min_ratio
    return bool(c_ok and v_ok)


def fetch_us_cached(tickers, start: str = None, end: str = None,
                    period: str = "2y", max_age_hours: float = 20.0) -> dict:
    close_path = f"{CACHE_PREFIX}_close.parquet"
    high_path = f"{CACHE_PREFIX}_high.parquet"
    low_path = f"{CACHE_PREFIX}_low.parquet"
    value_path = f"{CACHE_PREFIX}_value.parquet"

    # [2026-09-20] "캐시 대상인가"는 "start/end가 없는가"가 아니라
    # "end가 오늘 날짜인가"로 판단해야 한다. 메인 스캔(sepa_scanner.run)은
    # 항상 start/end를 채워서 부르는데(그날 기준 lookback을 자기가 미리
    # 계산해서 넘김), 그 end가 결국 "오늘"이면 backtest.py의 period="2y"
    # 호출(start/end 없음)과 사실상 같은 요청이다. 예전 조건(start/end가
    # 둘 다 없어야 캐시 대상)으로는 메인 스캔 쪽에서 캐시가 절대 안 쌓여서,
    # 이 캐시를 만든 목적(같은 날 중복 요청 방지)이 무력화됐다.
    # end만 특정 과거 날짜(소급 스캔 등)면 오늘 캐시와 성격이 다르므로
    # 여전히 캐시 대상에서 제외한다.
    today_str = dt.date.today().strftime("%Y%m%d")
    cacheable = (end is None) or (end == today_str)

    if cacheable and os.path.exists(close_path):
        age_hours = (time.time() - os.path.getmtime(close_path)) / 3600
        if age_hours < max_age_hours:
            try:
                out = {
                    "close": pd.read_parquet(close_path),
                    "value": pd.read_parquet(value_path),
                }
                if os.path.exists(high_path):
                    out["high"] = pd.read_parquet(high_path)
                    out["low"] = pd.read_parquet(low_path)
                # [2026-09-29] 이 수정 이전에 저장된 캐시에는 빈 행이 들어
                # 있을 수 있다. 마지막 행이 불완전하면 재사용하지 않고 새로 받는다.
                last_date = out["close"].index[-1].date()
                expected = _expected_last_session()
                if not _last_row_complete(out["close"], out["value"]):
                    print(f"[US 캐시] 마지막 날짜({last_date}) 행이 "
                          f"비어 있는 미완성 캐시 → 무시하고 새로 받습니다")
                # [2026-09-29] 캐시가 최신 장보다 오래됐고(예: 장 마감 직후라
                # 9/28 봉이 빠진 채 9/25까지만 저장됨) 저장한 지 1시간이 넘었으면
                # 새로 받는다. 1시간 이내면 같은 run_daily 실행 안의 중복 요청이라
                # 재사용한다 — 이 캐시를 만든 원래 목적(yfinance sqlite 충돌 방지).
                elif last_date < expected and age_hours >= 1.0:
                    print(f"[US 캐시] 캐시 마지막 날짜 {last_date}가 최신 장({expected})보다 "
                          f"오래됨 ({age_hours:.1f}시간 전 수집) → 새로 받습니다")
                else:
                    print(f"[US 캐시] 재사용 ({age_hours:.1f}시간 전 수집, "
                          f"{len(out['close'].columns)}종목, "
                          f"마지막 날짜 {last_date}) — yfinance 재요청 생략")
                    return out
            except Exception as e:
                print(f"[US 캐시] 읽기 실패, 새로 받습니다: {e}")

    from sepa_scanner import fetch_us
    out = fetch_us(tickers, start=start, end=end, period=period)

    # [2026-09-29] 불완전한 결과는 캐시에 남기지 않는다. fetch_us()가 이미
    # 빈 행을 걸러내므로 보통은 통과하지만, 이중 안전장치로 둔다 — 잘못된
    # 캐시가 한 번 저장되면 20시간 동안 모든 재실행이 그걸 재사용하기 때문이다.
    if cacheable and out.get("close") is not None and not out["close"].empty \
            and not _last_row_complete(out["close"], out.get("value")):
        print("[US 캐시] 마지막 날짜 행이 불완전해 캐시에 저장하지 않습니다 "
              "(이번 실행 결과는 그대로 사용)")
    elif cacheable and out.get("close") is not None and not out["close"].empty:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            out["close"].to_parquet(close_path)
            out["value"].to_parquet(value_path)
            if "high" in out:
                out["high"].to_parquet(high_path)
                out["low"].to_parquet(low_path)
            print(f"[US 캐시] 저장 완료 ({len(out['close'].columns)}종목) — "
                  f"오늘 안에 다시 조회하면 이걸 재사용합니다")
        except Exception as e:
            print(f"[US 캐시] 저장 실패(이번 실행 결과는 정상 사용됨): {e}")

    return out
