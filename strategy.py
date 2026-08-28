# -*- coding: utf-8 -*-
"""
SEPA 매매전략 생성기

중요한 한계부터 밝힌다: 이 모듈은 그날의 스캔 데이터(통과종목 수, RS 분포,
관찰종목 등)를 SEPA 규칙에 대입해 자동으로 서술을 만든다. 실시간 뉴스나
매크로 지표를 그 순간에 가져와 요약하는 것이 아니다. 정적 HTML 대시보드
구조상 완전한 라이브 요약은 불가능하며, 이 점을 대시보드 화면에도 명시한다.
"""

import os
import json
import datetime as dt

import pandas as pd

import market_calendar as mc

KST = dt.timezone(dt.timedelta(hours=9))
REGISTRY_FILE = "rs90_registry.json"


# ═════════════════════════════════════════════════════════════
# RS90 진입일 레지스트리
# ═════════════════════════════════════════════════════════════

def load_registry(hist_dir: str) -> dict:
    path = os.path.join(hist_dir, REGISTRY_FILE)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_registry(hist_dir: str, registry: dict):
    os.makedirs(hist_dir, exist_ok=True)
    with open(os.path.join(hist_dir, REGISTRY_FILE), "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=0)


def update_registry(registry: dict, df: pd.DataFrame, date_str: str) -> dict:
    """
    RS>=90을 처음 충족한 날짜를 기록한다. 도중에 90 밑으로 떨어지면 그 종목의
    기록은 지운다 — 다음에 다시 90을 넘으면 그날이 새 진입일이 된다.

    df는 호출 전에 '오늘 실제로 개장한 시장'만 남기고 걸러져 있어야 한다
    (휴장일이면 그 시장 행 자체가 없음). 정리(prune)는 오늘 데이터가 있는
    시장에 한해서만 수행한다 — 그렇지 않으면 휴장일에 다른 시장 하나만 있어도
    휴장한 시장의 레지스트리가 전부 삭제되는 문제가 생긴다.
    """
    markets_today = set(df.get("market", pd.Series(dtype=str)).unique())
    seen_today = set()

    for ticker, row in df.iterrows():
        rs = row.get("RS")
        if pd.isna(rs):
            continue
        key = f"{row.get('market', '')}:{ticker}"
        seen_today.add(key)
        if rs >= 90:
            if key not in registry:
                registry[key] = date_str
        else:
            registry.pop(key, None)

    # 오늘 데이터가 있었던 시장에 한해서만, 스캔에 안 나타난(상장폐지 등) 종목을 정리한다.
    for key in list(registry):
        mkt = key.split(":", 1)[0]
        if mkt in markets_today and key not in seen_today:
            registry.pop(key, None)

    return registry


def elapsed_for(registry: dict, market: str, ticker: str, date_str: str):
    key = f"{market}:{ticker}"
    first = registry.get(key)
    if not first:
        return None, None
    n = mc.trading_days_between(first, date_str, market)
    return mc.format_elapsed(n), first


# ═════════════════════════════════════════════════════════════
# 시장 국면(breadth) 판단 — 정량 지표 기반 단순 휴리스틱
# ═════════════════════════════════════════════════════════════

def market_regime(df: pd.DataFrame, market: str) -> dict:
    """
    스캔 모집단 대비 통과 비율(breadth)로 국면을 추정한다.
    미네르비니의 정식 시장 국면 판단(지수 자체의 트렌드템플릿 충족 여부)과는
    다른, 스캔 결과 기반의 보조 지표임을 명시한다.
    """
    sub = df[df["market"] == market]
    if sub.empty:
        return {"label": "데이터 없음", "breadth": None, "scanned": 0, "passed": 0}

    scanned = len(sub)
    passed = int(sub["PASS"].sum())
    breadth = passed / scanned if scanned else 0

    if breadth >= 0.03:
        label = "CONFIRMED UPTREND (상승추세 확산)"
    elif breadth >= 0.01:
        label = "UNDER PRESSURE (선별적 강세)"
    else:
        label = "CORRECTION / 관망 (강세 종목 희소)"

    return {"label": label, "breadth": round(breadth * 100, 2),
           "scanned": scanned, "passed": passed}


# ═════════════════════════════════════════════════════════════
# 세션 맥락 판단
# ═════════════════════════════════════════════════════════════

def session_context(now_kst: dt.datetime) -> dict:
    """
    지금이 하루 중 어느 시점인지에 따라 서술의 초점을 바꾼다.
    본 시스템은 평일 18:30·06:30(KST) 두 시점에 자동 실행되도록 설계되어 있어,
    그 두 경우를 기본으로 판단하되 그 외 시각(수동 실행 등)도 다룬다.
    """
    h, m = now_kst.hour, now_kst.minute
    minutes = h * 60 + m

    kr_close = 15 * 60 + 30
    us_open_kst = 22 * 60 + 30    # 서머타임 기준 근사치
    us_close_kst = 5 * 60         # 익일 05:00 근사치
    us_close_kst_2 = 6 * 60       # 표준시 기준 06:00

    if kr_close <= minutes < us_open_kst:
        return {"phase": "KR_AFTER_CLOSE",
               "label": "한국 마감 후 · 미국 장 시작 전",
               "focus": "오늘 한국 시장 스캔 결과를 정리하고, 밤사이 미국 장에서 지켜볼 종목을 미리 확인하는 시간입니다."}
    if minutes >= us_open_kst or minutes < us_close_kst_2:
        return {"phase": "US_SESSION",
               "label": "미국 장중 (또는 장 마감 직후)",
               "focus": "미국 시장이 열려 있거나 막 마감된 시점입니다. 신규 진입은 오늘자 종가 확정 후 재검토를 권장합니다."}
    if us_close_kst_2 <= minutes < kr_close - 6 * 60:
        return {"phase": "US_AFTER_CLOSE",
               "label": "미국 마감 후 · 한국 장 시작 전",
               "focus": "간밤 미국 시장 결과를 반영해 오늘 한국 장에서의 대응을 준비하는 시간입니다."}
    return {"phase": "KR_SESSION",
           "label": "한국 장중",
           "focus": "정규장 진행 중입니다. 스캔은 전일 종가 기준이므로, 장중 변동은 별도로 확인하세요."}


# ═════════════════════════════════════════════════════════════
# 전략 본문 생성
# ═════════════════════════════════════════════════════════════

def build_strategy(df: pd.DataFrame, date_str: str, registry: dict,
                   now_kst: dt.datetime = None) -> dict:
    now_kst = now_kst or dt.datetime.now(KST)
    sess = session_context(now_kst)
    kr_stat = mc.kr_status(date_str)
    us_stat = mc.us_status(date_str)
    kr_regime = market_regime(df, "KR") if kr_stat["open"] else None
    us_regime = market_regime(df, "US") if us_stat["open"] else None

    passed = df[df["PASS"] == True].copy()
    rows = []
    for ticker, r in passed.sort_values("RS", ascending=False).iterrows():
        label, first_date = elapsed_for(registry, r["market"], ticker, date_str)
        rows.append({
            "ticker": ticker,
            "name": r.get("name", ticker),
            "market": r["market"],
            "rs": None if pd.isna(r.get("RS")) else round(r["RS"], 1),
            "high": None if pd.isna(r.get("vs_52w_high_%")) else round(r["vs_52w_high_%"], 1),
            "elapsed": label or "–",
            "first_date": first_date,
        })

    # ── 서술 섹션 구성 ─────────────────────────────────────
    sections = []

    regime_lines = []
    if kr_stat["open"] and kr_regime:
        regime_lines.append(
            f"한국: {kr_regime['label']} — 스캔 {kr_regime['scanned']:,}종목 중 "
            f"{kr_regime['passed']}종목 통과 (breadth {kr_regime['breadth']}%)")
    else:
        regime_lines.append(f"한국: {kr_stat['label']} — 스캔 미실시")

    if us_stat["open"] and us_regime:
        regime_lines.append(
            f"미국: {us_regime['label']} — 스캔 {us_regime['scanned']:,}종목 중 "
            f"{us_regime['passed']}종목 통과 (breadth {us_regime['breadth']}%)")
    else:
        regime_lines.append(f"미국: {us_stat['label']} — 스캔 미실시")

    sections.append({"title": "1. 오늘의 시장 국면", "body": regime_lines})

    # 국면별 포지션 사이징 가이드 (미네르비니 규칙 요약)
    def sizing_guide(regime):
        if regime is None:
            return "휴장 — 포지션 조정 불필요"
        if "CONFIRMED" in regime["label"]:
            return "정상 비중 진입 가능 (신규 진입 적극 검토)"
        if "PRESSURE" in regime["label"]:
            return "포지션 축소 · 선별 진입만 (통과종목 상위 RS 위주)"
        return "신규 진입 자제 · 현금 비중 확대 검토"

    sections.append({
        "title": "2. 포지션 사이징 가이드",
        "body": [
            f"한국: {sizing_guide(kr_regime) if kr_stat['open'] else '휴장'}",
            f"미국: {sizing_guide(us_regime) if us_stat['open'] else '휴장'}",
            "※ 이는 스캔 통과율(breadth)에 기반한 보조 지표입니다. "
            "지수 자체의 추세(코스피/코스닥, S&P500/나스닥)와 함께 판단하세요.",
        ],
    })

    entry_lines = [
        "신규 진입은 피벗포인트(직전 저항 돌파) 근접 종목 중 RS 상위 종목부터 검토합니다.",
        "매수 후 거래량이 평소 대비 40~50% 이상 증가하며 돌파하는지 확인하세요 (거래량 미동반 돌파는 다이버전스로 간주).",
        "1회 매수 비중은 총 자본의 20~25%를 넘기지 않고, 분할 진입을 원칙으로 합니다.",
    ]
    sections.append({"title": "3. 신규 진입 체크리스트", "body": entry_lines})

    exit_lines = [
        "손절선은 매수가 대비 -7~8% 고정. SEPA는 이 룰을 예외 없이 지키는 것을 핵심으로 합니다.",
        "보유 종목이 8개 조건 중 2개 이상 미달로 전환되면 비중 축소를 검토하세요 (관찰 리스트로 편입).",
        "수익 구간에서는 200일선 또는 50일선 이탈을 트레일링 스탑 기준으로 활용합니다.",
    ]
    sections.append({"title": "4. 보유·청산 기준", "body": exit_lines})

    sections.append({
        "title": "5. 세션 메모",
        "body": [f"{sess['label']} — {sess['focus']}"],
    })

    return {
        "date": date_str,
        "generated_at": now_kst.strftime("%Y-%m-%d %H:%M KST"),
        "session": sess,
        "kr_status": kr_stat,
        "us_status": us_stat,
        "sections": sections,
        "table": rows,
        "disclaimer": (
            "본 전략은 그날 스캔 데이터(통과종목 수, RS 분포)를 SEPA 트렌드템플릿 "
            "규칙에 대입해 자동 생성한 것으로, 실시간 뉴스·매크로 지표를 그 순간에 "
            "조회해 요약한 것이 아닙니다. 투자 판단의 최종 책임은 투자자 본인에게 있습니다."
        ),
    }
