# -*- coding: utf-8 -*-
"""
us_sector_data.py — 미국 종목의 업종 분류(GICS sector)를 만든다.

[2026-09-18] 설계 근거 — sector_data.py(한국)와 같은 패턴
- yfinance의 시가총액 조회(us_market_caps)는 가벼운 fast_info를 쓰는데,
  거기엔 업종 정보가 없다. 업종은 더 무거운 .info를 따로 불러야 한다
  (종목당 응답 시간이 몇 배 더 걸림). 그래서 시가총액처럼 매번 "통과+관찰"
  종목만 조회하는 방식 대신, 전체 유니버스를 한 번 받아 캐시해두고
  30일 정도 재사용한다 — 업종 분류 자체는 자주 안 바뀌므로 이게 맞다.
- GICS 11개 대분류(Technology, Healthcare, Financial Services,
  Consumer Cyclical, Industrials, Communication Services, Consumer
  Defensive, Energy, Utilities, Real Estate, Basic Materials)를 그대로
  쓴다 — 한국의 WICS처럼 별도 필터링이 필요 없다(yfinance의 sector
  필드가 이미 업종 그 자체이지, 시장 전체/스타일 지수 같은 잡음이
  섞이지 않는다).
"""
import os
import json
import time
import datetime as dt

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cache", "us_sector_map.json")
CACHE_MAX_AGE_DAYS = 30


def fetch_us_sector_map(tickers: list, force: bool = False, sleep_sec: float = 0.15) -> dict:
    """
    {티커: GICS 업종명} 딕셔너리를 반환한다. 캐시가 30일 이내면 그걸 쓰고,
    없거나 오래됐으면 yfinance로 새로 받는다. 실패해도(개별 종목 조회
    실패, 네트워크 문제 등) 빈 딕셔너리나 부분 결과를 반환한다 — 이 기능은
    부가 정보라, 실패해도 스캐너 본체가 죽으면 안 된다.
    """
    if not force and os.path.exists(CACHE_PATH):
        age_days = (time.time() - os.path.getmtime(CACHE_PATH)) / 86400
        if age_days < CACHE_MAX_AGE_DAYS:
            try:
                with open(CACHE_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[미국 업종분류] 캐시 사용 ({age_days:.0f}일 전 수집, {len(data)}종목)")
                return data
            except Exception as e:
                print(f"[미국 업종분류] 캐시 읽기 실패, 새로 받습니다: {e}")

    try:
        import yfinance as yf
    except ImportError:
        print("[미국 업종분류] yfinance가 설치되어 있지 않아 건너뜁니다.")
        return {}

    mapping = {}
    fail_count = 0
    t0 = time.time()
    print(f"[미국 업종분류] {len(tickers)}종목 조회 시작 (fast_info보다 느린 .info를 "
          f"써서 시간이 좀 걸립니다)...")

    for i, t in enumerate(tickers):
        if i > 0 and i % 100 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(tickers) - i)
            print(f"  {i}/{len(tickers)}종목 처리 · 경과 {elapsed:.0f}초 · 예상 잔여 {eta:.0f}초")
        try:
            info = yf.Ticker(t).info
            sector = info.get("sector")
            if sector:
                mapping[t] = sector
        except Exception:
            fail_count += 1
        time.sleep(sleep_sec)

    print(f"[미국 업종분류] 완료 · {len(mapping)}종목 매핑 (실패 {fail_count}건) · "
          f"{time.time()-t0:.0f}초")

    if mapping:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)
        except Exception as e:
            print(f"[미국 업종분류] 캐시 저장 실패(이번 실행 결과는 정상 사용됨): {e}")

    return mapping


if __name__ == "__main__":
    from sepa_scanner import us_universe
    tickers = us_universe()
    m = fetch_us_sector_map(tickers, force=True)
    from collections import Counter
    counts = Counter(m.values())
    for name, n in counts.most_common(20):
        print(f"  {name}: {n}종목")
