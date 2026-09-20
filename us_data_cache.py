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
                print(f"[US 캐시] 재사용 ({age_hours:.1f}시간 전 수집, "
                      f"{len(out['close'].columns)}종목) — yfinance 재요청 생략")
                return out
            except Exception as e:
                print(f"[US 캐시] 읽기 실패, 새로 받습니다: {e}")

    from sepa_scanner import fetch_us
    out = fetch_us(tickers, start=start, end=end, period=period)

    if cacheable and out.get("close") is not None and not out["close"].empty:
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
