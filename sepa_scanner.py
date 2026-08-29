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
MIN_TURNOVER_KR = 1_000_000_000   # 20일 평균 거래대금 10억원 이상
MIN_DOLLAR_VOL_US = 10_000_000    # 20일 평균 거래대금 $10M 이상


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


def fetch_us(tickers: list, period: str = "2y") -> dict:
    """yfinance 배치 다운로드. 100개씩 끊어서 요청."""
    import yfinance as yf

    closes, volumes = [], []
    for i in range(0, len(tickers), 100):
        batch = tickers[i:i + 100]
        print(f"[US] {i+1}~{i+len(batch)} / {len(tickers)} 다운로드...")
        df = yf.download(batch, period=period, auto_adjust=True,
                         progress=False, group_by="column", threads=True)
        if df is None or df.empty:
            continue
        closes.append(df["Close"])
        volumes.append(df["Volume"])
        time.sleep(1.0)

    close = pd.concat(closes, axis=1)
    volume = pd.concat(volumes, axis=1)
    value = close * volume          # 거래대금(달러)
    meta = pd.DataFrame({"market": "US"}, index=close.columns)
    return {"close": close.sort_index(), "value": value.sort_index(), "meta": meta}


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
# 3. 트렌드템플릿 8조건 스캔
# ═════════════════════════════════════════════════════════════

def screen(data: dict, market_tag: str, min_rs: int = MIN_RS) -> pd.DataFrame:
    close, value = data["close"], data["value"]

    # 데이터가 부족한 종목(신규상장 등) 제외
    valid = close.notna().sum() >= 252
    close = close.loc[:, valid]
    value = value.loc[:, close.columns]
    close = close.ffill()

    if len(close) < 252:
        raise ValueError(f"데이터 부족: {len(close)}일치만 확보됨 (252일 필요)")

    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    hi52 = close.rolling(252).max()
    lo52 = close.rolling(252).min()

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
        liq = (px >= MIN_PRICE_KR) & (avg_val >= MIN_TURNOVER_KR)
    else:
        liq = (px >= MIN_PRICE_US) & (avg_val >= MIN_DOLLAR_VOL_US)

    conds = pd.DataFrame({
        "C1_above_150_200": c1, "C2_150_over_200": c2, "C3_200_rising": c3,
        "C4_50_over_150_200": c4, "C5_above_50": c5, "C6_above_low_30": c6,
        "C7_near_high_25": c7, "C8_rs_pass": c8,
    })
    passed = conds.all(axis=1) & liq.fillna(False)

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


def run(market: str, min_rs: int, kr_source: str = "fdr") -> pd.DataFrame:
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
                data = fetch_kr_fdr(start, end)
                r = screen(data, "KR", min_rs)
                r.insert(0, "name", pd.Series(fdr_names(r.index[r["PASS"]])))
                # 시가총액: 상장목록을 다시 부를 필요 없이 캐시에서 바로 붙인다 (추가 호출 없음)
                r["market_cap"] = pd.Series(kr_market_caps(r.index))
            else:
                data = fetch_kr(start, end)
                r = screen(data, "KR", min_rs)
                r.insert(0, "name", pd.Series(kr_names(r.index[r["PASS"]])))
                r["market_cap"] = None
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
            data = fetch_us(tickers)
            r = screen(data, "US", min_rs)
            r.insert(0, "name", pd.Series(us_names(r.index)))
            # 시가총액: 종목별 호출이 필요해 통과+관찰 종목으로만 범위를 좁힌다.
            # (전체 500종목에 매번 걸면 몇 분씩 걸리고 차단 위험도 커진다)
            focus = r.index[(r["PASS"]) | (r["conditions_met"] >= 7)]
            print(f"[US] 시가총액 조회: {len(focus)}종목 (통과+관찰 범위로 축소)")
            caps = us_market_caps(list(focus))
            r["market_cap"] = pd.Series({**{t: None for t in r.index}, **caps})
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
    a = ap.parse_args()
    run(a.market, a.min_rs, a.kr_source)
