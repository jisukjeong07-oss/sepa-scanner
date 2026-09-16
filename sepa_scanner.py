# -*- coding: utf-8 -*-
"""
SEPA Trend Template Scanner
Mark Minervini 트렌드템플릿 8조건 + RS Rating 정량 스캔

데이터 소스:
  - 한국: pykrx (KRX 공식 데이터, 무료)
  - 미국: yfinance (무료, 비공식 — 운영 단계에선 EODHD 등으로 교체 권장)

사용법:
  python sepa_scanner.py --market KR
  python sepa_scanner.py --market US
  python sepa_scanner.py --market ALL --min-rs 80
"""

import os
import time
import json
import argparse
import datetime as dt
import warnings

import numpy as np
import pandas as pd

# ── .env 자동 로딩 ────────────────────────────────────────────
# 같은 폴더의 .env 파일에서 API 키를 읽어 환경변수로 등록한다.
# python-dotenv 가 설치돼 있으면 그것을 쓰고, 없으면 직접 파싱한다.
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
# ─────────────────────────────────────────────────────────────


warnings.filterwarnings("ignore")

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# 스캔 파라미터 (필요에 맞게 조정)
# ─────────────────────────────────────────────────────────────
LOOKBACK_DAYS = 420          # 200일선 + 여유분 확보 (약 2년치 영업일 아님, 캘린더 기준)
MIN_RS = 70                  # 트렌드템플릿 8번 조건: RS Rating 하한 (미네르비니 권장 70+, 이상적 80~90)
NEAR_HIGH_PCT = 25.0         # 조건7: 52주 신고가 대비 -25% 이내
ABOVE_LOW_PCT = 30.0         # 조건6: 52주 신저가 대비 +30% 이상
MA200_SLOPE_DAYS = 21        # 조건3: 200일선이 최소 1개월(≈21영업일) 상승
MIN_PRICE_KR = 2000          # 동전주 제외
MIN_PRICE_US = 10.0
# 유동성 하한: 손절이 필요할 때 슬리피지 없이 빠져나올 수 있는 수준이어야 한다.
# 하루 10~20억 거래되는 종목에서 -7% 손절은 체결 기준으로 -12%가 되기도 한다.
# 명령행 --min-turnover(억원 단위)로 실행마다 덮어쓸 수 있다.
MIN_TURNOVER_KR = 5_000_000_000   # 20일 평균 거래대금 50억원 이상
MIN_DOLLAR_VOL_US = 10_000_000    # 20일 평균 거래대금 $10M 이상

# 이격 지속일수 판정 기준.
# [2026-09-15] "50일선에서 며칠째 벌어져 있나"를 보려는 목적. 연속 스트릭이 아니라
# 최근 DEV_LOOKBACK거래일 중 |이격| > DEV_THRESHOLD 인 날의 '총 횟수'로 센다.
# 연속으로 세면 하루만 눌려도 카운트가 0으로 리셋돼 재베이스 진행 상황을 놓친다.
# 누적 횟수는 그런 흔들림에 끊기지 않고, 대시보드에서 "5~15일" 식 범위 필터가 가능하다.
DEV_THRESHOLD = 15.0
DEV_LOOKBACK = 60


# ═════════════════════════════════════════════════════════════
# 1. 데이터 수집
# ═════════════════════════════════════════════════════════════

def _kr_business_days(start: str, end: str) -> list:
    """
    수집 대상 영업일 목록.

    pykrx의 get_previous_business_days()는 KRX 서버에 의존하는데, 서버가
    빈 응답을 주면 목록이 0개가 되어 전체 수집이 조용히 실패한다.
    그래서 실패 시 로컬에서 평일 목록을 생성해 대체한다.
    공휴일은 조회 결과가 비어 있으면 자동으로 건너뛰므로 문제되지 않는다.
    """
    from pykrx import stock
    try:
        bdays = stock.get_previous_business_days(fromdate=start, todate=end)
        if bdays is not None and len(bdays) > 0:
            return list(bdays)
        print("[KR] KRX 영업일 조회가 비어 있음 → 로컬 평일 목록으로 대체")
    except Exception as e:
        print(f"[KR] KRX 영업일 조회 실패({e}) → 로컬 평일 목록으로 대체")

    return list(pd.bdate_range(start=pd.to_datetime(start), end=pd.to_datetime(end)))


PARTIAL_CACHE = "kr_partial.parquet"   # 중단되어도 이어받을 수 있는 누적 캐시


def _write_cache(df: pd.DataFrame, path: str):
    """parquet 우선. pyarrow가 없는 환경에서는 pickle로 대체 저장."""
    try:
        df.to_parquet(path)
    except (ImportError, ValueError):
        df.to_pickle(path + ".pkl")


def _read_cache(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            return pd.read_parquet(path)
        except (ImportError, ValueError):
            pass
    return pd.read_pickle(path + ".pkl")


def _cache_exists(path: str) -> bool:
    return os.path.exists(path) or os.path.exists(path + ".pkl")


def _krx_relogin():
    """KRX 세션 만료 시 재로그인 시도. pykrx 버전마다 함수 위치가 달라 넓게 탐색."""
    import os as _os
    uid, pw = _os.environ.get("KRX_ID"), _os.environ.get("KRX_PW")
    if not uid or not pw:
        return False
    try:
        from pykrx.website.krx.krxio import login as _login   # 버전에 따라 존재
        _login(uid, pw)
        return True
    except Exception:
        pass
    try:
        from pykrx import stock as _s
        for name in ("login", "krx_login", "set_credential"):
            fn = getattr(_s, name, None)
            if callable(fn):
                fn(uid, pw)
                return True
    except Exception:
        pass
    return False


def fetch_kr(start: str, end: str, markets=("KOSPI", "KOSDAQ")) -> dict:
    """
    pykrx로 한국 전 종목 일봉을 수집.

    날짜별 전 종목 조회를 사용해 호출 수를 줄인다(종목별이면 2,800회).

    KRX 회원제 전환 이후 다음 두 가지가 실전에서 문제가 된다.
      1) 요청이 일정 횟수를 넘으면 차단됨
      2) 로그인 세션이 1시간이라 장시간 수집 중 만료됨
    그래서 (a) 수집분을 누적 캐시에 계속 저장해 재실행 시 이어받고,
    (b) 실패하면 대기 후 재시도하며 재로그인을 시도한다.
    """
    from pykrx import stock

    final_cache = os.path.join(CACHE_DIR, f"kr_{end}.parquet")
    if _cache_exists(final_cache):
        print(f"[KR] 캐시 사용: {final_cache}")
        return _unpack(_read_cache(final_cache))

    bdays = _kr_business_days(start, end)
    if not bdays:
        raise RuntimeError("수집할 영업일이 없습니다. 날짜 범위를 확인하세요.")

    # ── 이어받기: 이미 모아둔 날짜는 건너뛴다 ──────────────────
    partial_path = os.path.join(CACHE_DIR, PARTIAL_CACHE)
    frames, done_dates = [], set()
    if _cache_exists(partial_path):
        prev = _read_cache(partial_path)
        prev = prev[(prev["date"] >= pd.Timestamp(bdays[0])) &
                    (prev["date"] <= pd.Timestamp(bdays[-1]))]
        if not prev.empty:
            frames.append(prev)
            done_dates = set(pd.to_datetime(prev["date"]).dt.normalize())
            print(f"[KR] 이어받기: 이미 {len(done_dates)}일 수집됨 "
                  f"({prev['date'].min():%Y-%m-%d} ~ {prev['date'].max():%Y-%m-%d})")

    todo = [d for d in bdays if pd.Timestamp(d).normalize() not in done_dates]
    print(f"[KR] 전체 {len(bdays)}영업일 중 {len(todo)}일 수집 시작...")
    if not todo:
        print("[KR] 새로 받을 날짜가 없습니다.")

    def _save_partial():
        if frames:
            _write_cache(pd.concat(frames, ignore_index=True), partial_path)

    MAX_RETRY = 3
    fail_streak = 0
    collected = 0

    for i, d in enumerate(todo):
        ds = d.strftime("%Y%m%d")
        got_any = False

        for mkt in markets:
            for attempt in range(MAX_RETRY):
                try:
                    df = stock.get_market_ohlcv_by_ticker(ds, market=mkt)
                except Exception as e:
                    if attempt == MAX_RETRY - 1:
                        print(f"  [warn] {ds} {mkt}: {str(e)[:80]}")
                        df = None
                    else:
                        # 차단·세션만료 추정 → 점점 길게 쉬고 재로그인 시도
                        wait = 15 * (attempt + 1)
                        print(f"  [retry] {ds} {mkt} {wait}초 대기 후 재시도...")
                        time.sleep(wait)
                        _krx_relogin()
                        continue
                break

            if df is None or df.empty:
                continue
            df = df.rename(columns={"종가": "close", "거래대금": "value", "거래량": "volume"})
            if "close" not in df.columns or "value" not in df.columns:
                continue                      # 휴장일 등 빈 응답
            df = df[["close", "value"]].copy()
            df["date"] = pd.Timestamp(d)
            df["ticker"] = df.index
            df["market"] = mkt
            frames.append(df.reset_index(drop=True))
            got_any = True

        if got_any:
            collected += 1
            fail_streak = 0
        else:
            fail_streak += 1

        # 연속 실패가 길면 차단 상태. 여기까지를 저장하고 깔끔히 멈춘다.
        if fail_streak >= 10:
            _save_partial()
            raise RuntimeError(
                f"KRX 응답이 {fail_streak}일 연속 실패했습니다. 차단 또는 세션 만료로 보입니다.\n"
                f"  지금까지 수집분은 저장했습니다 ({len(done_dates)+collected}일).\n"
                f"  → 10~30분 뒤 같은 명령을 다시 실행하면 이어서 수집합니다.\n"
                f"  (남은 날짜: 약 {len(todo)-i-1}일)"
            )

        if (i + 1) % 20 == 0:
            _save_partial()               # 20일마다 중간 저장
            print(f"  ... {i+1}/{len(todo)}  (누적 {len(done_dates)+collected}일)")

        time.sleep(0.5)    # 차단 방지: 회원제 전환 후 여유를 더 둔다

    _save_partial()

    if not frames:
        raise RuntimeError("수집된 데이터가 없습니다. 위 [warn] 메시지를 확인하세요.")

    long_df = pd.concat(frames, ignore_index=True)
    n_days = long_df["date"].nunique()
    if n_days < 252:
        raise RuntimeError(
            f"수집된 영업일이 {n_days}일로 부족합니다 (252일 필요).\n"
            f"  → 잠시 후 같은 명령을 다시 실행하면 이어서 수집합니다."
        )

    _write_cache(long_df, final_cache)
    print(f"[KR] 캐시 저장: {final_cache} ({len(long_df):,}행 / {n_days}영업일)")
    return _unpack(long_df)


def _unpack(long_df: pd.DataFrame) -> dict:
    """long format -> {close: wide DF, value: wide DF, meta: DF}"""
    close = long_df.pivot_table(index="date", columns="ticker", values="close")
    value = long_df.pivot_table(index="date", columns="ticker", values="value")
    meta = long_df.groupby("ticker")["market"].last().to_frame()
    return {"close": close.sort_index(), "value": value.sort_index(), "meta": meta}


def kr_names(tickers) -> dict:
    from pykrx import stock
    out = {}
    for t in tickers:
        try:
            out[t] = stock.get_market_ticker_name(t)
        except Exception:
            out[t] = t
    return out


def _http_get(url: str, timeout: int = 20) -> str:
    """
    브라우저처럼 요청한다.
    위키피디아 등은 기본 파이썬 User-Agent를 봇으로 보고 403으로 거부한다.
    """
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _save_us_universe(cache: str, tickers: list, names: dict):
    with open(cache, "w", encoding="utf-8") as f:
        for t in tickers:
            f.write(f"{t}\t{names.get(t, t)}\n")


def _load_us_universe(cache: str) -> tuple:
    tickers, names = [], {}
    with open(cache, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t", 1)
            t = parts[0]
            tickers.append(t)
            names[t] = parts[1] if len(parts) > 1 else t
    return tickers, names


def us_universe(min_expected: int = 100) -> list:
    """
    미국 스캔 유니버스(S&P500) 티커 목록. 이름은 us_names() 캐시에 함께 저장된다.

    RS는 유니버스 내 백분위이므로, 목록이 몇 종목으로 쪼그라들면 RS 자체가
    무의미해진다. 따라서 소수만 확보되면 조용히 넘어가지 않고 실패시킨다.
    """
    cache = os.path.join(CACHE_DIR, "us_universe.txt")

    # 1) 위키피디아 (브라우저 UA로 요청) — 티커와 회사명을 함께 확보
    try:
        from io import StringIO
        html = _http_get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        tables = pd.read_html(StringIO(html))
        for t in tables:
            if "Symbol" in t.columns:
                sym = (t["Symbol"].astype(str).str.strip()
                       .str.replace(".", "-", regex=False))
                name_col = next((c for c in ("Security", "Company", "Name")
                                if c in t.columns), None)
                nm = t[name_col].astype(str).str.strip() if name_col else sym
                pairs = dict(zip(sym, nm))
                tickers = sorted(set(sym))
                if len(tickers) >= min_expected:
                    _save_us_universe(cache, tickers, pairs)
                    print(f"[US] S&P500 목록 {len(tickers)}종목 확보 (위키피디아)")
                    return tickers
        print("[US] 위키 표에서 Symbol 컬럼을 찾지 못함")
    except Exception as e:
        print(f"[US] 위키 조회 실패({str(e)[:60]})")

    # 2) 공개 CSV 미러
    for url in (
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
    ):
        try:
            from io import StringIO
            csv_txt = _http_get(url)
            df = pd.read_csv(StringIO(csv_txt))
            col = next((c for c in ("Symbol", "symbol") if c in df.columns), None)
            name_col = next((c for c in ("Name", "Security", "name") if c in df.columns), None)
            if col:
                sym = df[col].astype(str).str.strip().str.replace(".", "-", regex=False)
                nm = df[name_col].astype(str).str.strip() if name_col else sym
                pairs = dict(zip(sym, nm))
                tickers = sorted(set(sym))
                if len(tickers) >= min_expected:
                    _save_us_universe(cache, tickers, pairs)
                    print(f"[US] S&P500 목록 {len(tickers)}종목 확보 (CSV 미러)")
                    return tickers
        except Exception as e:
            print(f"[US] CSV 미러 실패({str(e)[:50]})")

    # 3) 이전 실행에서 저장해둔 목록
    if os.path.exists(cache):
        tickers, _ = _load_us_universe(cache)
        if len(tickers) >= min_expected:
            print(f"[US] 저장된 목록 재사용 ({len(tickers)}종목)")
            return tickers

    raise RuntimeError(
        "S&P500 종목 목록을 가져오지 못했습니다.\n"
        "  RS Rating은 유니버스 내 백분위라, 소수 종목만으로 스캔하면\n"
        "  결과가 무의미해집니다. 그래서 진행하지 않고 멈춥니다.\n"
        "  - 네트워크 상태를 확인한 뒤 다시 실행해 보세요.\n"
        "  - 계속 실패하면 티커 목록을 cache/us_universe.txt 에\n"
        "    한 줄에 하나씩 직접 저장해두면 그 목록을 사용합니다."
    )


def us_names(tickers) -> dict:
    """종목코드 -> 회사명. us_universe() 호출 시 저장된 캐시를 사용한다."""
    cache = os.path.join(CACHE_DIR, "us_universe.txt")
    if os.path.exists(cache):
        _, names = _load_us_universe(cache)
        return {t: names.get(t, t) for t in tickers}
    return {t: t for t in tickers}


def fetch_us(tickers: list, start: str = None, end: str = None, period: str = "2y") -> dict:
    """
    yfinance 배치 다운로드. 100개씩 끊어서 요청.
    start/end(YYYYMMDD)를 주면 그 구간으로, 안 주면 오늘 기준 period(기본 2y)로 받는다.
    과거 특정 날짜를 '오늘'인 것처럼 스캔하고 싶을 때 end를 그 날짜로 지정한다.
    """
    import yfinance as yf

    kwargs = {}
    if start and end:
        s = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        # yfinance의 end는 배타적(그 날짜 자체는 제외)이라 하루를 더해줘야
        # 지정한 종료일 종가까지 포함된다.
        e_date = dt.datetime.strptime(end, "%Y%m%d").date() + dt.timedelta(days=1)
        kwargs = {"start": s, "end": e_date.strftime("%Y-%m-%d")}
    else:
        kwargs = {"period": period}

    closes, volumes, highs, lows = [], [], [], []
    for i in range(0, len(tickers), 100):
        batch = tickers[i:i + 100]
        print(f"[US] {i+1}~{i+len(batch)} / {len(tickers)} 다운로드...")
        df = yf.download(batch, auto_adjust=True,
                         progress=False, group_by="column", threads=True, **kwargs)
        if df is None or df.empty:
            continue
        closes.append(df["Close"])
        volumes.append(df["Volume"])
        if "High" in df.columns.get_level_values(0):
            highs.append(df["High"])
            lows.append(df["Low"])
        time.sleep(1.0)

    close = pd.concat(closes, axis=1)
    volume = pd.concat(volumes, axis=1)
    value = close * volume          # 거래대금(달러)
    meta = pd.DataFrame({"market": "US"}, index=close.columns)
    out = {"close": close.sort_index(), "value": value.sort_index(), "meta": meta}
    # [2026-09-13] 52주 신고가/VCP 계산용 고가·저가. yfinance가 어차피
    # 같이 내려주는 값이라 추가 호출 비용이 없다.
    if highs:
        out["high"] = pd.concat(highs, axis=1).sort_index().reindex(columns=close.columns)
        out["low"] = pd.concat(lows, axis=1).sort_index().reindex(columns=close.columns)
    return out


# ═════════════════════════════════════════════════════════════
# 2. RS Rating (IBD 방식 근사)
# ═════════════════════════════════════════════════════════════

def rs_rating(close: pd.DataFrame) -> pd.Series:
    """
    IBD RS Rating은 유료 독점 지표이므로, 공개된 표준 근사식으로 대체:
        RS Score = 2*(3개월 수익률) + 1*(6개월) + 1*(9개월) + 1*(12개월)
    이후 유니버스 내 백분위(1~99)로 환산.
    """
    n = len(close)
    def ret(days):
        if n <= days:
            return pd.Series(np.nan, index=close.columns)
        return close.iloc[-1] / close.iloc[-1 - days] - 1.0

    score = 2 * ret(63) + ret(126) + ret(189) + ret(252)
    pct = score.rank(pct=True) * 98 + 1        # 1~99 스케일
    return pct.round(0)


# ═════════════════════════════════════════════════════════════
# 2.5 회전율 · 이격 지속일수
# [2026-09-15] 9/8~9/15 브리핑에서 반도체 후공정·저유동성 테마주를 분석하며
# 나온 요구사항. "얼마나 올랐나"만으로는 실적 기반 상승과 단타 수급을
# 구분할 수 없어서, 회전율(수급 과열 판별)과 이격 지속일수(재베이스 타이밍
# 판별)를 추가한다.
# ═════════════════════════════════════════════════════════════

def deviation_days(close: pd.DataFrame, ma50: pd.DataFrame,
                    threshold: float = DEV_THRESHOLD,
                    lookback: int = DEV_LOOKBACK) -> pd.DataFrame:
    """
    50일선 이격이 최근 lookback거래일 중 며칠이나 threshold%를 넘었는지.

    반환 컬럼:
      dev_days      최근 lookback거래일 중 |이격| > threshold 인 날의 개수
      dev_days_now  오늘도 초과 중인지(bool)
      dev_peak      그 구간 내 최대 |이격|%
      dev_trend     최근 5일 초과일수 vs 그 이전 5일 초과일수 방향 ("↓"/"→"/"↑")
                    "↓"면 좁혀지는 중 — 재베이스가 진행되고 있다는 신호.

    50일선이 아직 없는 종목(상장 50일 미만 등)은 전부 None으로 채운다.
    """
    dev_pct = (close / ma50 - 1) * 100
    window = dev_pct.tail(lookback)
    exceed = window.abs() > threshold

    dev_days = exceed.sum(axis=0)
    dev_days_now = exceed.iloc[-1]
    dev_peak = window.abs().max(axis=0).round(1)

    last5 = exceed.tail(5).sum(axis=0)
    if len(exceed) >= 10:
        prev5 = exceed.tail(10).head(5).sum(axis=0)
    else:
        prev5 = pd.Series(0, index=exceed.columns)
    dev_trend = pd.Series(
        np.where(last5 < prev5, "↓", np.where(last5 > prev5, "↑", "→")),
        index=exceed.columns,
    )

    out = pd.DataFrame({
        "dev_days": dev_days,
        "dev_days_now": dev_days_now,
        "dev_peak": dev_peak,
        "dev_trend": dev_trend,
    }).astype(object)   # None을 섞어 넣을 것이므로 처음부터 object dtype으로
    no_data = ma50.iloc[-1].isna()
    out.loc[no_data, :] = None
    return out


def add_turnover_ratio(r: pd.DataFrame) -> pd.DataFrame:
    """
    회전율(%) = 당일(또는 5일평균) 거래대금 ÷ 시가총액 × 100.
    호출 시점에 r["market_cap"]이 이미 채워져 있어야 한다(run()에서 KR/US
    각각 market_cap을 붙인 직후 호출).

    한계 2가지:
      - market_cap은 조회 '현재' 시점 값이라 과거 이력이 없다. 5일평균도
        이 현재 시총 하나로 나누므로, 그 며칠 사이 유상증자·감자 등으로
        시총이 크게 바뀐 종목은 오차가 커진다.
      - 대주주 지분 비중이 큰 종목은 분모(전체 시총)가 실제 유동주식수보다
        커서 회전율이 실제보다 낮게 나온다. free float 데이터가 없어
        현재는 보정하지 않는다.
    """
    cap = r["market_cap"]
    r["turnover_ratio"] = (r["trade_value_today"] / cap * 100).round(2)
    r["turnover_ratio_5d"] = (r["trade_value_5d_avg"] / cap * 100).round(2)
    return r


# ═════════════════════════════════════════════════════════════
# 3. 트렌드템플릿 8조건 스캔
# ═════════════════════════════════════════════════════════════

def screen(data: dict, market_tag: str, min_rs: int = MIN_RS,
           min_turnover: float = None,
           dev_threshold: float = DEV_THRESHOLD,
           dev_lookback: int = DEV_LOOKBACK) -> pd.DataFrame:
    """
    min_turnover: 20일 평균 거래대금 하한. None이면 시장별 기본 상수를 쓴다.
    dev_threshold, dev_lookback: 이격 지속일수 판정 기준 (deviation_days() 참고).
    """
    close, value = data["close"], data["value"]
    high, low = data.get("high"), data.get("low")

    # 데이터가 부족한 종목(신규상장 등) 제외
    valid = close.notna().sum() >= 252
    close = close.loc[:, valid]
    value = value.loc[:, close.columns]
    close = close.ffill()
    if high is not None:
        high = high.loc[:, close.columns].ffill()
        low = low.loc[:, close.columns].ffill()
    else:
        print(f"[{market_tag}] 주의: 고가·저가가 없어 52주 고점·저점을 종가로 근사합니다.")

    if len(close) < 252:
        raise ValueError(f"데이터 부족: {len(close)}일치만 확보됨 (252일 필요)")

    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    # [2026-09-13] 52주 고점·저점은 실제 장중 고가·저가 기준(원래 정의).
    # market_breadth.py와 같은 방식으로 통일했다 — 없으면 종가로 근사.
    hi_src = high if high is not None else close
    lo_src = low if low is not None else close
    hi52 = hi_src.rolling(252).max()
    lo52 = lo_src.rolling(252).min()

    px = close.iloc[-1]
    m50, m150, m200 = ma50.iloc[-1], ma150.iloc[-1], ma200.iloc[-1]
    m200_prev = ma200.iloc[-1 - MA200_SLOPE_DAYS]
    h52, l52 = hi52.iloc[-1], lo52.iloc[-1]
    rs = rs_rating(close)

    # ── 8개 조건 ──────────────────────────────────
    c1 = (px > m150) & (px > m200)                          # 현재가 > 150일선, 200일선
    c2 = m150 > m200                                        # 150일선 > 200일선
    c3 = m200 > m200_prev                                   # 200일선 1개월 이상 상승
    c4 = (m50 > m150) & (m50 > m200)                        # 50일선 > 150일선, 200일선
    c5 = px > m50                                           # 현재가 > 50일선
    c6 = (px / l52 - 1) * 100 >= ABOVE_LOW_PCT              # 52주 저점 대비 +30% 이상
    c7 = (px / h52 - 1) * 100 >= -NEAR_HIGH_PCT             # 52주 고점 대비 -25% 이내
    c8 = rs >= min_rs                                       # RS Rating 하한

    # 유동성/가격 필터 (트렌드템플릿 외 실전 필터)
    avg_val = value.rolling(20).mean().iloc[-1]
    if market_tag == "KR":
        min_val = MIN_TURNOVER_KR if min_turnover is None else float(min_turnover)
        min_px = MIN_PRICE_KR
    else:
        min_val = MIN_DOLLAR_VOL_US if min_turnover is None else float(min_turnover)
        min_px = MIN_PRICE_US
    liq = (px >= min_px) & (avg_val >= min_val)

    conds = pd.DataFrame({
        "C1_above_150_200": c1, "C2_150_over_200": c2, "C3_200_rising": c3,
        "C4_50_over_150_200": c4, "C5_above_50": c5, "C6_above_low_30": c6,
        "C7_near_high_25": c7, "C8_rs_pass": c8,
    })
    tt_pass = conds.all(axis=1)                  # 8조건만 본 결과
    liq_ok = liq.fillna(False)
    passed = tt_pass & liq_ok

    # 하한을 올리면 몇 종목이 걸러졌는지 매 실행마다 눈에 보이게 남긴다.
    dropped = int((tt_pass & ~liq_ok).sum())
    unit = "원" if market_tag == "KR" else "달러"
    print(f"[{market_tag}] 유동성 필터: 20일 평균 거래대금 {min_val:,.0f}{unit} 이상 "
          f"· 8조건 통과 {int(tt_pass.sum())}종목 중 {dropped}종목 제외 "
          f"→ 최종 {int(passed.sum())}종목")

    result = pd.DataFrame({
        "market": market_tag,
        "price": px.round(2),
        "RS": rs,
        "vs_52w_high_%": ((px / h52 - 1) * 100).round(1),
        "vs_52w_low_%": ((px / l52 - 1) * 100).round(1),
        "vs_MA50_%": ((px / m50 - 1) * 100).round(1),
        "vs_MA200_%": ((px / m200 - 1) * 100).round(1),
        "MA200_slope_%": ((m200 / m200_prev - 1) * 100).round(2),
        "avg_turnover_20d": avg_val.round(0),
        "conditions_met": conds.sum(axis=1),
        "PASS": passed,
    })
    result = pd.concat([result, conds], axis=1)

    # [2026-09-15] 회전율용 원값. 시가총액(market_cap)은 run()에서 나중에
    # 붙으므로, 실제 비율(turnover_ratio) 계산은 add_turnover_ratio()가 담당한다.
    result["trade_value_today"] = value.iloc[-1].round(0)
    result["trade_value_5d_avg"] = value.rolling(5).mean().iloc[-1].round(0)

    # [2026-09-15] 이격 지속일수 — "이번 상승이 재베이스 없이 계속 벌어지는
    # 중인지, 눌림으로 좁혀지는 중인지"를 판별하기 위함.
    dev_stats = deviation_days(close, ma50, threshold=dev_threshold, lookback=dev_lookback)
    result = result.join(dev_stats)

    # [2026-09-13] VCP(변동성 수축 패턴) 분석 — 통과(8)·관찰(7)만 계산한다.
    # 전체 유니버스(수천 종목)에 다 돌리면 느려지고, 애초에 8조건도 다 못
    # 채운 종목은 VCP를 볼 이유가 없다.
    vcp_cols = ["pivot_price", "stop_price", "pct_from_pivot", "risk_pct",
                "contraction_count", "is_tightening", "vol_dryup", "vcp_status",
                "vol_ratio", "vol_ratio_label"]
    for c in vcp_cols:
        result[c] = None
    try:
        import vcp as _vcp
        candidates = result.index[result["conditions_met"] >= 7]
        chart_store = {}
        if len(candidates):
            sub = _vcp.add_vcp_columns(result.loc[candidates].copy(), close, high, low,
                                        value, chart_store=chart_store)
            for c in vcp_cols:
                result.loc[sub.index, c] = sub[c]
        # DataFrame.attrs는 같은 파이썬 프로세스 안에서만 살아있는 메타데이터다
        # (CSV로 저장하면 사라진다). run()이 이걸 모아서 별도 JSON으로 쓴다 —
        # 종목당 130일치 가격을 CSV 컬럼에 욱여넣으면 파일이 무거워지고
        # 다른 도구로 열어보기도 불편해지기 때문이다.
        result.attrs["vcp_charts"] = chart_store
    except Exception as e:
        print(f"[{market_tag}] VCP 분석 건너뜀: {str(e)[:150]}")

    return result.sort_values(["PASS", "RS"], ascending=[False, False])


# ═════════════════════════════════════════════════════════════
# 4. 실행
# ═════════════════════════════════════════════════════════════

def us_market_caps(tickers, sleep_sec: float = 0.25) -> dict:
    """
    종목코드 -> 시가총액(달러). yfinance는 대량 다운로드에 시가총액을 안 주므로
    종목별로 따로 물어봐야 한다. 그래서 이 함수는 호출한 쪽에서 스스로
    범위를 좁혀(예: 통과+관찰 종목만) 넘기는 것을 전제로 한다 — 미국 500종목
    전체에 매번 쓰면 몇 분씩 걸리고 차단 위험도 커진다.
    """
    import yfinance as yf
    out = {}
    for i, t in enumerate(tickers):
        try:
            fi = yf.Ticker(t).fast_info
            cap = fi.get("market_cap") or fi.get("marketCap")
            out[t] = float(cap) if cap else None
        except Exception as e:
            out[t] = None
            if i < 3:
                print(f"  [warn] {t} 시가총액 조회 실패: {str(e)[:60]}")
        time.sleep(sleep_sec)
    return out


def run(market: str, min_rs: int, kr_source: str = "fdr", as_of: str = None,
        min_turnover_kr: float = None, min_turnover_us: float = None,
        dev_threshold: float = DEV_THRESHOLD, dev_lookback: int = DEV_LOOKBACK) -> pd.DataFrame:
    """
    as_of: 'YYYYMMDD'. 주면 그 날짜를 '오늘'인 것처럼 취급해 과거 시점을 스캔한다
    (백필용). 안 주면 실제 오늘 날짜로 스캔한다.

    과거 시점 스캔의 한계 두 가지:
      - 시가총액은 과거 값을 구할 수 없어 항상 '지금 이 순간'의 값이 들어간다.
      - 미국 유니버스는 위키피디아의 '현재' S&P500 구성이라, 그 과거 날짜에
        실제로 지수에 속해 있었는지는 반영되지 않는다(생존편향).
    """
    if as_of:
        today = dt.datetime.strptime(as_of, "%Y%m%d").date()
        print(f"[안내] 과거 시점 소급 스캔: {today} 기준 (시가총액은 현재 값 사용)")
    else:
        today = dt.date.today()
    start = (today - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    results = []

    kr_error = None
    if market in ("KR", "ALL"):
        try:
            if kr_source == "fdr":
                # 기본 경로: FinanceDataReader(네이버).
                # KRX 회원제 전환 이후 pykrx는 로그인이 필요하고 IP 차단 위험이 커서
                # 매일 돌리는 용도에는 적합하지 않다.
                from kr_data_fdr import fetch_kr_fdr, fdr_names, kr_market_caps
                # 날짜별 조회(KRX Open API)가 종목별 조회(FDR)보다 훨씬 빠르다
                # (호출 수: 영업일수×2 vs 종목수 약 2,600). 되면 그걸 쓰고,
                # 안 되면(키 없음, 호출 실패 등) 기존 FDR 방식으로 조용히 돌아간다.
                # 이 폴백 덕분에 고속 경로가 막혀도 스캔 자체는 계속 돈다.
                try:
                    from kr_data_fdr import fetch_kr_krx_open
                    data = fetch_kr_krx_open(start, end)
                    print("[KR] KRX Open API 날짜별 조회로 시세 수집 완료 (고속 경로)")

                    # 정리매매(상장폐지 확정 후 상하한가 해제 구간) 의심 종목은
                    # 가격 자체가 붕괴 중이라 신뢰할 수 없다. 8조건 스캔에서
                    # 자연스레 탈락하긴 하지만, RS 산출(유니버스 내 백분위) 등
                    # 다른 종목 계산에도 영향을 주므로 여기서 미리 제외한다.
                    suspects = [t for t in data.get("delisting_suspects", [])
                                if t in data["close"].columns]
                    if suspects:
                        print(f"[KR] 정리매매 의심 {len(suspects)}종목 유니버스에서 제외: "
                              f"{', '.join(suspects)}")
                        data["close"] = data["close"].drop(columns=suspects)
                        data["value"] = data["value"].drop(columns=suspects)
                        data["meta"] = data["meta"].drop(index=suspects, errors="ignore")
                except Exception as e:
                    print(f"[KR] KRX Open API 고속 경로 실패({str(e)[:150]}) "
                          f"→ 기존 FDR 방식(종목별 조회)으로 폴백합니다.")
                    data = fetch_kr_fdr(start, end)
                r = screen(data, "KR", min_rs, min_turnover_kr,
                           dev_threshold=dev_threshold, dev_lookback=dev_lookback)
                # [2026-09-13] 예전엔 PASS(8조건 통과) 종목만 이름을 조회해서
                # 관찰·전체 탭의 종목명이 NaN으로 떴다. fdr_names()는
                # kr_listing() 캐시 하나로 이름을 붙이는 로컬 조회라 종목
                # 수를 늘려도 네트워크 호출이 늘지 않는다 — 전체로 넓혀도 비용 없음.
                r.insert(0, "name", pd.Series(fdr_names(r.index)))
                # 시가총액: 상장목록을 다시 부를 필요 없이 캐시에서 바로 붙인다 (추가 호출 없음)
                r["market_cap"] = pd.Series(kr_market_caps(r.index))
                r = add_turnover_ratio(r)   # market_cap이 방금 붙었으니 여기서 회전율 계산
            else:
                data = fetch_kr(start, end)
                r = screen(data, "KR", min_rs, min_turnover_kr,
                           dev_threshold=dev_threshold, dev_lookback=dev_lookback)
                r.insert(0, "name", pd.Series(kr_names(r.index)))
                r["market_cap"] = None
                # market_cap이 없어 회전율 계산 불가 — 컬럼 자체는 만들어 None으로 채운다
                r["turnover_ratio"] = None
                r["turnover_ratio_5d"] = None

            # [2026-09-16] 스팩(기업인수목적회사)은 실제 사업이 없는 페이퍼컴퍼니라
            # SEPA/VCP가 전제하는 "추세를 만드는 실제 매출·이익 성장"이 애초에
            # 없다. 한국 스팩은 규정상 사명에 반드시 "스팩"이 들어가므로
            # (예: "삼성스팩13호", "미래에셋비전스팩10호") 이름으로 안전하게
            # 걸러낼 수 있다. 실사례: 473000·473950이 진입가능으로 잘못 잡혔다.
            spac_mask = r["name"].astype(str).str.contains("스팩", na=False)
            if spac_mask.any():
                spac_list = r.loc[spac_mask, "name"].tolist()
                print(f"[KR] 스팩 {spac_mask.sum()}종목 유니버스에서 제외: "
                      f"{', '.join(spac_list)}")
                r = r.loc[~spac_mask]

            # [2026-09-15] 업종 분류 — 가격 수집 경로(고속/폴백)와 무관하게
            # 한 번만 붙인다. 실패해도(pykrx 없음, 네트워크 문제 등) 전체
            # 스캔이 죽지 않도록 별도로 감싼다 — 이건 부가 정보다.
            try:
                from sector_data import fetch_kr_sector_map
                sector_map = fetch_kr_sector_map()
                r["sector"] = r.index.map(sector_map.get) if sector_map else None
            except Exception as e:
                print(f"[업종분류] 건너뜀(표는 정상 생성됨): {str(e)[:150]}")
                r["sector"] = None

            results.append(r)
        except Exception as e:
            # 한국 쪽이 KRX 차단 등으로 실패해도 미국 스캔·리포트는 살려야 한다.
            # market="ALL" 인데 여기서 그냥 죽으면 미국 결과까지 통째로 날아간다.
            kr_error = e
            print(f"\n[경고] 한국 시장 스캔 실패 — 이 시장은 건너뛰고 계속 진행합니다.")
            print(f"  원인: {str(e)[:200]}")
            if market == "KR":
                raise   # 한국만 요청했는데 실패했으면 그건 진짜로 알려야 한다

    us_error = None
    if market in ("US", "ALL"):
        try:
            tickers = us_universe()
            data = fetch_us(tickers, start=start, end=end)
            r = screen(data, "US", min_rs, min_turnover_us,
                       dev_threshold=dev_threshold, dev_lookback=dev_lookback)
            r.insert(0, "name", pd.Series(us_names(r.index)))
            # 시가총액: 종목별 호출이 필요해 통과+관찰 종목으로만 범위를 좁힌다.
            # (전체 500종목에 매번 걸면 몇 분씩 걸리고 차단 위험도 커진다)
            focus = r.index[(r["PASS"]) | (r["conditions_met"] >= 7)]
            print(f"[US] 시가총액 조회: {len(focus)}종목 (통과+관찰 범위로 축소)")
            caps = us_market_caps(list(focus))
            r["market_cap"] = pd.Series({**{t: None for t in r.index}, **caps})
            # market_cap이 없는(시총 조회 범위 밖) 종목은 NaN÷NaN이 되어
            # 자동으로 회전율도 None이 된다 — 별도 예외처리 불필요.
            r = add_turnover_ratio(r)
            results.append(r)
        except Exception as e:
            us_error = e
            print(f"\n[경고] 미국 시장 스캔 실패 — 이 시장은 건너뛰고 계속 진행합니다.")
            print(f"  원인: {str(e)[:200]}")
            if market == "US":
                raise

    if not results:
        raise RuntimeError(
            "모든 시장 스캔이 실패했습니다. 저장할 데이터가 없습니다.\n"
            f"  한국: {kr_error}\n  미국: {us_error}"
        )
    if kr_error is not None:
        print(f"\n※ 이번 실행은 한국 데이터 없이 미국만 저장됩니다 (한국 스캔 실패).")
    if us_error is not None:
        print(f"\n※ 이번 실행은 미국 데이터 없이 한국만 저장됩니다 (미국 스캔 실패).")



    out = pd.concat(results)
    csv_path = os.path.join(OUT_DIR, f"sepa_scan_{end}.csv")
    out.to_csv(csv_path, encoding="utf-8-sig")

    # VCP 미니 차트용 데이터 — CSV와는 별도 파일로 저장한다(종목당 130일치
    # 가격이 들어가 CSV에 같이 실으면 무거워지고 다른 도구로 보기도 불편해짐).
    # 파일명 규칙을 sepa_scan_{stamp}.csv 와 맞춰서, 나중에 make_dashboard.py가
    # csv_path만 보고 같은 폴더의 짝 파일을 자동으로 찾을 수 있게 한다.
    charts = {}
    for r in results:
        try:
            charts.update(r.attrs.get("vcp_charts", {}))
        except Exception:
            pass
    chart_path = os.path.join(OUT_DIR, f"sepa_vcp_charts_{end}.json")
    try:
        with open(chart_path, "w", encoding="utf-8") as f:
            json.dump(charts, f, ensure_ascii=False)
        print(f"[VCP] 미니 차트 데이터 {len(charts)}종목 저장: {os.path.basename(chart_path)}")
    except Exception as e:
        print(f"[VCP] 미니 차트 데이터 저장 실패(화면에서 차트는 안 뜨지만 표는 정상): {e}")

    passed = out[out["PASS"]]
    print(f"\n{'='*60}")
    print(f"스캔 완료: 전체 {len(out)}종목 중 {len(passed)}종목 통과 (RS>={min_rs})")
    print(f"저장: {csv_path}")
    print(f"{'='*60}")
    if not passed.empty:
        print(passed[["name", "market", "price", "RS", "vs_52w_high_%"]].head(30).to_string())
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="ALL", choices=["KR", "US", "ALL"])
    ap.add_argument("--min-rs", type=int, default=MIN_RS)
    ap.add_argument("--kr-source", default="fdr", choices=["fdr", "pykrx"],
                    help="한국 데이터 소스 (기본 fdr: 네이버, KRX 로그인 불필요)")
    ap.add_argument("--date", default=None,
                    help="YYYYMMDD. 과거 특정 날짜를 '오늘'처럼 소급 스캔한다(백필용). "
                        "생략하면 실제 오늘 날짜.")
    ap.add_argument("--min-turnover", type=float, default=None, metavar="억원",
                    help=f"한국 종목 20일 평균 거래대금 하한(억원). "
                         f"생략하면 기본 {MIN_TURNOVER_KR/1e8:.0f}억원. 예: --min-turnover 100")
    ap.add_argument("--min-dollar-vol", type=float, default=None, metavar="백만달러",
                    help=f"미국 종목 20일 평균 거래대금 하한(백만 달러). "
                         f"생략하면 기본 {MIN_DOLLAR_VOL_US/1e6:.0f}백만 달러.")
    ap.add_argument("--dev-threshold", type=float, default=DEV_THRESHOLD, metavar="%",
                    help=f"이격 지속일수 판정 임계값(%%). 생략하면 기본 {DEV_THRESHOLD}")
    ap.add_argument("--dev-lookback", type=int, default=DEV_LOOKBACK, metavar="거래일",
                    help=f"이격 지속일수 계산 구간(거래일). 생략하면 기본 {DEV_LOOKBACK}")
    a = ap.parse_args()

    # 입력 단위(억원 / 백만달러)를 내부 단위(원 / 달러)로 환산
    kr_min = None if a.min_turnover is None else a.min_turnover * 1e8
    us_min = None if a.min_dollar_vol is None else a.min_dollar_vol * 1e6

    run(a.market, a.min_rs, a.kr_source, as_of=a.date,
        min_turnover_kr=kr_min, min_turnover_us=us_min,
        dev_threshold=a.dev_threshold, dev_lookback=a.dev_lookback)
