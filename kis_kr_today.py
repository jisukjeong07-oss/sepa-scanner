# -*- coding: utf-8 -*-
"""
kis_kr_today.py — 한국투자증권(KIS) Open API로 "오늘" 한국 정규장 봉을 받아
KRX Open API 캐시(어제까지)에 덧붙인다.

[2026-10-12] 왜 필요한가
- KRX Open API는 D일 시세를 D+1 08:00 전후에야 공개한다. 장마감 실행에서
  "오늘 종가"를 쓰려면 다른 출처가 필요하고, 그게 KIS다.
- KIS 현재가(stck_prpr)와 당일 일봉 종가(stck_clpr)는 16:00 이후 KRX
  애프터마켓 체결로 바뀐다(10/12 측정: 16:02에 30종목 중 23종목 변동,
  16:30엔 평균 0.4%·최대 1.0% 차이). 정규장 종가는 공식 종가와 다르다.
- 당일 분봉(FHKST03010200)의 15:30 봉은 정규장 종가 단일가 체결가다.
  16:05 이후엔 30/30 종목이 KRX 공식 종가와 일치했다(15:31엔 7/30만 —
  봉이 확정되기까지 몇 분 걸린다). 그래서 16:15 이후에 이 봉을 종가로 쓴다.
- 시가·고가·저가·거래량·거래대금은 현재가 API(FHKST01010100)에서 받는다.
  고가는 16:30까지 30/30 불변, 저가는 1~2종목이 애프터마켓 저가로 0.1%쯤
  바뀔 수 있다(다음 날 KRX 공식 값으로 자동 교체되므로 하루만 감수).
  거래량은 KRX 공식 값도 애프터마켓분을 포함하므로 오히려 공식 값에 가깝다.

설계 원칙
- 오늘 봉은 KRX 캐시(cache/kr_krx_daily)에 절대 넣지 않는다. 별도 파일
  (cache/kr_kis_today.parquet)에 두고, 다음 날 KRX 공식 값이 들어오면
  자연히 무시된다. (fetch_kr_krx_open은 코스피·코스닥이 모두 있는 날짜를
  "수집 완료"로 보므로, KIS 행을 캐시에 넣으면 공식 값이 영영 안 들어온다.)
- 15:30 봉이 없는 종목(거래정지 등)은 오늘 값을 만들지 않는다. 현재가로
  대신 채우면 애프터마켓 가격이 종가로 들어갈 수 있기 때문이다. 그 종목은
  어제 값으로 남고 screen()이 stale로 표시한다.
- KIS 전체가 실패하면 예외를 던지지 않고 (빈 결과, 상태) 를 돌려준다.
  호출부는 어제 종가로 스캔을 계속하고 상태를 latest.json에 남긴다.
- 키는 환경변수 KIS_APP_KEY / KIS_APP_SECRET(GitHub Secrets)로만 받는다.
  로그에 키·토큰을 찍지 않는다. KIS_DISABLE=1 이면 아무것도 하지 않는다.

[2026-10-13] 병렬 호출 + 미국 세션 재사용
- 호출은 kis_client.KIS(스레드 + 초당 한도)로 바꿨다. 45~60분 → 6~7분.
- 미국 장마감 세션(KST 05:17 전후)에서는 KRX 공식 값이 아직 없으므로(08:00 공개),
  전날 16:16 실행이 받아 둔 cache/kr_kis_today.parquet를 그대로 덧붙인다.
  (캐시 날짜가 KRX 캐시 마지막 날짜보다 뒤이고 오늘 이전이면 재사용)

조회 시각 규칙 (KST)
- 오늘이 한국 거래일이고, 캐시 마지막 날짜가 오늘보다 이전일 때만 동작.
- 16:15 이후면 바로 조회. 15:30~16:15 사이면 16:15까지 기다린다(예약이
  일찍 돌았을 때). 15:30 이전이면 장전/장중 실행이므로 건너뛴다.
- 정규장이 1시간 늦게 끝나는 날(수능일 등)은 17:15 기준. market_calendar의
  KR_IRREGULAR 사유에 "1시간"이 들어가면 그렇게 본다. 2026-11-19(수능)은
  달력에 없더라도 여기서 직접 처리한다.
"""
from __future__ import annotations
import os
import sys
import time
import json
import datetime as dt

import pandas as pd

KST = dt.timezone(dt.timedelta(hours=9))
BASE = "https://openapi.koreainvestment.com:9443"     # 실전투자 도메인
CACHE_DIR = "cache"
TODAY_CACHE = os.path.join(CACHE_DIR, "kr_kis_today.parquet")

READY_HHMM = (16, 15)        # 이 시각 이후 15:30 봉이 확정된 것으로 본다
EARLIEST_WAIT_HHMM = (15, 30)  # 이보다 이르면 장전/장중 실행으로 보고 건너뜀
LATE_CLOSE_DAYS = {"2026-11-19"}   # 정규장 10:00~16:30 (수능일) → 기준 +1시간
MAX_MOVE = 0.31              # 전일 대비 ±31% 넘는 종가는 데이터 오류로 보고 버림


# ──────────────────────────────────────────────────────────────
# 시각 판단
# ──────────────────────────────────────────────────────────────
def _ready_time(today: dt.date) -> dt.datetime:
    h, m = READY_HHMM
    late = today.isoformat() in LATE_CLOSE_DAYS
    try:
        import market_calendar as mc
        reason = (mc.kr_status(today).get("reason") or "")
        if "1시간" in reason and "개장" not in reason:
            late = True
    except Exception:
        pass
    if late:
        h += 1
    return dt.datetime(today.year, today.month, today.day, h, m, tzinfo=KST)


def decide(now: dt.datetime, last_cached: dt.date | None) -> dict:
    """
    지금 KIS 조회를 해야 하는지 판단한다.
    반환: {"action": "run"|"wait"|"skip", "reason": str, "wait_sec": int}
    """
    today = now.date()
    try:
        import market_calendar as mc
        if not mc.is_kr_trading_day(today):
            return {"action": "skip", "reason": f"{today} 한국 휴장일", "wait_sec": 0}
    except Exception:
        if today.weekday() >= 5:
            return {"action": "skip", "reason": "주말", "wait_sec": 0}
    if last_cached is not None and last_cached >= today:
        return {"action": "skip", "reason": f"KRX 캐시에 이미 {last_cached} 종가가 있음", "wait_sec": 0}

    ready = _ready_time(today)
    earliest = ready - dt.timedelta(minutes=45)      # 15:30 (수능일 16:30)
    if now < earliest:
        return {"action": "skip",
                "reason": f"현재 {now:%H:%M} — 장마감 전이라 KIS 당일 봉을 쓰지 않음(장전/장중 실행)",
                "wait_sec": 0}
    if now < ready:
        sec = int((ready - now).total_seconds()) + 5
        return {"action": "wait", "reason": f"{ready:%H:%M}까지 {sec // 60}분 대기(15:30 봉 확정 대기)",
                "wait_sec": sec}
    return {"action": "run", "reason": f"현재 {now:%H:%M} ≥ {ready:%H:%M}", "wait_sec": 0}


# ──────────────────────────────────────────────────────────────
# KIS 호출 — 공용 클라이언트(kis_client.py)
# ──────────────────────────────────────────────────────────────
from kis_client import KIS, num as _num_shared


def _num(v):
    return _num_shared(v)


def _fetch_one(kis: KIS, ticker: str, today_str: str) -> dict | None:
    """종목 하나의 오늘 봉. 15:30 봉이 없으면 None."""
    m = kis.get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                 "FID_INPUT_HOUR_1": "153000", "FID_PW_DATA_INCU_YN": "N", "FID_ETC_CLS_CODE": ""})
    bars = [b for b in (m.get("output2") or []) if isinstance(b, dict)]
    bar = next((b for b in bars
                if b.get("stck_bsop_date") == today_str
                and str(b.get("stck_cntg_hour", "")).startswith("1530")), None)
    if bar is None:
        return None
    close = _num(bar.get("stck_prpr"))
    if not close or close <= 0:
        return None

    p = kis.get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}).get("output") or {}
    prev = _num(p.get("stck_sdpr")) or _num((m.get("output1") or {}).get("stck_prdy_clpr"))
    if prev and prev > 0 and abs(close / prev - 1.0) > MAX_MOVE:
        return None                                  # 상하한가(±30%)로 설명 안 되는 값 → 버림

    high = _num(p.get("stck_hgpr"))
    low = _num(p.get("stck_lwpr"))
    opn = _num(p.get("stck_oprc"))
    vol = _num(p.get("acml_vol"))
    val = _num(p.get("acml_tr_pbmn"))
    if val is None:
        val = _num((m.get("output1") or {}).get("acml_tr_pbmn"))
    if vol is None:
        vol = _num((m.get("output1") or {}).get("acml_vol"))
    # 고가·저가는 종가를 벗어나지 않게 보정(애프터마켓 저가 혼입 대비 최소한의 방어)
    high = max(high, close) if high else close
    low = min(low, close) if low else close
    return {"ticker": ticker, "close": close, "open": opn, "high": high, "low": low,
            "volume": vol, "value": val, "prev_close": prev}


# ──────────────────────────────────────────────────────────────
# 공개 함수
# ──────────────────────────────────────────────────────────────
def load_today_cache(today: dt.date) -> pd.DataFrame | None:
    """같은 날 이미 받아 둔 오늘 봉이 있으면 돌려준다(재실행·시장 폭 계산용)."""
    try:
        df = pd.read_parquet(TODAY_CACHE)
        if df.empty:
            return None
        if pd.to_datetime(df["date"]).dt.date.iloc[0] != today:
            return None
        return df
    except Exception:
        return None


def fetch_today(tickers, now: dt.datetime | None = None, wait: bool = True) -> tuple:
    """
    오늘 봉 수집. 반환: (DataFrame[date,ticker,close,open,high,low,volume,value], status dict)
    status = {"status": "ok"|"partial"|"skipped"|"failed", "date", "source": "KIS",
              "fetched_at", "n_requested", "n_ok", "n_missing", "reason", "calls"}
    """
    now = now or dt.datetime.now(KST)
    today = now.date()
    today_str = today.strftime("%Y%m%d")
    tickers = [str(t).zfill(6) for t in tickers]
    st = {"status": "skipped", "date": today.isoformat(), "source": "KIS", "fetched_at": None,
          "n_requested": len(tickers), "n_ok": 0, "n_missing": len(tickers), "reason": "", "calls": 0}

    if os.environ.get("KIS_DISABLE"):
        st["reason"] = "KIS_DISABLE 설정"
        return pd.DataFrame(), st
    key = os.environ.get("KIS_APP_KEY", "").strip()
    sec = os.environ.get("KIS_APP_SECRET", "").strip()
    if not key or not sec:
        st["reason"] = "KIS_APP_KEY/KIS_APP_SECRET 없음"
        return pd.DataFrame(), st

    cached = load_today_cache(today)
    if cached is not None and len(cached) >= 0.9 * len(tickers):
        st.update(status="ok", fetched_at="(이번 실행 이전에 받아 둔 캐시)",
                  n_ok=len(cached), n_missing=len(tickers) - len(cached), reason="같은 날 캐시 재사용")
        print(f"[KIS] 오늘({today}) 봉 캐시 재사용: {len(cached)}종목")
        return cached, st

    try:
        kis = KIS(key, sec).auth()
    except Exception as e:
        st.update(status="failed", reason=str(e)[:150])
        print(f"[KIS] {st['reason']} → 어제 종가로 계속")
        return pd.DataFrame(), st

    t0 = time.time()
    print(f"[KIS] 오늘({today}) 봉 수집 시작: {len(tickers)}종목 (종목당 2회 호출, 병렬)")
    # 앞 40종목을 먼저 받아 토큰·권한·네트워크를 확인한다. 전부 실패면 바로 포기.
    head = kis.map(lambda t: _fetch_one(kis, t, today_str), tickers[:40], progress_every=0)
    if not any(head):
        st.update(status="failed", reason="초반 40종목 전부 실패 — 토큰/권한/네트워크 문제로 보고 중단",
                  calls=kis.calls)
        print(f"[KIS] {st['reason']} → 어제 종가로 계속")
        return pd.DataFrame(), st
    rest = kis.map(lambda t: _fetch_one(kis, t, today_str), tickers[40:], progress_every=500)
    rows = []
    for r in head + rest:
        if r:
            r["date"] = pd.Timestamp(today)
            rows.append(r)

    df = pd.DataFrame(rows)
    st.update(fetched_at=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), n_ok=len(df),
              n_missing=len(tickers) - len(df), calls=kis.calls)
    if df.empty:
        st.update(status="failed", reason="수집 0종목")
        return df, st
    st["status"] = "ok" if len(df) >= 0.9 * len(tickers) else "partial"
    st["reason"] = "" if st["status"] == "ok" else f"{st['n_missing']}종목 15:30 봉 없음/실패(어제 값·stale로 처리)"
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_parquet(TODAY_CACHE, index=False)
    except Exception as e:
        print(f"[KIS] 오늘 봉 캐시 저장 실패(계속 진행): {e}")
    print(f"[KIS] 수집 완료: {len(df)}/{len(tickers)}종목, 호출 {kis.calls}회"
          f"{', 한도 재시도 ' + str(kis.retries) + '회' if kis.retries else ''}, {time.time() - t0:.0f}초 → {st['status']}")
    return df, st


def attach_today(data: dict, today_df: pd.DataFrame) -> dict:
    """
    fetch_kr_krx_open() 결과(dict of wide DataFrame)에 오늘 행을 덧붙인다.
    없는 종목은 NaN으로 남는다(screen()이 어제 값으로 채우고 stale 표시).
    """
    if today_df is None or today_df.empty:
        return data
    day = pd.Timestamp(today_df["date"].iloc[0]).normalize()
    t = today_df.set_index("ticker")
    cols = data["close"].columns
    for key, col in (("close", "close"), ("value", "value"), ("open", "open"),
                     ("high", "high"), ("low", "low"), ("volume", "volume")):
        if key not in data or data[key] is None:
            if key in ("close", "value"):
                continue
            # 캐시에 그 컬럼 자체가 없으면(옛 FDR 폴백 형태) 억지로 만들지 않는다
            continue
        wide = data[key]
        if day in wide.index:
            wide = wide.drop(index=day)
        row = t[col].reindex(cols).astype(float)
        row.name = day
        data[key] = pd.concat([wide, row.to_frame().T]).sort_index()
    return data


def apply(data: dict, wait: bool = True, now: dt.datetime | None = None) -> tuple:
    """
    run()·market_breadth에서 한 줄로 쓰는 진입점.
    조건을 보고(거래일·시각·캐시 날짜) 필요하면 기다렸다가 받아서 붙인다.
    반환: (data, status)
    """
    now = now or dt.datetime.now(KST)
    last = None
    try:
        last = pd.Timestamp(data["close"].index[-1]).date()
    except Exception:
        pass
    # [2026-10-13] 전날 장마감 실행이 받아 둔 오늘 봉 캐시가 KRX 캐시보다 새 날짜면
    # (미국 세션 새벽 실행: KRX 공식 값은 08:00에야 나옴) 새로 조회하지 않고 재사용한다.
    try:
        cached = pd.read_parquet(TODAY_CACHE)
        cdate = pd.to_datetime(cached["date"]).dt.date.iloc[0] if len(cached) else None
    except Exception:
        cached, cdate = None, None
    if cached is not None and cdate is not None and (last is None or cdate > last) and cdate <= now.date():
        data = attach_today(data, cached)
        st = {"status": "ok", "date": cdate.isoformat(), "source": "KIS", "fetched_at": "(이전 실행 캐시)",
              "n_requested": len(data["close"].columns), "n_ok": int(len(cached)),
              "n_missing": int(len(data["close"].columns) - len(cached)), "reason": "전날 장마감 실행의 KIS 캐시 재사용",
              "calls": 0}
        print(f"[KIS] 캐시 재사용: {cdate} 봉 {len(cached)}종목을 KRX 캐시(~{last})에 덧붙임")
        if cdate >= now.date():
            return data, st
        # 캐시가 어제 것이고 지금이 오늘 장마감 이후라면 오늘 봉도 이어서 받는다
        # (KRX 공식 값 공개가 늦어진 날 대비). 오늘 조건이 안 맞으면 캐시 재사용으로 끝.
        reused, last = st, cdate
        d = decide(now, last)
        if d["action"] == "skip":
            return data, reused
    else:
        d = decide(now, last)
    if d["action"] == "skip":
        st = {"status": "skipped", "date": now.date().isoformat(), "source": "KRX",
              "reason": d["reason"], "n_ok": 0, "n_missing": 0}
        print(f"[KIS] 오늘 봉 건너뜀: {d['reason']}")
        return data, st
    if d["action"] == "wait":
        if not wait:
            st = {"status": "skipped", "date": now.date().isoformat(), "source": "KRX",
                  "reason": d["reason"] + " (대기 안 함)", "n_ok": 0, "n_missing": 0}
            return data, st
        print(f"[KIS] {d['reason']}")
        time.sleep(d["wait_sec"])
    today_df, st = fetch_today(list(data["close"].columns), now=dt.datetime.now(KST), wait=wait)
    if st["status"] in ("ok", "partial"):
        data = attach_today(data, today_df)
        print(f"[KIS] 오늘 봉 {st['n_ok']}종목을 KRX 캐시(~{last})에 덧붙임 → 데이터 기준일 {st['date']}")
    return data, st


if __name__ == "__main__":
    # 수동 점검: python kis_kr_today.py 005930 000660
    tk = sys.argv[1:] or ["005930", "000660", "009150"]
    df, status = fetch_today(tk, wait=False)
    print(status)
    print(df)
