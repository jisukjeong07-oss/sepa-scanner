# -*- coding: utf-8 -*-
"""
한국 시장 데이터 수집 — FinanceDataReader(네이버) 기반

pykrx 대체 모듈. KRX 회원제 전환 이후 pykrx는 로그인이 필요하고
호출량이 조금만 많아도 IP가 차단되어 매일 돌리는 시스템에 부적합하다.
FinanceDataReader는 네이버 데이터를 사용하므로 KRX 로그인·차단과 무관하다.

pykrx와의 차이:
  - pykrx: 날짜별 전 종목 조회 (약 560회 호출)
  - FDR:   종목별 전 기간 조회 (약 2,800회 호출, 대신 1회로 1년치를 받음)

종목 단위로 이어받기를 지원하므로 중간에 끊겨도 재실행하면 이어진다.

[2026-09-09 추가] fdr.StockListing() 폴백
  FDR이 참조하는 KRX 시가총액 캐시 CSV가 유지관리자 서버에서 사라져
  StockListing()이 404를 던지는 일이 생겼다. 로컬 macOS와 GitHub Actions
  양쪽에서 재현되었고 최신 버전(0.9.202)에서도 동일해, 우리 환경이 아니라
  FDR 쪽 캐시 파일 문제로 확인했다. 반면 fdr.DataReader()(개별 시세 조회)는
  이 캐시와 무관해 정상 동작한다.
  그래서 kr_listing()에만 pykrx 폴백을 추가한다. 일봉 수집(fetch_kr_fdr)은
  손대지 않는다 — 거긴 이 문제와 무관하게 정상 작동 중이다.
  상장 종목은 하루에 몇 개 바뀌는 수준이라, 목록 조회가 완전히 막혀도
  마지막으로 저장된 캐시로 스캔을 계속할 수 있게 했다.

설치:
    python3 -m pip install finance-datareader
"""

import os
import time

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

FDR_PARTIAL = os.path.join(CACHE_DIR, "kr_fdr_partial")

# 수집 파라미터
SLEEP_SEC = 0.15          # 종목 간 대기 (네이버 배려)
SAVE_EVERY = 100          # 몇 종목마다 중간 저장할지
MAX_FAIL_STREAK = 40      # 연속 실패 허용치 (넘으면 차단 의심 → 중단)

# 목록 캐시 유효기간. 평소엔 새로고침용 상한이지만, 조회가 전부 실패하면
# 기간이 지났어도 이 캐시를 최후의 수단으로 계속 사용한다.
LISTING_MAX_AGE_DAYS = 7


# ═════════════════════════════════════════════════════════════
# 캐시 입출력 (pyarrow 없으면 pickle로 대체)
# ═════════════════════════════════════════════════════════════

def _write(df: pd.DataFrame, path: str):
    try:
        df.to_parquet(path + ".parquet")
    except Exception:
        df.to_pickle(path + ".pkl")


def _read(path: str) -> pd.DataFrame:
    for ext, fn in ((".parquet", pd.read_parquet), (".pkl", pd.read_pickle)):
        if os.path.exists(path + ext):
            try:
                return fn(path + ext)
            except Exception:
                continue
    raise FileNotFoundError(path)


def _exists(path: str) -> bool:
    return any(os.path.exists(path + e) for e in (".parquet", ".pkl"))


def _cache_age_days(path: str):
    for e in (".parquet", ".pkl"):
        if os.path.exists(path + e):
            return (time.time() - os.path.getmtime(path + e)) / 86400
    return None


# ═════════════════════════════════════════════════════════════
# 종목 목록
# ═════════════════════════════════════════════════════════════

def _listing_from_fdr() -> pd.DataFrame:
    """FDR(네이버) 기반 목록 조회. 실패하면 예외를 그대로 던진다."""
    import FinanceDataReader as fdr

    frames = []
    for mkt in ("KOSPI", "KOSDAQ"):
        df = fdr.StockListing(mkt)
        df.columns = [str(c) for c in df.columns]

        code_col = next((c for c in ("Code", "Symbol", "종목코드") if c in df.columns), None)
        name_col = next((c for c in ("Name", "종목명") if c in df.columns), None)
        if code_col is None:
            raise RuntimeError(f"종목코드 컬럼을 찾지 못했습니다: {list(df.columns)[:10]}")

        cap_col = next((c for c in ("Marcap", "MarketCap", "Market Cap", "시가총액")
                       if c in df.columns), None)

        out = pd.DataFrame({
            "code": df[code_col].astype(str).str.zfill(6),
            "name": df[name_col].astype(str) if name_col else df[code_col].astype(str),
            "market": mkt,
        })
        out["market_cap"] = (pd.to_numeric(df[cap_col], errors="coerce").values
                              if cap_col else pd.NA)

        out = out[out["code"].str.match(r"^\d{6}$")]
        out = out[~out["name"].str.contains("스팩|제[0-9]+호", na=False)]
        frames.append(out)
        if cap_col:
            print(f"[FDR] {mkt} {len(out):,}종목 (시가총액 컬럼 '{cap_col}' 확인됨)")
        else:
            print(f"[FDR] {mkt} {len(out):,}종목 "
                 f"(시가총액 컬럼을 찾지 못함 — 사용 가능한 컬럼: {list(df.columns)[:10]})")

    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("code")
              .set_index("code"))


def _listing_from_pykrx() -> pd.DataFrame:
    """
    pykrx 폴백. FDR의 KrxMarcapListingCache와 달리 KRX를 직접 호출하므로
    그 캐시 파일과 무관하게 동작한다. 시가총액도 함께 받아온다.
    """
    from pykrx import stock

    today = pd.Timestamp.now(tz="Asia/Seoul")
    # 휴장일에 걸리면 빈 응답이 오므로 최근 5영업일 중 값이 있는 날짜를 찾는다.
    frames = []
    for mkt in ("KOSPI", "KOSDAQ"):
        df = None
        for back in range(5):
            d = (today - pd.Timedelta(days=back)).strftime("%Y%m%d")
            try:
                cand = stock.get_market_cap_by_ticker(d, market=mkt)
            except Exception:
                cand = None
            if cand is not None and not cand.empty:
                df = cand
                break
        if df is None:
            raise RuntimeError(f"pykrx {mkt} 시가총액 조회가 계속 비어 있습니다.")

        df = df.reset_index().rename(columns={"티커": "code"})
        cap_col = next((c for c in df.columns if "시가총액" in c), None)
        out = pd.DataFrame({
            "code": df["code"].astype(str).str.zfill(6),
            "market": mkt,
            "market_cap": pd.to_numeric(df[cap_col], errors="coerce") if cap_col else pd.NA,
        })

        # 종목명은 별도 호출. 시세 없이 이름만 물어보는 거라 부담이 적다.
        names = {}
        for code in out["code"]:
            try:
                names[code] = stock.get_market_ticker_name(code)
            except Exception:
                names[code] = code
        out["name"] = out["code"].map(names)

        out = out[out["code"].str.match(r"^\d{6}$")]
        out = out[~out["name"].str.contains("스팩|제[0-9]+호", na=False)]
        frames.append(out)
        print(f"[pykrx] {mkt} {len(out):,}종목 (FDR 목록 폴백)")

    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("code")
              .set_index("code"))


def kr_listing() -> pd.DataFrame:
    """
    KOSPI/KOSDAQ 상장 종목 목록.
    반환: index=종목코드, columns=[name, market, market_cap]

    순서: 최신 캐시(7일 이내) → FDR → pykrx 폴백 → 오래된 캐시라도 재사용.
    상장 종목은 하루에 몇 개 바뀌는 수준이라, 마지막 두 단계까지 가더라도
    스캔 자체가 무의미해지지는 않는다.
    """
    cache = os.path.join(CACHE_DIR, "kr_listing")

    age = _cache_age_days(cache)
    if age is not None and age < LISTING_MAX_AGE_DAYS:
        return _read(cache)

    try:
        listing = _listing_from_fdr()
        _write(listing, cache)
        return listing
    except Exception as e:
        print(f"[FDR] 종목 목록 조회 실패({str(e)[:120]}) → pykrx로 폴백합니다.")

    try:
        listing = _listing_from_pykrx()
        _write(listing, cache)
        return listing
    except Exception as e:
        print(f"[pykrx] 종목 목록 조회도 실패({str(e)[:120]})")

    # 최후 수단: 기간이 지났어도 저장된 캐시가 있으면 그걸로 계속 간다.
    if _exists(cache):
        stale = age if age is not None else -1
        print(f"[경고] 목록 조회가 모두 실패해 {stale:.1f}일 전 캐시를 재사용합니다. "
              f"신규 상장·상장폐지가 반영되지 않았을 수 있습니다.")
        return _read(cache)

    raise RuntimeError(
        "한국 종목 목록을 FDR과 pykrx 양쪽 모두에서 가져오지 못했고, "
        "재사용할 이전 캐시도 없습니다. 네트워크 상태를 확인해 주세요."
    )


def kr_market_caps(tickers) -> dict:
    """종목코드 -> 시가총액(원). kr_listing() 캐시에 컬럼이 없으면 전부 None."""
    try:
        listing = kr_listing()
    except Exception:
        return {t: None for t in tickers}
    if "market_cap" not in listing.columns:
        return {t: None for t in tickers}
    out = {}
    for t in tickers:
        v = listing["market_cap"].get(t) if t in listing.index else None
        out[t] = None if pd.isna(v) else float(v)
    return out


# ═════════════════════════════════════════════════════════════
# 일봉 수집 (종목 단위 이어받기)
# ═════════════════════════════════════════════════════════════

def fetch_kr_fdr(start: str, end: str, limit: int = None) -> dict:
    """
    FinanceDataReader로 한국 전 종목 일봉 수집.

    start, end: 'YYYYMMDD'
    limit: 테스트용. 앞에서 N종목만 수집.

    반환: {"close": wide DF, "value": wide DF, "meta": DF}
          value = 종가 × 거래량 (거래대금 근사)
    """
    import FinanceDataReader as fdr

    final_cache = os.path.join(CACHE_DIR, f"kr_fdr_{end}")
    if _exists(final_cache):
        print(f"[FDR] 캐시 사용: {final_cache}")
        return _unpack_fdr(_read(final_cache))

    listing = kr_listing()
    codes = list(listing.index)
    if limit:
        codes = codes[:limit]

    s = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e = f"{end[:4]}-{end[4:6]}-{end[6:]}"

    # ── 이어받기 ────────────────────────────────────────────
    frames, done = [], set()
    if _exists(FDR_PARTIAL):
        prev = _read(FDR_PARTIAL)
        prev["date"] = pd.to_datetime(prev["date"])
        prev = prev[(prev["date"] >= pd.Timestamp(s)) & (prev["date"] <= pd.Timestamp(e))]
        if not prev.empty:
            frames.append(prev)
            done = set(prev["ticker"].unique())
            print(f"[FDR] 이어받기: 이미 {len(done):,}종목 수집됨")

    todo = [c for c in codes if c not in done]
    print(f"[FDR] 전체 {len(codes):,}종목 중 {len(todo):,}종목 수집 시작 "
          f"(예상 {len(todo)*SLEEP_SEC/60:.0f}~{len(todo)*0.5/60:.0f}분)")

    def _save():
        if frames:
            _write(pd.concat(frames, ignore_index=True), FDR_PARTIAL)

    fail_streak, ok = 0, 0
    for i, code in enumerate(todo):
        try:
            df = fdr.DataReader(code, s, e)
        except Exception as ex:
            fail_streak += 1
            if fail_streak <= 3 or fail_streak % 10 == 0:
                print(f"  [warn] {code}: {str(ex)[:70]}")
            if fail_streak >= MAX_FAIL_STREAK:
                _save()
                raise RuntimeError(
                    f"{fail_streak}종목 연속 실패했습니다. 네트워크 문제이거나 "
                    f"데이터 제공처가 응답하지 않습니다.\n"
                    f"  지금까지 {len(done)+ok:,}종목은 저장했습니다.\n"
                    f"  → 잠시 후 같은 명령을 다시 실행하면 이어서 수집합니다."
                )
            time.sleep(1.0)
            continue

        if df is None or df.empty or "Close" not in df.columns:
            fail_streak = 0
            continue

        vol = df["Volume"] if "Volume" in df.columns else pd.Series(0, index=df.index)
        rec = pd.DataFrame({
            "date": pd.to_datetime(df.index),
            "ticker": code,
            "close": df["Close"].astype(float).values,
            "value": (df["Close"].astype(float) * vol.astype(float)).values,
            "market": listing.loc[code, "market"] if code in listing.index else "KR",
        })
        frames.append(rec.reset_index(drop=True))
        ok += 1
        fail_streak = 0

        if (i + 1) % SAVE_EVERY == 0:
            _save()
            print(f"  ... {i+1:,}/{len(todo):,}  (누적 {len(done)+ok:,}종목)")

        time.sleep(SLEEP_SEC)

    _save()

    if not frames:
        raise RuntimeError("수집된 데이터가 없습니다.")

    long_df = pd.concat(frames, ignore_index=True)
    n_days = long_df["date"].nunique()
    n_tick = long_df["ticker"].nunique()
    coverage = n_tick / max(len(codes), 1)
    print(f"[FDR] 수집 결과: {n_tick:,}종목 / {n_days}영업일 / {len(long_df):,}행 "
          f"(요청 종목 대비 {coverage:.0%})")

    # 상당수 종목이 빠진 채로 최종 캐시를 만들면, 다음 실행부터 불완전한
    # 데이터를 계속 재사용하게 된다. 중간 캐시만 남기고 재실행을 유도한다.
    if coverage < 0.8:
        raise RuntimeError(
            f"요청한 {len(codes):,}종목 중 {n_tick:,}종목만 수집되었습니다 ({coverage:.0%}).\n"
            f"  수집분은 저장했으니 데이터는 버려지지 않습니다.\n"
            f"  → 잠시 후 같은 명령을 다시 실행하면 빠진 종목만 이어서 받습니다."
        )

    if n_days < 252:
        raise RuntimeError(
            f"확보된 영업일이 {n_days}일로 부족합니다 (252일 필요).\n"
            f"  조회 기간을 늘리거나(LOOKBACK_DAYS), 재실행해 주세요."
        )

    _write(long_df, final_cache)
    print(f"[FDR] 최종 캐시 저장: {final_cache}")
    return _unpack_fdr(long_df)


def _unpack_fdr(long_df: pd.DataFrame) -> dict:
    """long → wide 변환. sepa_scanner.screen() 이 기대하는 형태로 맞춘다."""
    long_df = long_df.copy()
    long_df["date"] = pd.to_datetime(long_df["date"])
    close = long_df.pivot_table(index="date", columns="ticker", values="close")
    value = long_df.pivot_table(index="date", columns="ticker", values="value")
    meta = long_df.groupby("ticker")["market"].last().to_frame()
    return {"close": close.sort_index(),
            "value": value.sort_index().reindex(columns=close.columns),
            "meta": meta}


def fdr_names(tickers) -> dict:
    """종목코드 → 종목명"""
    try:
        listing = kr_listing()
        return {t: listing.loc[t, "name"] if t in listing.index else t for t in tickers}
    except Exception:
        return {t: t for t in tickers}


if __name__ == "__main__":
    import argparse
    import datetime as dt

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="테스트용 종목 수")
    ap.add_argument("--days", type=int, default=420)
    a = ap.parse_args()

    today = dt.date.today()
    st = (today - dt.timedelta(days=a.days)).strftime("%Y%m%d")
    en = today.strftime("%Y%m%d")
    print(f"테스트 수집: {st} ~ {en}, {a.limit}종목")

    d = fetch_kr_fdr(st, en, limit=a.limit)
    print("close 형태:", d["close"].shape)
    print(d["close"].iloc[-3:, :5])
