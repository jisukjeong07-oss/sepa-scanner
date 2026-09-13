# -*- coding: utf-8 -*-
"""
보정 후에도 남은 이상치의 원인을 진단한다.
auto_sepa_new 폴더에서 실행: python3 diagnose_residual.py
"""
import pandas as pd
from kr_data_fdr import _adjust_splits

df = pd.read_parquet("cache/kr_krx_daily.parquet")
df["date"] = pd.to_datetime(df["date"])
adjusted, stats = _adjust_splits(df)

targets = ["096610", "269620", "043090", "060240", "121850"]

for t in targets:
    print("=" * 70)
    print(f"종목 {t}")
    raw = df[df.ticker == t].sort_values("date")
    adj = adjusted[adjusted.ticker == t].sort_values("date")

    # 이상치로 잡힌 날짜 전후로 ±4거래일 윈도우만 출력
    raw2 = raw.copy()
    raw2["ret"] = raw2["close"].pct_change()
    hot_dates = raw2[raw2["ret"].abs() > 0.32]["date"]

    for hd in hot_dates:
        idx = raw2.index[raw2["date"] == hd][0]
        pos = raw2.index.get_loc(idx)
        window = raw2.iloc[max(0, pos - 4): pos + 5][["date", "close", "ret"]]
        adj_window = adj[adj["date"].isin(window["date"])][["date", "close"]]
        merged = window.merge(adj_window, on="date", suffixes=("_원본", "_보정"))
        print(f"-- 이상치 날짜 {hd.date()} 주변 --")
        print(merged.to_string(index=False))
        print()

    # 데이터 전체 개수와 최근 종가 수준도 참고용으로
    print(f"  총 {len(raw)}행, 최근 종가: {raw['close'].iloc[-1]}, "
          f"기간: {raw['date'].min().date()}~{raw['date'].max().date()}")
    print()
