# -*- coding: utf-8 -*-
"""
sector_data.py — 한국 종목의 업종 분류(WICS 기반) 매핑을 만든다.

핵심 아이디어: 종목 하나하나를 업종별로 조회하는 대신(2,500번+ 요청),
KRX가 이미 운영 중인 "업종별 지수"(코스피/코스닥 업종지수)의 구성종목을
가져온다. 업종 지수는 20~30개뿐이라 요청 수가 훨씬 적고, 크롤링이
아니라 pykrx가 감싼 KRX 정식 API 경로를 쓴다.

[2026-09-15] 설계 근거 — WICS/GICS 공식 분류(A안)를 우선하고, 향후
가격 상관관계 기반 자동 클러스터링(B안)을 보조 지표로 얹기로 함
(테마 강도 기능 논의에서 결정).

업종 지수와 시장 전체/스타일 지수(코스피200, 대형주, ESG 등)가 섞여
나오므로, 이름에 특정 키워드가 들어간 것만 "업종 지수"로 인정한다.
하드코딩된 지수 코드에 의존하지 않는 이유: KRX가 지수를 개편하면
코드가 바뀔 수 있는데, 이름 기반 필터는 그 변화에 더 안전하다.
"""
import os
import json
import time
import datetime as dt

# ── .env 자동 로딩 ────────────────────────────────────────────
# [2026-09-15] run_daily.py를 거치지 않고 이 파일을 단독 실행할 때
# (python3 sector_data.py) .env가 자동으로 안 읽혀서 KRX_ID/KRX_PW가
# 이미 .env에 있는데도 "환경변수 없음"으로 실패하는 문제가 있었다.
# sepa_scanner.py와 완전히 같은 방식으로 맞춘다 — python-dotenv가
# 없으면(설치 안 됐을 수 있음) 직접 파싱하는 방어선까지 포함해야
# 확실히 읽힌다. GitHub Actions는 .env 파일 자체가 없고 Secrets로
# 직접 주입하므로, 이 파일이 없어도 조용히 넘어간다 — 필수 의존성 아님.
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

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cache", "kr_sector_map.json")
CACHE_MAX_AGE_DAYS = 30  # 업종 분류는 자주 안 바뀌므로 한 달에 한 번 정도만 새로 받는다

# 이 키워드가 지수 이름에 들어 있으면 "시장 전체/스타일 지수"로 보고
# 업종 지수 후보에서 제외한다. 실제 업종명(반도체, 자동차 등)에는
# 안 나올 만한 단어들로 구성했다.
_EXCLUDE_KEYWORDS = [
    "코스피", "코스닥", "코스피지수",  # 시장 지수 자체
    "200", "100", "50", "150",         # 대표지수(코스피200 등)
    "대형주", "중형주", "소형주", "중소형",
    "우량", "프리미어", "ESG", "배당", "가치", "성장", "모멘텀",
    "지배구조", "여성", "가족친화", "밸류업",
]


def _is_sector_index_name(name: str) -> bool:
    if not name:
        return False
    return not any(kw in name for kw in _EXCLUDE_KEYWORDS)


def fetch_kr_sector_map(force: bool = False) -> dict:
    """
    {티커: 업종명} 딕셔너리를 반환한다. 캐시가 30일 이내면 그걸 쓰고,
    없거나 오래됐으면 pykrx로 새로 받는다. pykrx가 없거나 네트워크
    문제로 실패하면 빈 딕셔너리를 반환한다 — 이 기능은 부가 정보라,
    실패해도 스캐너 본체가 죽으면 안 된다.
    """
    if not force and os.path.exists(CACHE_PATH):
        age_days = (time.time() - os.path.getmtime(CACHE_PATH)) / 86400
        if age_days < CACHE_MAX_AGE_DAYS:
            try:
                with open(CACHE_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[업종분류] 캐시 사용 ({age_days:.0f}일 전 수집, {len(data)}종목)")
                return data
            except Exception as e:
                print(f"[업종분류] 캐시 읽기 실패, 새로 받습니다: {e}")

    try:
        from pykrx import stock as pkstock
    except ImportError:
        print("[업종분류] pykrx가 설치되어 있지 않아 업종 분류를 건너뜁니다.")
        return {}

    today = dt.date.today().strftime("%Y%m%d")
    total_indices = 0
    # [2026-09-15] 1단계에서는 매핑을 바로 안 만들고, (지수명, 구성종목)
    # 후보만 전부 모아둔다. 2단계에서 "종목 수가 적은(=더 세밀한) 지수부터"
    # 우선 배정해야, "제조"처럼 크고 뭉뚱그린 지수가 세밀한 지수(반도체 등)
    # 보다 먼저 종목을 채가는 문제를 막을 수 있다. 실사례: 첫 버전에서는
    # 처리 순서가 우연에 맡겨져 있어 전체의 43%가 "제조" 하나로 뭉쳐 나왔다.
    candidates = []  # [(idx_name, [ticker, ...]), ...]

    for market in ("KOSPI", "KOSDAQ"):
        try:
            index_tickers = pkstock.get_index_ticker_list(today, market=market)
        except Exception as e:
            print(f"[업종분류] {market} 지수 목록 조회 실패: {str(e)[:150]}")
            continue

        for idx_ticker in index_tickers:
            total_indices += 1
            try:
                idx_name = pkstock.get_index_ticker_name(idx_ticker)
            except Exception:
                continue
            if not _is_sector_index_name(idx_name):
                continue
            try:
                members = pkstock.get_index_portfolio_deposit_file(idx_ticker)
            except Exception as e:
                print(f"[업종분류] '{idx_name}'({idx_ticker}) 구성종목 조회 실패: {str(e)[:100]}")
                continue
            if not members:
                continue
            candidates.append((idx_name, members))
            time.sleep(0.2)  # KRX 요청 과다 방지 (기존 코드 관행과 동일)

    # 2단계: 종목 수 오름차순(세밀한 지수 먼저) 정렬 후 배정.
    # 같은 종목이 여러 지수에 걸리면, 먼저 배정된(=더 세밀한) 쪽을 유지한다.
    candidates.sort(key=lambda c: len(c[1]))
    mapping = {}
    used_indices = 0
    for idx_name, members in candidates:
        claimed = 0
        for ticker in members:
            if ticker not in mapping:
                mapping[ticker] = idx_name
                claimed += 1
        if claimed:
            used_indices += 1

    print(f"[업종분류] 지수 {total_indices}개 중 업종 지수 {used_indices}개 사용 · "
          f"{len(mapping)}종목 매핑 완료")

    if mapping:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)
        except Exception as e:
            print(f"[업종분류] 캐시 저장 실패(이번 실행 결과는 정상 사용됨): {e}")

    return mapping


if __name__ == "__main__":
    m = fetch_kr_sector_map(force=True)
    # 업종별 종목 수 요약 출력 — 매핑이 그럴듯한지 눈으로 확인하기 위함
    from collections import Counter
    counts = Counter(m.values())
    for name, n in counts.most_common(30):
        print(f"  {name}: {n}종목")
