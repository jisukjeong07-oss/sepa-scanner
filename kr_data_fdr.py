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


def _listing_from_krx_open_unofficial() -> pd.DataFrame:
    """
    KRX 정보데이터시스템(data.krx.co.kr) 웹페이지가 내부적으로 쓰는
    비공식 엔드포인트. 공식 Open API(_listing_from_krx_open_api)가 없거나
    막혔을 때의 최후 폴백. KRX 개편에 따라 예고 없이 바뀔 수 있다.
    """
    import json
    import urllib.request
    import urllib.parse

    url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    frames = []
    for mkt, mkt_id in (("KOSPI", "STK"), ("KOSDAQ", "KSQ")):
        payload = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT01901",
            "mktId": mkt_id,
            "share": "1",
            "csvxls_isNo": "false",
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode("utf-8", errors="replace"))

        rows = body.get("OutBlock_1") or body.get("output") or []
        if not rows:
            raise RuntimeError(f"KRX 비공식 엔드포인트에서 {mkt} 데이터를 받지 못했습니다.")

        code_key = next((k for k in ("ISU_SRT_CD", "short_code", "ISU_CD") if k in rows[0]), None)
        name_key = next((k for k in ("ISU_ABBRV", "isu_abbrv", "ISU_NM") if k in rows[0]), None)
        if not code_key or not name_key:
            raise RuntimeError(f"응답 형식이 예상과 다릅니다: {list(rows[0].keys())[:10]}")

        out = pd.DataFrame({
            "code": [str(r.get(code_key, "")).zfill(6) for r in rows],
            "name": [str(r.get(name_key, "")) for r in rows],
            "market": mkt,
            "market_cap": pd.NA,
        })
        out = out[out["code"].str.match(r"^\d{6}$")]
        out = out[~out["name"].str.contains("스팩|제[0-9]+호", na=False)]
        frames.append(out)
        print(f"[KRX 비공식] {mkt} {len(out):,}종목 (시가총액은 미확보)")

    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("code")
              .set_index("code"))


def _listing_from_pykrx() -> pd.DataFrame:
    """
    pykrx 폴백. FDR의 KrxMarcapListingCache와 달리 KRX를 직접 호출하므로
    그 캐시 파일과 무관하게 동작한다.

    [2026-09-09 수정] 시가총액 API(get_market_cap_by_ticker)는 KRX 회원제
    전환 이후 로그인(KRX_ID/KRX_PW)이 없으면 빈 응답을 준다. 이 계정을
    운영 환경(GitHub Actions)에 두지 않기로 했으므로, 여기서는 로그인이
    필요 없는 get_market_ticker_list() + get_market_ticker_name()만 쓴다.
    시가총액은 이 폴백에서는 얻지 못해 None으로 둔다 — 8조건 스캔에는
    쓰이지 않는 표시용 값이라 스캔 자체에는 영향이 없다.
    """
    from pykrx import stock

    today = pd.Timestamp.now(tz="Asia/Seoul")
    frames = []
    for mkt in ("KOSPI", "KOSDAQ"):
        codes = None
        # 휴장일에 걸리면 빈 목록이 오므로 최근 5일 중 값이 있는 날짜를 찾는다.
        for back in range(5):
            d = (today - pd.Timedelta(days=back)).strftime("%Y%m%d")
            try:
                cand = stock.get_market_ticker_list(d, market=mkt)
            except Exception:
                cand = None
            if cand:
                codes = cand
                break
        if not codes:
            raise RuntimeError(f"pykrx {mkt} 종목 목록 조회가 계속 비어 있습니다.")

        names = {}
        for code in codes:
            try:
                names[code] = stock.get_market_ticker_name(code)
            except Exception:
                names[code] = code

        out = pd.DataFrame({
            "code": [str(c).zfill(6) for c in codes],
            "market": mkt,
            "name": [names[c] for c in codes],
            "market_cap": pd.NA,   # 로그인 없이는 조회 불가 — kr_market_caps()가 None으로 처리
        })
        out = out[out["code"].str.match(r"^\d{6}$")]
        out = out[~out["name"].str.contains("스팩|제[0-9]+호", na=False)]
        frames.append(out)
        print(f"[pykrx] {mkt} {len(out):,}종목 (FDR 목록 폴백, 시가총액은 미확보)")

    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("code")
              .set_index("code"))


def _krx_open_api_call(api_id: str, bas_dd: str) -> list:
    """
    KRX Open API 공통 호출부.
    2026-09-10 발급받은 개발 명세서 + 샘플 예제 기준:
      - endpoint: https://data-dbg.krx.co.kr/svc/apis/sto/{api_id}
      - method:   GET, 쿼리 파라미터 ?basDd=YYYYMMDD
                  (Spec.docx의 "request" 섹션 {"basDd":"__"}는 파라미터
                   설명이었을 뿐 POST body 형식이 아니었다 — 샘플 예제의
                   실제 HTTP Request가 정답: GET .../stk_bydd_trd?basDd=... )
      - 응답:      {"OutBlock_1": [...]}
      - 인증:      AUTH_KEY 헤더 (샘플 예제에서 확인됨)
    KRX_API_KEY 환경변수가 없으면 애초에 호출하지 않고 바로 예외를 던져,
    다음 폴백(FDR 등)으로 자연스럽게 넘어가게 한다.
    """
    import json
    import urllib.request
    import urllib.parse

    key = os.environ.get("KRX_API_KEY")
    if not key:
        raise RuntimeError("KRX_API_KEY 환경변수가 설정되지 않았습니다.")

    url = f"https://data-dbg.krx.co.kr/svc/apis/sto/{api_id}?" + urllib.parse.urlencode({"basDd": bas_dd})
    req = urllib.request.Request(url, method="GET")
    # urllib은 기본적으로 헤더 이름을 Title-Case로 정규화한다(AUTH_KEY -> Auth_key).
    # HTTP 헤더명은 대소문자를 구분하지 않는 게 표준이지만, 서버가 엄격히
    # 볼 가능성에 대비해 add_unredirected_header로 원래 표기를 그대로 유지한다.
    req.add_unredirected_header("AUTH_KEY", key)
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))

    rows = payload.get("OutBlock_1")
    if rows is None:
        raise RuntimeError(f"KRX Open API 응답에 OutBlock_1이 없습니다: {list(payload.keys())[:5]}")
    return rows


def _krx_open_api_recent_bas_dd(api_id: str, max_back: int = 7) -> tuple:
    """
    최근 영업일을 모른 채로 바로 조회를 시도한다. 휴장일이면 OutBlock_1이
    빈 배열로 오므로, 값이 나올 때까지 하루씩 거슬러 올라간다.
    반환: (기준일 'YYYYMMDD', 그날의 rows)
    """
    d = pd.Timestamp.now(tz="Asia/Seoul").normalize()
    for _ in range(max_back):
        bas_dd = d.strftime("%Y%m%d")
        rows = _krx_open_api_call(api_id, bas_dd)
        if rows:
            return bas_dd, rows
        d -= pd.Timedelta(days=1)
    raise RuntimeError(f"{api_id}: 최근 {max_back}일 모두 빈 응답이었습니다.")


def _listing_from_krx_open_api() -> pd.DataFrame:
    """
    KRX Open API 공식 목록 조회.
    stk_bydd_trd(유가증권 일별매매정보) / ksq_bydd_trd(코스닥 일별매매정보)는
    종목 목록이 아니라 '그날의 매매정보'를 주는 API지만, 응답에 상장된
    모든 종목이 포함되어 있어 그대로 목록으로도 쓸 수 있다. 게다가 종가·
    시가총액까지 함께 오므로, 이 경로가 정상 작동하면 목록 문제뿐 아니라
    fetch_kr_fdr()가 하던 종목별 개별 시세 조회(약 2,600회 호출)도
    이 한 번의 호출로 상당 부분 대체할 수 있다(추후 개선 여지로 남겨둔다).

    공식 API이므로 이게 성공하면 신뢰도가 가장 높은 소스다.

    [2026-09-10] KOSPI(stk_bydd_trd)와 KOSDAQ(ksq_bydd_trd)는 KRX Open API
    포털에서 개별 승인 단위라, 한쪽만 신청/승인됐으면 다른 쪽은 401을
    반환한다. 실제로 KOSPI만 승인된 계정에서 KOSDAQ 호출이 401로 막히는
    사례가 있었다. 시장 단위로 개별 try/except를 둬서, 한쪽만 성공해도
    그 시장만큼은 결과에 반영한다 — 이전에는 하나가 실패하면 이미 받은
    916종목(KOSPI)까지 통째로 버려지고 있었다.
    """
    frames = []
    errors = []
    for mkt_label, api_id in (("KOSPI", "stk_bydd_trd"), ("KOSDAQ", "ksq_bydd_trd")):
        try:
            bas_dd, rows = _krx_open_api_recent_bas_dd(api_id)
        except Exception as e:
            errors.append(f"{mkt_label}: {str(e)[:100]}")
            print(f"[KRX Open API] {mkt_label} 조회 실패({str(e)[:100]}) "
                  f"— 이 시장만 건너뛰고 계속 진행합니다.")
            continue

        out = pd.DataFrame({
            "code": [str(r.get("ISU_CD", "")).strip().zfill(6) for r in rows],
            "name": [str(r.get("ISU_NM", "")).strip() for r in rows],
            "market": mkt_label,
            "market_cap": [
                pd.to_numeric(str(r.get("MKTCAP", "")).replace(",", ""), errors="coerce")
                for r in rows
            ],
        })
        out = out[out["code"].str.match(r"^\d{6}$")]
        out = out[~out["name"].str.contains("스팩|제[0-9]+호", na=False)]
        frames.append(out)
        print(f"[KRX Open API] {mkt_label} {len(out):,}종목 (기준일 {bas_dd}, 시가총액 포함)")

    if not frames:
        raise RuntimeError("KRX Open API: 모든 시장 조회 실패 — " + " / ".join(errors))
    if errors:
        # 일부 시장만 성공한 경우, 그 사실을 결과에 묻어두지 않고 경고로 남긴다.
        print(f"[KRX Open API] 일부 시장 누락된 채로 진행합니다 ({' / '.join(errors)}). "
              f"승인 상태를 KRX Open API 포털에서 확인하세요.")

    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("code")
              .set_index("code"))


def kr_listing() -> pd.DataFrame:
    """
    KOSPI/KOSDAQ 상장 종목 목록.
    반환: index=종목코드, columns=[name, market, market_cap]

    순서: 최신 캐시(7일 이내) → KRX Open API(공식, 키 있을 때만) → FDR
          → pykrx → KRX 비공식 엔드포인트 → 오래된 캐시.
    2026-09-09에 FDR·pykrx·비공식 엔드포인트가 같은 날 동시에 막힌 사고가
    있었고, 그 직후 KRX Open API 정식 키를 발급받아 최우선 순위로 추가했다.
    KRX_API_KEY가 없으면 이 단계는 즉시 건너뛰어 기존 흐름과 동일하게 동작한다.

    [2026-09-10] KRX Open API에서 KOSPI/KOSDAQ가 개별 승인 단위임이
    확인되어, 한 소스가 두 시장 중 하나만 반환하는 경우가 생긴다.
    그래서 소스를 하나 고르고 끝내는 게 아니라, 아직 못 채운 시장이
    남아 있으면 다음 소스로 그 시장만 보완한다.

    상장 종목은 하루에 몇 개 바뀌는 수준이라, 마지막 단계(오래된 캐시)까지
    가더라도 스캔 자체가 무의미해지지는 않는다.
    """
    cache = os.path.join(CACHE_DIR, "kr_listing")

    age = _cache_age_days(cache)
    if age is not None and age < LISTING_MAX_AGE_DAYS:
        return _read(cache)

    need = {"KOSPI", "KOSDAQ"}
    have = {}   # market -> DataFrame

    for label, fn in (
        ("KRX Open API", _listing_from_krx_open_api),
        ("FDR", _listing_from_fdr),
        ("pykrx", _listing_from_pykrx),
        ("KRX 비공식 엔드포인트", _listing_from_krx_open_unofficial),
    ):
        if not need:
            break
        try:
            listing = fn()
        except Exception as e:
            print(f"[{label}] 종목 목록 조회 실패({str(e)[:120]}) → 다음 소스로 폴백합니다.")
            continue

        got = set(listing["market"].unique()) & need
        if not got:
            print(f"[{label}] 필요한 시장({', '.join(sorted(need))})을 채우지 못했습니다.")
            continue

        for mkt in got:
            have[mkt] = listing[listing["market"] == mkt]
        need -= got
        if got != {"KOSPI", "KOSDAQ"}:
            print(f"[{label}] {', '.join(sorted(got))}만 확보. "
                  f"남은 시장({', '.join(sorted(need)) if need else '없음'})은 다음 소스에서 시도합니다.")

    if have:
        listing = pd.concat(have.values())
        if need:
            print(f"[경고] {', '.join(sorted(need))}는 어떤 소스에서도 얻지 못해 "
                  f"이번 스캔에서 제외됩니다.")
        _write(listing, cache)
        return listing

    # 최후 수단: 기간이 지났어도 저장된 캐시가 있으면 그걸로 계속 간다.
    if _exists(cache):
        stale = age if age is not None else -1
        print(f"[경고] 세 소스 모두 실패해 {stale:.1f}일 전 캐시를 재사용합니다. "
              f"신규 상장·상장폐지가 반영되지 않았을 수 있습니다.")
        return _read(cache)

    raise RuntimeError(
        "한국 종목 목록을 FDR·pykrx·KRX 오픈 조회 세 곳 모두에서 가져오지 못했고, "
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
