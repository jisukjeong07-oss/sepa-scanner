# -*- coding: utf-8 -*-
"""
find_missed_patterns.py — "developing(형성 중)까지는 갔는데, entry_ready
(진입가능)를 한 번도 못 거치고 extended(너무 늘어남)로 넘어가버린" 종목을
전체 유니버스에서 찾는다.

[2026-09-19] 만든 이유
- SK하이닉스(000660) 진단에서 발견한 패턴 — "베이스를 만들다가(developing),
  entry_ready 없이 바로 extended로 건너뜀" — 이 다른 종목에도 흔한지
  확인하기 위해 만들었다. diagnose_ticker.py가 종목 하나에 대해 하던
  일(8조건 게이트+VCP 상태 확인)을 전체 유니버스로 확장한 것뿐이다.
- 판정 로직: 8조건을 통과한 날들만 순서대로 훑으면서 VCP 상태 흐름을
  본다. "developing"을 한 번이라도 본 뒤, 그 다음에 "entry_ready"를
  안 거치고 "extended"가 나오면 그 종목을 "놓친 패턴"으로 표시한다.
  (developing과 extended 사이에 no_pattern이 끼어도 상관없다 — 중요한
  건 그 사이에 entry_ready가 없었다는 것뿐이다.)
- 수익률은 "처음 developing이 된 날"부터 "그 extended가 확인된 날"까지의
  가격 변화로 계산한다 — "이 구간을 놓친 게 실제로 얼마나 아까웠는지"를
  숫자로 보여주기 위함이다.
"""
import os
import sys
import time
import argparse

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as bt
import vcp as _vcp

OUT_DIR = bt.OUT_DIR


def _find_all_episodes(dates, close, high, low, value, gate, start_t, end_t):
    """
    [2026-09-20] 예전 버전은 "첫 번째 developing→extended"만 찾고 멈춰서,
    SK하이닉스처럼 같은 종목 안에 여러 번(5번) 비슷한 패턴이 있으면 가장
    작았던 첫 파동만 보고 "별로 안 아까웠다"고 착각하게 만드는 문제가
    있었다. 이번엔 종목 하나를 끝까지 훑으면서 "developing→extended"가
    몇 번이든 나오는 대로 전부 기록한다.

    추가로, 구간 끝(end_t)까지 갔는데도 developing 상태가 안 끝나고 남아
    있으면(아직 entry_ready도 extended도 안 됨) "현재 관찰 중"으로 별도
    표시한다 — 이건 "놓친 것"이 아니라 "아직 결론이 안 난 것"이라
    확정된 파동(episodes)과는 분리해서 다뤄야 한다(매수 신호로 오인되면
    안 되므로 화면에서도 이 둘을 반드시 구분해서 보여준다).

    반환값: (episodes, currently_developing_idx)
      episodes: [{dev_idx, ext_idx, recovered_idx}, ...] — 확정된 파동들
      currently_developing_idx: 구간 끝까지 안 끝난 developing 시작일
                                 (없으면 None)
    """
    status_log = []
    for t in range(start_t, end_t + 1):
        if not gate[t]:
            continue
        res = _vcp.analyze_ticker(high[:t + 1], low[:t + 1], close[:t + 1], value[:t + 1])
        status_log.append((t, res.get("vcp_status") if res else None))

    episodes = []
    developing_idx = None
    for t, status in status_log:
        if status == "entry_ready":
            developing_idx = None
        elif status == "developing" and developing_idx is None:
            developing_idx = t
        elif status == "extended" and developing_idx is not None:
            episodes.append({"dev_idx": developing_idx, "ext_idx": t})
            developing_idx = None   # 리셋 — 다음 파동을 새로 찾기 시작

    for ep in episodes:
        ep["recovered_idx"] = next(
            (t2 for t2, s2 in status_log if t2 > ep["ext_idx"] and s2 == "entry_ready"), None)

    # [2026-09-20] "현재 관찰 중"은 반드시 "오늘(end_t) 기준으로 8조건도
    # 통과하고 developing 상태"여야 한다. 예전 로직은 그냥 "developing_idx가
    # 안 풀린 채 루프가 끝났는지"만 봤는데, 그 종목이 몇 달 전 딱 하루만
    # 8조건을 통과하고 그 뒤로 다시는 통과 못 했어도(=사실상 죽은 신호)
    # 그대로 "관찰 중"으로 잡혔다. 실사례: 이 버그로 한국 유니버스의
    # 절반 넘게(1464종목)가 "관찰 중"으로 잘못 집계됐다. status_log의
    # 마지막 기록이 실제로 오늘(end_t)인지까지 확인해야 진짜 "지금"이다.
    currently_developing_idx = None
    if (developing_idx is not None and status_log
            and status_log[-1][0] == end_t and status_log[-1][1] == "developing"):
        currently_developing_idx = developing_idx
    return episodes, currently_developing_idx


def _find_missed_pattern_for_ticker(dates, close, high, low, value, gate, start_t, end_t):
    """[구버전 호환용] 여러 파동 중 첫 번째만 반환한다. 이제부터는 되도록
    _find_all_episodes()를 직접 쓰는 걸 권장한다 — 이 함수는 diagnose_ticker.py
    같은 기존 코드가 그대로 동작하도록 남겨둔 것뿐이다."""
    episodes, _ = _find_all_episodes(dates, close, high, low, value, gate, start_t, end_t)
    if not episodes:
        return None
    ep = episodes[0]
    return (ep["dev_idx"], ep["ext_idx"], ep["recovered_idx"])


def run(market: str = "KR", top_n: int = 20, progress_every: int = 500):
    print(f"[놓친패턴찾기] [{market}] 데이터 로딩 중...")
    loader = {"KR": bt._load_kr_prices, "US": bt._load_us_prices}[market]
    close, high, low, value = loader(min_rows=bt.GATE_MIN_HISTORY + 20)

    valid = close.notna().sum() >= bt.GATE_MIN_HISTORY
    close, high, low, value = (df.loc[:, valid] for df in (close, high, low, value))
    close, high, low = close.ffill(), high.ffill(), low.ffill()

    n = len(close)
    start_t, end_t = bt.GATE_MIN_HISTORY, n - 1
    dates = close.index.to_numpy()

    print(f"[놓친패턴찾기] {len(close.columns)}종목 / {n}영업일 · 8조건 게이트 계산 중...")
    gate_df, _ = bt._build_trend_template_gate(close, high, low)

    tickers = list(close.columns)
    results = []
    gate_ever_count = 0   # "8조건을 한 번이라도 통과한 종목 수" — 비율 계산의 분모용
    t0 = time.time()
    for i, ticker in enumerate(tickers):
        if progress_every and i % progress_every == 0 and i > 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(tickers) - i)
            print(f"  {i}/{len(tickers)}종목 처리 · 경과 {elapsed:.0f}초 · 예상 잔여 {eta:.0f}초")
        c = close[ticker].to_numpy(dtype=float)
        h = high[ticker].to_numpy(dtype=float)
        l = low[ticker].to_numpy(dtype=float)
        v = value[ticker].to_numpy(dtype=float)
        g = gate_df[ticker].to_numpy(dtype=bool)
        if np.isnan(c).all():
            continue
        if not g[start_t:end_t + 1].any():
            continue   # 8조건을 애초에 한 번도 통과 못 한 종목 — "놓친 기회" 자체가 없었음
        gate_ever_count += 1

        hit = _find_missed_pattern_for_ticker(dates, c, h, l, v, g, start_t, end_t)
        if hit is None:
            continue
        dev_idx, ext_idx, recovered_idx = hit
        dev_price, ext_price = c[dev_idx], c[ext_idx]
        results.append({
            "ticker": ticker,
            "developing_date": str(dates[dev_idx])[:10],
            "extended_date": str(dates[ext_idx])[:10],
            "developing_price": dev_price,
            "extended_price": ext_price,
            "missed_return_pct": round((ext_price / dev_price - 1) * 100, 2),
            "days_span": ext_idx - dev_idx,
            "recovered_later": recovered_idx is not None,
            "recovered_date": str(dates[recovered_idx])[:10] if recovered_idx is not None else None,
        })

    print(f"[놓친패턴찾기] 완료 · {len(results)}종목 발견 · 총 {time.time()-t0:.0f}초")
    print(f"[놓친패턴찾기] 8조건을 한 번이라도 통과한 종목 {gate_ever_count}개 중 "
          f"{len(results)}개({len(results)/gate_ever_count*100:.1f}%)가 이 패턴에 해당")
    if results:
        recovered_n = sum(1 for r in results if r["recovered_later"])
        print(f"[놓친패턴찾기] 그중 이후에 entry_ready로 회복한 종목: "
              f"{recovered_n}개 ({recovered_n/len(results)*100:.1f}%)")

    df = pd.DataFrame(results).sort_values("missed_return_pct", ascending=False)
    return df.head(top_n).reset_index(drop=True), df, gate_ever_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    ap.add_argument("--top", type=int, default=20,
                     help="상위 몇 개까지 보여줄지 직접 입력 (기본 20)")
    args = ap.parse_args()

    top_df, full_df, gate_ever_count = run(market=args.market, top_n=args.top)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = pd.Timestamp.today().strftime("%Y%m%d")
    out_path = os.path.join(OUT_DIR, f"missed_patterns_{args.market}_{stamp}.csv")
    full_df.to_csv(out_path, index=False, encoding="utf-8-sig")

    print()
    print("=" * 70)
    print(f" developing → entry_ready 없이 → extended 로 넘어간 종목 "
          f"(상위 {len(top_df)}개, 전체 {len(full_df)}개 중)")
    print(f" (8조건 한 번이라도 통과한 {gate_ever_count}종목 중 "
          f"{len(full_df)}개 = {len(full_df)/gate_ever_count*100:.1f}%)")
    print("=" * 70)
    if top_df.empty:
        print(" 해당하는 종목이 없습니다.")
    else:
        print(top_df.to_string(index=False))
    print("=" * 70)
    print(f" 전체 목록 저장: {out_path}")


if __name__ == "__main__":
    main()
