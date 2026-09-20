# -*- coding: utf-8 -*-
"""
missed_patterns_data.py — run_daily.py가 매일 호출하는 "놓친 패턴 관찰"
패널용 스냅샷 생성기.

[2026-09-20] 설계 근거
- find_missed_patterns.py의 핵심 판정 로직(_find_all_episodes)을 그대로
  재사용한다 — 판정 기준이 두 군데서 서로 다르게 새면 안 되므로.
- 매일 돌리는 이유: 계산 자체가 가볍다(한국 전체 유니버스 기준 3초대).
  "더 자주 계산한다고 기회를 더 빨리 잡아주는 건 아니다"(이 패턴 자체가
  extended로 확정된 뒤에야 성립하는 사후 판정이라서)는 점은 이미
  논의했지만, 비용이 저렴하니 아낄 이유가 없어 매일 갱신한다.
- 두 그룹으로 분리해서 반환한다 — 화면에서도 반드시 분리해서 보여줘야
  한다(사용자 요청):
    confirmed  : 이미 extended로 확정된 파동(사후 분석용, 매매 신호 아님)
    developing : 아직 결론이 안 난 채 진행 중인 것(watch 후보, "놓친 것"이 아님)
- recent_days: confirmed 목록은 최근 파동만 남긴다. 1년 전 파동까지 매일
  다 보여주면 패널이 무한정 길어지고, 오래된 건 지금 시점 의미가 낮다.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as bt
from find_missed_patterns import _find_all_episodes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_kr_name_map():
    kr_map = {}
    path = os.path.join(BASE_DIR, "cache", "kr_listing.parquet")
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
            name_col = next((c for c in ("Name", "name", "종목명") if c in df.columns), None)
            if name_col:
                idx = df.index.astype(str).str.zfill(6)
                kr_map = dict(zip(idx, df[name_col]))
        except Exception as e:
            print(f"[놓친패턴] 한국 종목명 캐시 읽기 실패(코드로 대체): {e}")
    return kr_map


def _us_name_map(tickers):
    try:
        from sepa_scanner import us_names
        return dict(us_names(tickers))
    except Exception as e:
        print(f"[놓친패턴] 미국 종목명 조회 실패(코드로 대체): {str(e)[:150]}")
        return {}


def _compute_for_market(market: str, recent_days: int = 90):
    loader = {"KR": bt._load_kr_prices, "US": bt._load_us_prices}[market]
    close, high, low, value = loader(min_rows=bt.GATE_MIN_HISTORY + 20)

    valid = close.notna().sum() >= bt.GATE_MIN_HISTORY
    close, high, low, value = (df.loc[:, valid] for df in (close, high, low, value))
    close, high, low = close.ffill(), high.ffill(), low.ffill()

    n = len(close)
    start_t, end_t = bt.GATE_MIN_HISTORY, n - 1
    dates = close.index.to_numpy()

    gate_df, _ = bt._build_trend_template_gate(close, high, low)
    cutoff_idx = max(start_t, end_t - recent_days)

    confirmed, developing = [], []
    for ticker in close.columns:
        c = close[ticker].to_numpy(dtype=float)
        h = high[ticker].to_numpy(dtype=float)
        l = low[ticker].to_numpy(dtype=float)
        v = value[ticker].to_numpy(dtype=float)
        g = gate_df[ticker].to_numpy(dtype=bool)
        if np.isnan(c).all() or not g[start_t:end_t + 1].any():
            continue

        episodes, cur_dev_idx = _find_all_episodes(dates, c, h, l, v, g, start_t, end_t)

        for ep in episodes:
            if ep["ext_idx"] < cutoff_idx:
                continue
            dev_price, ext_price = float(c[ep["dev_idx"]]), float(c[ep["ext_idx"]])
            confirmed.append({
                "ticker": ticker, "market": market,
                "developing_date": str(dates[ep["dev_idx"]])[:10],
                "extended_date": str(dates[ep["ext_idx"]])[:10],
                "missed_return_pct": round((ext_price / dev_price - 1) * 100, 2),
                "recovered_later": ep["recovered_idx"] is not None,
                "recovered_date": (str(dates[ep["recovered_idx"]])[:10]
                                   if ep["recovered_idx"] is not None else None),
            })

        if cur_dev_idx is not None:
            developing.append({
                "ticker": ticker, "market": market,
                "developing_date": str(dates[cur_dev_idx])[:10],
                "days_watching": int(end_t - cur_dev_idx),
            })

    kr_map = _load_kr_name_map() if market == "KR" else {}
    us_tickers = list({r["ticker"] for r in confirmed + developing}) if market == "US" else []
    us_map = _us_name_map(us_tickers) if us_tickers else {}

    def attach_name(r):
        r["name"] = (kr_map.get(r["ticker"], r["ticker"]) if r["market"] == "KR"
                     else us_map.get(r["ticker"], r["ticker"]))
        return r

    confirmed = [attach_name(r) for r in confirmed]
    developing = [attach_name(r) for r in developing]
    confirmed.sort(key=lambda r: r["extended_date"], reverse=True)
    developing.sort(key=lambda r: r["days_watching"], reverse=True)
    return confirmed, developing


def compute_missed_patterns_snapshot(markets=("KR", "US"), recent_days: int = 90) -> dict:
    """
    run_daily.py의 진입점. 시장 하나가 실패해도 나머지는 살린다 —
    이 패널은 부가 정보라, 실패해도 스캔·리포트·대시보드 본체가 죽으면 안 된다.
    """
    all_confirmed, all_developing = [], []
    for mkt in markets:
        try:
            confirmed, developing = _compute_for_market(mkt, recent_days=recent_days)
            all_confirmed.extend(confirmed)
            all_developing.extend(developing)
            print(f"[놓친패턴] [{mkt}] 이미 놓침 {len(confirmed)}건(최근 {recent_days}일 내), "
                  f"관찰 중 {len(developing)}건")
        except Exception as e:
            print(f"[놓친패턴] [{mkt}] 건너뜀: {str(e)[:200]}")

    return {"confirmed": all_confirmed, "developing": all_developing}


if __name__ == "__main__":
    import json
    snap = compute_missed_patterns_snapshot()
    print(json.dumps(snap, ensure_ascii=False, indent=2)[:3000])
