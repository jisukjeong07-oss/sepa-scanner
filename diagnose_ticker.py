# -*- coding: utf-8 -*-
"""
diagnose_ticker.py — 특정 종목이 백테스트 신호에서 왜 빠졌는지(또는 왜
적게 잡혔는지) 진단한다.

[2026-09-19] 만든 이유
- "SK하이닉스처럼 1년간 크게 오른 종목이 왜 신호에 안 잡혔나"를 추측이
  아니라 실제 데이터로 확인하기 위해 만들었다. backtest.py의
  _build_trend_template_gate()를 그대로 호출해서 "통과/불통과"를 얻고,
  그 안의 개별 8조건 수식도 똑같이 재현해 "어느 조건이 발목을 잡았는지"
  까지 쪼개본다 — 두 계산이 어긋나면(assert) 바로 알 수 있게 해서,
  이 스크립트의 조건식이 실제 백테스트와 다르게 새는 걸 막는다.
- 확인하는 것 두 가지:
    1) 8조건 게이트를 하루하루 통과했는지, 못 했다면 어느 조건이
       가장 자주 발목을 잡았는지
    2) 게이트를 통과한 날들 중에서, VCP가 실제로 "진입가능(entry_ready)"
       으로 판정한 날이 있었는지 — 없다면 "추세는 맞는데 VCP가 요구하는
       수축(contraction) 패턴 자체가 안 나온" 경우다. 급등이 너무 가팔라서
       눌림목(베이스) 없이 계속 뻗기만 한 종목이 전형적으로 이렇다.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as bt
import vcp as _vcp
from sepa_scanner import MIN_RS, NEAR_HIGH_PCT, ABOVE_LOW_PCT, MA200_SLOPE_DAYS

COND_NAMES = {
    "c1": "150·200일선 위",
    "c2": "150일선 > 200일선",
    "c3": "200일선 상승 중(21일 전 대비)",
    "c4": "50일선이 150·200일선 위",
    "c5": "종가가 50일선 위",
    "c6": "52주 저점 대비 +30% 이상",
    "c7": "52주 고점 대비 -25% 이내",
    "c8": "RS 70 이상",
}


def diagnose(ticker: str, market: str = "KR"):
    print(f"=== {ticker} ({market}) 진단 시작 ===\n")

    loader = bt._load_kr_prices if market == "KR" else bt._load_us_prices
    close, high, low, value = loader(min_rows=bt.GATE_MIN_HISTORY + 20)

    if ticker not in close.columns:
        print(f"[결과] '{ticker}'가 유니버스에 아예 없습니다 — 이름/코드 표기, "
              f"또는 상장폐지·거래정지 여부를 확인해 주세요.")
        return

    valid = close.notna().sum() >= bt.GATE_MIN_HISTORY
    if not valid.get(ticker, False):
        print(f"[결과] '{ticker}'는 데이터가 {bt.GATE_MIN_HISTORY}일 미만이라 "
              f"애초에 분석 대상에서 제외됐습니다(신규상장 등으로 과거 데이터 부족).")
        return

    close, high, low = close.ffill(), high.ffill(), low.ffill()
    n = len(close)
    start_t, end_t = bt.GATE_MIN_HISTORY, n - 1

    # 실제 백테스트와 동일한 함수로 공식 게이트·RS를 구한다(기준선).
    official_gate, official_rs = bt._build_trend_template_gate(close, high, low)

    # 개별 조건 쪼개기(위 공식 게이트와 같은 수식) — 어느 조건이 걸림돌인지 보려는 용도.
    ma50, ma150, ma200 = close.rolling(50).mean(), close.rolling(150).mean(), close.rolling(200).mean()
    ma200_prev = ma200.shift(MA200_SLOPE_DAYS)
    hi52, lo52 = high.rolling(252).max(), low.rolling(252).min()
    px = close[ticker]
    conds = pd.DataFrame({
        "c1": (px > ma150[ticker]) & (px > ma200[ticker]),
        "c2": ma150[ticker] > ma200[ticker],
        "c3": ma200[ticker] > ma200_prev[ticker],
        "c4": (ma50[ticker] > ma150[ticker]) & (ma50[ticker] > ma200[ticker]),
        "c5": px > ma50[ticker],
        "c6": (px / lo52[ticker] - 1) * 100 >= ABOVE_LOW_PCT,
        "c7": (px / hi52[ticker] - 1) * 100 >= -NEAR_HIGH_PCT,
        "c8": official_rs[ticker] >= MIN_RS,
    }).iloc[start_t:end_t + 1].fillna(False)

    my_gate = conds.all(axis=1)
    official_slice = official_gate[ticker].iloc[start_t:end_t + 1]
    mismatch = int((my_gate.to_numpy() != official_slice.to_numpy()).sum())
    if mismatch:
        print(f"[경고] 이 진단 스크립트의 조건식이 실제 백테스트와 {mismatch}일 어긋납니다 — "
              f"아래 조건별 통과율은 참고만 하고, '전체 통과일수'는 공식 게이트 쪽을 믿으세요.\n")
    gate_all = official_slice   # 최종 판정은 항상 공식 게이트를 기준으로 삼는다

    total_days = len(gate_all)
    pass_days = int(gate_all.sum())
    print(f"[8조건 게이트] 분석 구간 {total_days}거래일 중 "
          f"전체 통과 {pass_days}일 ({pass_days/total_days*100:.1f}%)\n")

    rates = {c: conds[c].mean() * 100 for c in COND_NAMES}
    min_rate = min(rates.values())
    print("조건별 통과율(개별 조건 기준 — 낮을수록 자주 발목을 잡았다는 뜻):")
    for c, label in COND_NAMES.items():
        marker = "  <- 가장 자주 걸림" if rates[c] == min_rate else ""
        print(f"  {c} ({label}): {rates[c]:.1f}%{marker}")

    if pass_days == 0:
        print("\n[결론] 8조건을 동시에 만족한 날이 분석 구간 내내 단 하루도 없었습니다.")
        print("       위 조건별 통과율에서 가장 낮은 항목이 주된 원인입니다.")
        return

    print(f"\n[VCP 확인] 8조건 통과일 {pass_days}일 동안 VCP 상태 분포:")
    c_arr = close[ticker].to_numpy(dtype=float)
    h_arr = high[ticker].to_numpy(dtype=float)
    l_arr = low[ticker].to_numpy(dtype=float)
    v_arr = value[ticker].to_numpy(dtype=float)
    gate_arr = gate_all.to_numpy()

    vcp_status_counts = {}
    entry_ready_dates = []
    for i, t in enumerate(range(start_t, end_t + 1)):
        if not gate_arr[i]:
            continue
        res = _vcp.analyze_ticker(h_arr[:t + 1], l_arr[:t + 1], c_arr[:t + 1], v_arr[:t + 1])
        status = res.get("vcp_status") if res else "분석불가"
        vcp_status_counts[status] = vcp_status_counts.get(status, 0) + 1
        if status == "entry_ready":
            entry_ready_dates.append(str(close.index[t].date()))

    for status, cnt in sorted(vcp_status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {cnt}일")

    if entry_ready_dates:
        print(f"\n[결론] entry_ready(진입가능) 신호가 {len(entry_ready_dates)}일 있었습니다:")
        print(f"       {entry_ready_dates[:10]}{' ...' if len(entry_ready_dates) > 10 else ''}")
        print("       그런데도 최종 거래 목록에 없었다면, 같은 시점에 다른 종목들과 "
              "동시보유 한도(슬롯)를 다투다 밀렸을 가능성이 높습니다.")
        print("       backtest_trades CSV에서 이 종목코드로 직접 검색해 확인해 보세요.")
    else:
        print(f"\n[결론] 8조건은 통과했지만, VCP가 '진입가능'으로 판정한 날이 "
              f"단 하루도 없었습니다.")
        print("       가장 흔한 이유: 급등이 너무 가팔라서 VCP가 요구하는 "
              "'수축(contraction) 후 피벗 돌파' 패턴 자체가 형성되지 않았을 가능성이 큽니다.")
        print("       눌림목(베이스) 없이 계속 오르기만 한 종목은, 추세 자체는 완벽해도 "
              "VCP 관점에서는 '들어갈 타이밍'이 안 나온 것으로 판정됩니다.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", help="종목코드 (예: 000660)")
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    args = ap.parse_args()
    diagnose(args.ticker, args.market)
