# -*- coding: utf-8 -*-
"""
_adjust_splits() v3 검증 (클러스터 + 거래중단 이중 조건).
auto_sepa_new 폴더에서 실행: python3 verify_split_adjust_v3.py
"""
import pandas as pd
from kr_data_fdr import _adjust_splits

df = pd.read_parquet("cache/kr_krx_daily.parquet")
df["date"] = pd.to_datetime(df["date"])
global_last = df["date"].max()
print(f"캐시 최신 날짜: {global_last.date()}")

adjusted, stats = _adjust_splits(df)

print()
print("=== 보정 통계 ===")
for k, v in stats.items():
    if k == "delisting_suspect_tickers":
        print(f"  {k}: {len(v)}종목 -> {v}")
    else:
        print(f"  {k}: {v}")

print()
print("=== 정리매매 의심 종목별 마지막 거래일 (전부 최신일보다 이전이어야 함) ===")
for t in stats["delisting_suspect_tickers"]:
    last = df[df.ticker == t]["date"].max()
    gap = (global_last - last).days
    print(f"  {t}: 마지막 거래일 {last.date()}  (최신일과 {gap}일 차이)")

print()
print("=== 지난번 오탐 4종목 재확인 (정리매매 목록에서 빠졌어야 함) ===")
for t in ["046070", "083660", "182400", "368970"]:
    in_list = t in stats["delisting_suspect_tickers"]
    raw = df[df.ticker == t].sort_values("date").tail(6)[["date", "close"]]
    adj = adjusted[adjusted.ticker == t].sort_values("date").tail(6)[["date", "close"]]
    print(f"-- {t} (정리매매 목록에 있음: {in_list}) --")
    print("  원본:", raw["close"].tolist())
    print("  보정:", adj["close"].tolist())

print()
print("=== 096610 재확인 (거래중단 상태라 여전히 목록에 남아야 정상) ===")
t = "096610"
print("목록 포함:", t in stats["delisting_suspect_tickers"])
print("마지막 거래일:", df[df.ticker == t]["date"].max().date())

print()
print("=== 보정 후 잔여 이상치 (정리매매 종목 제외하고 검사) ===")
chk = adjusted[~adjusted.ticker.isin(stats["delisting_suspect_tickers"])].copy()
chk = chk.sort_values(["ticker", "date"])
chk["ret"] = chk.groupby("ticker")["close"].pct_change()
still = chk[chk.ret.abs() > 0.32]
print(f"잔여 이상치: {len(still)}건")
if len(still):
    print(still[["date", "ticker", "close", "ret"]].sort_values("date", ascending=False)
          .to_string(index=False))

print()
print("=== 대한제분(001130) 회귀 테스트 ===")
a = adjusted[(adjusted.ticker == "001130") & (adjusted.date.between("2026-05-14", "2026-05-20"))]
print(a[["date", "close"]].to_string(index=False))
