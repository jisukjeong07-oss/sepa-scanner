# -*- coding: utf-8 -*-
"""
kis_us_today.py — KIS 해외주식 시세로 "오늘(방금 끝난 미국 정규장)" 봉을 받아
야후(yfinance) 데이터(전날까지)에 덧붙인다.

[2026-10-13] 왜 필요한가
- 야후는 미국 일봉을 한국 시간 오전 10~11시에야 채운다(10/7~10/8 관찰).
  미국 마감(KST 05:00, 11월부터 06:00) 직후 실행에서 당일 종가를 쓰려면
  다른 출처가 필요하고, 그게 KIS 해외주식 현재가 상세(HHDFS76200200)다.
- KIS 공식 예제 주석: "미국 현재가 항목은 주간시세 및 애프터시세는 반영하지
  않으며, 정규장 마감 후에는 종가로 조회됩니다." → 마감 후 last = 정규장 종가.
  (한국과 달리 애프터마켓이 섞이지 않는다고 KIS가 명시. 첫 실행에서 다음 날
  야후 값과 대조해 확인한다.)
- 주의: KIS 무료 미국 시세는 나스닥 거래소 체결 기준이라, 거래량(tvol)이
  전체 거래소 합계보다 작을 수 있다. 그래서 이 봉의 거래량은 "확정 전" 값으로
  보고, 다음 실행에서 야후 값으로 바뀐다(이 봉은 야후 캐시에 넣지 않는다).

설계
- 종목별 거래소 코드(NAS/NYS/AMS)는 처음 한 번 NAS→NYS→AMS 순으로 찾아
  cache/us_excd_map.json에 저장한다(커밋됨). 다음부터는 종목당 1회 호출.
- 야후 티커의 '-'(BRK-B)는 KIS에서 '/'로 시도하고, 안 되면 '.'도 시도한다.
- 오늘 봉은 별도 파일 cache/us_kis_today.parquet에 둔다. market_breadth가
  같은 파일을 읽어 시장 폭에도 반영한다.
- 조건: 미국 거래일 + 뉴욕 시간 16:15 이후(같은 날) + 야후 데이터 마지막 날짜가
  그날보다 이전. 아니면 아무것도 하지 않는다. 실패하면 전날 종가로 스캔.
- 전일 종가(base) 대비 ±40% 넘는 값은 오류로 보고 버린다(미국은 가격제한 없음).
"""
from __future__ import annotations
import os
import sys
import json
import time
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from kis_client import KIS, num, KST

NY = ZoneInfo("America/New_York")
CACHE_DIR = "cache"
TODAY_CACHE = os.path.join(CACHE_DIR, "us_kis_today.parquet")
EXCD_MAP = os.path.join(CACHE_DIR, "us_excd_map.json")
READY_AFTER = dt.time(16, 15)      # 뉴욕 시간. 마감(16:00) 15분 뒤
MAX_MOVE = 0.40
EXCD_ORDER = ("NAS", "NYS", "AMS")


def decide(now_ny: dt.datetime, last_cached: dt.date | None) -> dict:
    today = now_ny.date()
    try:
        import market_calendar as mc
        if not mc.is_us_trading_day(today):
            return {"action": "skip", "reason": f"{today} 미국 휴장일"}
        half = (mc.us_status(today).get("reason") or "")
        ready = dt.time(13, 15) if "조기" in half or "13:00" in half else READY_AFTER
    except Exception:
        ready = READY_AFTER
        if today.weekday() >= 5:
            return {"action": "skip", "reason": "주말"}
    if last_cached is not None and last_cached >= today:
        return {"action": "skip", "reason": f"야후 데이터에 이미 {last_cached} 봉이 있음"}
    if now_ny.time() < ready:
        return {"action": "skip", "reason": f"뉴욕 {now_ny:%H:%M} — 마감 {ready:%H:%M} 이전이라 KIS 당일 봉을 쓰지 않음"}
    return {"action": "run", "reason": f"뉴욕 {now_ny:%H:%M} ≥ {ready:%H:%M}"}


def _load_map() -> dict:
    try:
        with open(EXCD_MAP, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_map(m: dict):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(EXCD_MAP, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, sort_keys=True)
    except Exception as e:
        print(f"[KIS US] 거래소 맵 저장 실패(계속): {e}")


def _symbols(ticker: str) -> list:
    if "-" in ticker:
        base = ticker.split("-")[0]
        suf = ticker.split("-", 1)[1]
        return [f"{base}/{suf}", f"{base}.{suf}", ticker]
    return [ticker]


def _detail(kis: KIS, excd: str, symb: str) -> dict:
    j = kis.get("/uapi/overseas-price/v1/quotations/price-detail", "HHDFS76200200",
                {"AUTH": "", "EXCD": excd, "SYMB": symb})
    o = j.get("output") or {}
    return o if num(o.get("last")) else {}


def _fetch_one(kis: KIS, ticker: str, excd_map: dict, today: dt.date) -> dict | None:
    known = excd_map.get(ticker)                      # "NAS|BRK/B" 형태
    tries = [tuple(known.split("|", 1))] if known else [(e, s) for s in _symbols(ticker) for e in EXCD_ORDER]
    o, hit = {}, None
    for excd, symb in tries:
        o = _detail(kis, excd, symb)
        if o:
            hit = f"{excd}|{symb}"
            break
    if not o:
        return None
    last = num(o.get("last"))
    base = num(o.get("base"))
    if not last or last <= 0:
        return None
    if base and base > 0 and abs(last / base - 1.0) > MAX_MOVE:
        return None
    high, low, opn = num(o.get("high")), num(o.get("low")), num(o.get("open"))
    vol = num(o.get("tvol"))
    high = max(high, last) if high else last
    low = min(low, last) if low else last
    return {"ticker": ticker, "excd": hit, "date": pd.Timestamp(today), "close": last, "open": opn,
            "high": high, "low": low, "volume": vol,
            "value": (last * vol) if vol is not None else None, "prev_close": base,
            "mcap": num(o.get("tomv"))}


def load_today_cache(date: dt.date | None = None) -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(TODAY_CACHE)
        if df.empty:
            return None
        d = pd.to_datetime(df["date"]).dt.date.iloc[0]
        if date is not None and d != date:
            return None
        return df
    except Exception:
        return None


def fetch_today(tickers, today: dt.date) -> tuple:
    tickers = [str(t) for t in tickers]
    st = {"status": "skipped", "date": today.isoformat(), "source": "KIS", "fetched_at": None,
          "n_requested": len(tickers), "n_ok": 0, "n_missing": len(tickers), "reason": "", "calls": 0}
    if os.environ.get("KIS_DISABLE"):
        st["reason"] = "KIS_DISABLE 설정"
        return pd.DataFrame(), st
    cached = load_today_cache(today)
    if cached is not None and len(cached) >= 0.9 * len(tickers):
        st.update(status="ok", fetched_at="(이번 실행 이전에 받아 둔 캐시)", n_ok=len(cached),
                  n_missing=len(tickers) - len(cached), reason="같은 날 캐시 재사용")
        print(f"[KIS US] 오늘({today}) 봉 캐시 재사용: {len(cached)}종목")
        return cached, st
    try:
        kis = KIS().auth()
    except Exception as e:
        st.update(status="failed", reason=str(e)[:150])
        print(f"[KIS US] {st['reason']} → 전날 종가로 계속")
        return pd.DataFrame(), st

    excd_map = _load_map()
    t0 = time.time()
    print(f"[KIS US] 오늘({today}) 봉 수집 시작: {len(tickers)}종목 "
          f"(거래소 맵 {len(excd_map)}종목 보유, 병렬)")
    head = kis.map(lambda t: _fetch_one(kis, t, excd_map, today), tickers[:30], progress_every=0)
    if not any(head):
        st.update(status="failed", reason="초반 30종목 전부 실패 — 토큰/권한/해외주식 서비스 신청 여부 확인", calls=kis.calls)
        print(f"[KIS US] {st['reason']} → 전날 종가로 계속")
        return pd.DataFrame(), st
    rest = kis.map(lambda t: _fetch_one(kis, t, excd_map, today), tickers[30:], progress_every=200)
    rows = [r for r in head + rest if r]
    for r in rows:
        if r.get("excd"):
            excd_map[r["ticker"]] = r["excd"]
    _save_map(excd_map)

    df = pd.DataFrame(rows)
    st.update(fetched_at=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), n_ok=len(df),
              n_missing=len(tickers) - len(df), calls=kis.calls)
    if df.empty:
        st.update(status="failed", reason="수집 0종목")
        return df, st
    st["status"] = "ok" if len(df) >= 0.9 * len(tickers) else "partial"
    st["reason"] = "" if st["status"] == "ok" else f"{st['n_missing']}종목 조회 실패(전날 값·stale로 처리)"
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.drop(columns=["excd"]).to_parquet(TODAY_CACHE, index=False)
    except Exception as e:
        print(f"[KIS US] 오늘 봉 캐시 저장 실패(계속): {e}")
    print(f"[KIS US] 수집 완료: {len(df)}/{len(tickers)}종목, 호출 {kis.calls}회"
          f"{', 한도 재시도 ' + str(kis.retries) + '회' if kis.retries else ''}, {time.time() - t0:.0f}초 → {st['status']}")
    return df, st


def attach_today(data: dict, today_df: pd.DataFrame) -> dict:
    """fetch_us()/fetch_us_cached() 결과(close·value·high·low wide)에 오늘 행을 덧붙인다."""
    if today_df is None or today_df.empty:
        return data
    day = pd.Timestamp(today_df["date"].iloc[0]).normalize()
    t = today_df.set_index("ticker")
    cols = data["close"].columns
    for key, col in (("close", "close"), ("value", "value"), ("high", "high"), ("low", "low")):
        if key not in data or data[key] is None:
            continue
        wide = data[key]
        if day in wide.index:
            wide = wide.drop(index=day)
        row = t[col].reindex(cols).astype(float)
        row.name = day
        data[key] = pd.concat([wide, row.to_frame().T]).sort_index()
    return data


def apply(data: dict, now_ny: dt.datetime | None = None) -> tuple:
    """sepa_scanner.run()의 미국 수집 직후 한 줄로 쓰는 진입점. 반환: (data, status)"""
    now_ny = now_ny or dt.datetime.now(NY)
    last = None
    try:
        last = pd.Timestamp(data["close"].index[-1]).date()
    except Exception:
        pass
    # 이번 실행 이전(같은 날)에 받아 둔 캐시가 야후 데이터보다 새 날짜면 그대로 붙인다
    try:
        cached = pd.read_parquet(TODAY_CACHE)
        cdate = pd.to_datetime(cached["date"]).dt.date.iloc[0] if len(cached) else None
    except Exception:
        cached, cdate = None, None
    if cached is not None and cdate is not None and (last is None or cdate > last) and cdate <= now_ny.date():
        data = attach_today(data, cached)
        st = {"status": "ok", "date": cdate.isoformat(), "source": "KIS", "fetched_at": "(이전 실행 캐시)",
              "n_requested": len(data["close"].columns), "n_ok": int(len(cached)),
              "n_missing": int(len(data["close"].columns) - len(cached)), "reason": "이전 실행의 KIS 캐시 재사용", "calls": 0}
        print(f"[KIS US] 캐시 재사용: {cdate} 봉 {len(cached)}종목을 야후 데이터(~{last})에 덧붙임")
        return data, st
    d = decide(now_ny, last)
    if d["action"] == "skip":
        st = {"status": "skipped", "date": now_ny.date().isoformat(), "source": "yahoo",
              "reason": d["reason"], "n_ok": 0, "n_missing": 0}
        print(f"[KIS US] 오늘 봉 건너뜀: {d['reason']}")
        return data, st
    today_df, st = fetch_today(list(data["close"].columns), today=now_ny.date())
    if st["status"] in ("ok", "partial"):
        data = attach_today(data, today_df)
        print(f"[KIS US] 오늘 봉 {st['n_ok']}종목을 야후 데이터(~{last})에 덧붙임 → 데이터 기준일 {st['date']}")
    return data, st


if __name__ == "__main__":
    tk = sys.argv[1:] or ["AAPL", "MSFT", "BRK-B", "MPC"]
    df, status = fetch_today(tk, today=dt.datetime.now(NY).date())
    print(status)
    print(df)
