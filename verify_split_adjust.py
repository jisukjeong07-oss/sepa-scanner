# -*- coding: utf-8 -*-
"""
_adjust_splits() 검증용 스크립트.
kr_data_fdr.py와 같은 폴더(auto_sepa_new)에서 실행한다.

    python3 verify_split_adjust.py
"""
import pandas as pd
from kr_data_fdr import _adjust_splits

df = pd.read_parquet("cache/kr_krx_daily.parquet")
df["date"] = pd.to_datetime(df["date"])

adjusted, stats = _adjust_splits(df)

print("=== 보정 통계 ===")
for k, v in stats.items():
    print(f"  {k}: {v}")

print()
print("=== 대한제분(001130) 검증 — 3/12 액면분할 공시, 4/22~5/15 정지, 5/18 재상장 ===")
before = df[(df.ticker == "001130") & (df.date.between("2026-05-14", "2026-05-20"))]
after = adjusted[(adjusted.ticker == "001130") & (adjusted.date.between("2026-05-14", "2026-05-20"))]
print("--- 보정 전 ---")
print(before[["date", "close"]].to_string(index=False))
print("--- 보정 후 ---")
print(after[["date", "close"]].to_string(index=False))
print(">> 5/15 대비 5/18 보정 후 종가가 자연스러운 등락(±30% 이내)인지 확인")

print()
print("=== 잔여 이상치 재검사 (보정 후에도 |수익률|>32%가 남아있는지) ===")
chk = adjusted.sort_values(["ticker", "date"]).copy()
chk["ret"] = chk.groupby("ticker")["close"].pct_change()
still = chk[(chk.ret.abs() > 0.32)]
print(f"보정 후 잔여 이상치: {len(still)}건 (전량 최신일 판정보류分이면 정상)")
if len(still):
    print(still[["date", "ticker", "close", "ret"]].sort_values("date", ascending=False).head(10)
          .to_string(index=False))

print()
print("=== 임의 종목 5개 샘플 (보정 전후 최근 3거래일 종가 비교) ===")
sample_tickers = df.ticker.drop_duplicates().sample(5, random_state=0).tolist()
for t in sample_tickers:
    b = df[df.ticker == t].tail(3)[["date", "close"]]
    a = adjusted[adjusted.ticker == t].tail(3)[["date", "close"]]
    print(f"-- {t} --")
    print(" 전:", b["close"].tolist(), " 후:", a["close"].tolist())
