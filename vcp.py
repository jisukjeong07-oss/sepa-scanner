# -*- coding: utf-8 -*-
"""
VCP(Volatility Contraction Pattern) 자동 분석 — SEPA 2단계 P0.

[2026-09-13] 확정한 방식:
  - 레그(조정 구간) 유효 여부: 고정 %가 아니라 ATR(변동성) 기반
    (한국 상하한가 ±30%와 미국 시장의 변동성 차이를 자동으로 흡수하기 위함)
  - "수축 중인가" 판정: 최근 2개 레그만 비교 (마지막 레그가 그 직전보다
    좁으면 수축 중). 전체 레그의 단조 감소를 요구하면 실전에서 흔한
    "중간에 한 번 삐끗"까지 전부 탈락시켜 너무 엄격해진다.

주의: 이건 자동 근사치다. 미너비니 본인도 VCP는 결국 차트를 눈으로
확인해야 한다고 강조한다 — 이 모듈은 46종목을 3종목으로 좁혀주는
1차 필터일 뿐, 최종 판단은 차트로 봐야 한다.
"""

import numpy as np
import pandas as pd

ATR_WINDOW = 20        # ATR 계산 기간
ATR_MULT = 2.0         # 이 배수 이상 움직여야 유효한 스윙(레그 경계)로 인정
                        # [2026-09-13] 1.5 -> 2.0. 실제 미국 종목 검증에서
                        # 1.5배는 XOM 14개·Occidental 12개처럼 사소한 등락까지
                        # 레그로 잡는 과탐지가 확인돼 올렸다.
LOOKBACK = 130          # 베이스 후보 구간 (약 6개월, 거래일 기준)
MIN_LEGS_FOR_JUDGE = 2  # 이 이상이어야 "수축 중"을 판단할 수 있음
ENTRY_PCT_FROM_PIVOT = (-3.0, 2.0)   # 이 범위면 '진입 가능' 후보
ENTRY_MAX_RISK_PCT = 10.0            # 손절까지 리스크가 이 이내여야 '진입 가능'
ENTRY_BUFFER_PCT = 1.0               # 피벗 위 몇 % 지점을 진입가로 볼지


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    return np.maximum(high - low, np.maximum(np.abs(high - prev_close),
                                              np.abs(low - prev_close)))


def _zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, anchor: int) -> list:
    """
    anchor(베이스 시작점, 스윙 고점으로 간주)부터 끝까지 ATR 기반 지그재그로
    스윙 고점·저점을 찾는다. 마지막 구간은 아직 반등으로 '확정'되지 않았어도
    지금까지의 극값을 그대로 넣는다 — 진짜 매수 시점은 반등 확정 전, 아직
    눌림목이 진행 중인 시점이기 때문이다(확정을 기다리면 이미 늦는다).

    반환: [(index, price, 'H'|'L', confirmed: bool), ...]
    """
    n = len(high)
    swings = [(anchor, high[anchor], "H", True)]
    if anchor >= n - 1:
        return swings

    mode = "seek_low"
    extreme_idx, extreme_val = anchor, low[anchor]

    for i in range(anchor + 1, n):
        threshold = ATR_MULT * (atr[i] if not np.isnan(atr[i]) else 0)
        if mode == "seek_low":
            if low[i] < extreme_val:
                extreme_idx, extreme_val = i, low[i]
            if threshold > 0 and (high[i] - extreme_val) >= threshold:
                swings.append((extreme_idx, extreme_val, "L", True))
                mode = "seek_high"
                extreme_idx, extreme_val = i, high[i]
        else:
            if high[i] > extreme_val:
                extreme_idx, extreme_val = i, high[i]
            if threshold > 0 and (extreme_val - low[i]) >= threshold:
                swings.append((extreme_idx, extreme_val, "H", True))
                mode = "seek_low"
                extreme_idx, extreme_val = i, low[i]

    # 마지막 미확정 구간도 넣는다 (위 설명 참고)
    last_type = "L" if mode == "seek_low" else "H"
    if swings[-1][0] != extreme_idx:
        swings.append((extreme_idx, extreme_val, last_type, False))
    return swings


def analyze_ticker(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                   value: np.ndarray, dates=None) -> dict:
    """
    한 종목의 최근 이력(고가·저가·종가·거래대금, 시간순 오름차순, 마지막이
    최신일)을 받아 VCP 분석 결과를 반환한다. 데이터가 부족하면 None.

    dates가 주어지면(같은 길이의 날짜 배열), 반환값에 'chart' 키로 베이스
    구간(anchor~현재)의 날짜·가격·레그 구간·피벗선·손절선을 함께 담는다.
    대시보드가 이 값을 그대로 받아 미니 차트를 그릴 수 있게 하기 위함이다
    (별도 API 호출 없이, 분석에 쓴 것과 완전히 같은 구간·같은 인덱스로
    그려야 "레그를 왜 저렇게 잘랐지?"를 검증할 수 있다).
    """
    n = len(close)
    if n < LOOKBACK + ATR_WINDOW or n == 0:
        return None

    tr = _true_range(high, low, close)
    atr = pd.Series(tr).rolling(ATR_WINDOW).mean().to_numpy()

    start = n - LOOKBACK
    w_high, w_low, w_close = high[start:], low[start:], close[start:]
    w_value, w_atr = value[start:], atr[start:]
    w_dates = dates[start:] if dates is not None else None

    anchor = int(np.argmax(w_high))   # 구간 내 최고가 지점을 베이스 시작으로
    swings = _zigzag(w_high, w_low, w_atr, anchor)

    legs = []
    for j in range(1, len(swings)):
        prev_idx, prev_val, prev_type, _ = swings[j - 1]
        idx, val, typ, confirmed = swings[j]
        if prev_type == "H" and typ == "L":
            legs.append({
                "high_idx": prev_idx, "high": float(prev_val),
                "low_idx": idx, "low": float(val),
                "pct": round((val / prev_val - 1) * 100, 2),
                "confirmed": confirmed,
            })

    contraction_count = len(legs)
    cur_close = float(w_close[-1])

    if contraction_count == 0:
        # 유효한 눌림목이 아직 하나도 안 잡힘 — 계속 신고가 경신 중이거나
        # 데이터가 짧은 경우. 피벗은 베이스 시작점의 고가로 잠정 표시한다.
        pivot_price = float(w_high[anchor])
        stop_price = float(np.min(w_low[anchor:]))
        is_tightening = None
        vol_dryup = None
    else:
        last_leg = legs[-1]
        pivot_price = last_leg["high"]
        stop_price = last_leg["low"]
        if contraction_count >= MIN_LEGS_FOR_JUDGE:
            is_tightening = bool(abs(last_leg["pct"]) < abs(legs[-2]["pct"]))
        else:
            is_tightening = None   # 레그가 1개뿐이면 "좁아지는 중"을 판단할 기준점이 없음

        last_vol = np.nanmean(w_value[last_leg["high_idx"]:last_leg["low_idx"] + 1])
        if contraction_count >= 2:
            prev_leg = legs[-2]
            prev_vol = np.nanmean(w_value[prev_leg["high_idx"]:prev_leg["low_idx"] + 1])
            vol_dryup = bool(last_vol < prev_vol) if prev_vol == prev_vol else None
        elif last_leg["high_idx"] > 0:
            base_vol = np.nanmean(w_value[:last_leg["high_idx"]])
            vol_dryup = bool(last_vol < base_vol) if base_vol == base_vol else None
        else:
            vol_dryup = None

    entry_price = pivot_price * (1 + ENTRY_BUFFER_PCT / 100)
    risk_pct = (round((entry_price - stop_price) / entry_price * 100, 2)
                if stop_price < entry_price else None)
    pct_from_pivot = round((cur_close / pivot_price - 1) * 100, 2) if pivot_price else None

    if contraction_count == 0:
        vcp_status = "no_pattern"
    elif pct_from_pivot is None:
        vcp_status = "unknown"
    elif is_tightening is not True:
        # [2026-09-13] 핵심 수정: "수축이 확인됐을 때만" 진입가능으로 판정한다.
        # 이전엔 피벗 거리·리스크만 보고 판정해서, is_tightening=False(수축
        # 아님)인 종목까지 "진입가능"으로 뜨는 모순이 있었다(예: 2026-09-13
        # 실사례 — Travelers·Apple·Citigroup 등 6종목이 '수축 아님'인데도
        # 진입가능으로 표시됨). 레그가 1개뿐이라 판단 불가(None)인 경우도
        # 마찬가지로 아직 확신할 수 없으니 진입가능을 주지 않는다.
        vcp_status = "developing" if pct_from_pivot <= ENTRY_PCT_FROM_PIVOT[1] else "extended"
    elif ENTRY_PCT_FROM_PIVOT[0] <= pct_from_pivot <= ENTRY_PCT_FROM_PIVOT[1] and \
            (risk_pct is not None and risk_pct <= ENTRY_MAX_RISK_PCT):
        vcp_status = "entry_ready"
    elif pct_from_pivot > ENTRY_PCT_FROM_PIVOT[1]:
        vcp_status = "extended"
    else:
        vcp_status = "developing"

    result = {
        "pivot_price": round(pivot_price, 2),
        "stop_price": round(stop_price, 2),
        "pct_from_pivot": pct_from_pivot,
        "risk_pct": risk_pct,
        "contraction_count": contraction_count,
        "is_tightening": is_tightening,
        "vol_dryup": vol_dryup,
        "vcp_status": vcp_status,
        "legs": [round(l["pct"], 1) for l in legs],   # 디버깅·검증용
    }

    if w_dates is not None:
        # 미니 차트용 페이로드. 인덱스는 이 구간(w_*) 안에서의 위치라서
        # 분석에 쓴 것과 정확히 같은 좌표계다 — 프런트엔드가 따로 재계산할
        # 필요 없이 그대로 그리면 된다.
        def _dstr(d):
            return pd.Timestamp(d).strftime("%Y-%m-%d")

        result["chart"] = {
            "dates": [_dstr(d) for d in w_dates],
            "close": [round(float(x), 2) for x in w_close],
            "high": [round(float(x), 2) for x in w_high],
            "low": [round(float(x), 2) for x in w_low],
            "anchor_idx": anchor,
            "pivot_price": result["pivot_price"],
            "stop_price": result["stop_price"],
            "legs": [{"high_idx": l["high_idx"], "low_idx": l["low_idx"],
                      "pct": round(l["pct"], 1)} for l in legs],
        }

    return result


def add_vcp_columns(result_df: pd.DataFrame, close: pd.DataFrame, high: pd.DataFrame,
                    low: pd.DataFrame, value: pd.DataFrame, chart_store: dict = None) -> pd.DataFrame:
    """
    screen() 결과(result_df, 종목코드가 index)에 VCP 컬럼을 붙여 반환한다.
    high/low가 없는 시장(예전 캐시)이면 VCP 계산 없이 컬럼만 비운 채 반환한다.

    chart_store를 넘기면(딕셔너리), 종목별 미니 차트 페이로드를 그 안에
    채워 넣는다(원본을 in-place로 수정). CSV에는 안 싣고 별도 JSON으로
    저장하기 위한 통로다 — 종목당 130일치 가격을 CSV 컬럼에 다 박으면
    파일이 지저분해지고 다른 도구로 열어보기도 불편해진다.
    """
    cols = ["pivot_price", "stop_price", "pct_from_pivot", "risk_pct",
            "contraction_count", "is_tightening", "vol_dryup", "vcp_status"]

    if high is None or low is None:
        print("[VCP] 고가·저가가 없어 VCP 계산을 건너뜁니다.")
        for c in cols:
            result_df[c] = None
        return result_df

    dates = close.index.to_numpy()
    records = {}
    for ticker in result_df.index:
        if ticker not in close.columns:
            continue
        c = close[ticker].to_numpy(dtype=float)
        h = high[ticker].to_numpy(dtype=float) if ticker in high.columns else None
        l = low[ticker].to_numpy(dtype=float) if ticker in low.columns else None
        v = value[ticker].to_numpy(dtype=float) if ticker in value.columns else np.zeros_like(c)
        if h is None or l is None:
            continue
        res = analyze_ticker(h, l, c, v, dates=dates)
        if res:
            records[ticker] = res
            if chart_store is not None and "chart" in res:
                chart_store[ticker] = res["chart"]

    for c in cols:
        result_df[c] = result_df.index.map(lambda t: records.get(t, {}).get(c))

    n_ready = sum(1 for r in records.values() if r["vcp_status"] == "entry_ready")
    print(f"[VCP] {len(records)}/{len(result_df)}종목 분석 완료 · Entry Ready {n_ready}종목")
    return result_df
