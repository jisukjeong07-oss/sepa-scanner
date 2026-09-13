# -*- coding: utf-8 -*-
"""
_adjust_splits() v4 검증 (2단계 분리 후 간섭 버그 수정).
auto_sepa_new 폴더에서 실행: python3 verify_split_adjust_v4.py
"""
import pandas as pd
from kr_data_fdr import _adjust_splits

df = pd.read_parquet("cache/kr_krx_daily.parquet")
df["date"] = pd.to_datetime(df["date"])
global_last = df["date"].max()

adjusted, stats = _adjust_splits(df)

print("=== 보정 통계 ===")
for k, v in stats.items():
    if k == "delisting_suspect_tickers":
        print(f"  {k}: {len(v)}종목")
    else:
        print(f"  {k}: {v}")

print()
print("=== 121850 재확인 (간섭 버그가 있던 종목) ===")
raw = df[df.ticker == "121850"].sort_values("date").tail(6)[["date", "close"]]
adj = adjusted[adjusted.ticker == "121850"].sort_values("date").tail(6)[["date", "close"]]
print("원본:")
print(raw.to_string(index=False))
print("보정:")
print(adj.to_string(index=False))
print("정리매매 목록 포함 여부:", "121850" in stats["delisting_suspect_tickers"])

print()
print("=== 지난번 오탐 4종목 최종 확인 ===")
for t in ["046070", "083660", "182400", "368970"]:
    print(f"  {t}: 정리매매목록={t in stats['delisting_suspect_tickers']}")

print()
print("=== 대한제분(001130) 회귀 테스트 ===")
a = adjusted[(adjusted.ticker == "001130") & (adjusted.date.between("2026-05-14", "2026-05-20"))]
print(a[["date", "close"]].to_string(index=False))

print()
print("=== 보정 후 잔여 이상치 (정리매매 종목 제외) ===")
chk = adjusted[~adjusted.ticker.isin(stats["delisting_suspect_tickers"])].copy()
chk = chk.sort_values(["ticker", "date"])
chk["ret"] = chk.groupby("ticker")["close"].pct_change()
still = chk[chk.ret.abs() > 0.32]
print(f"잔여 이상치: {len(still)}건")
if len(still):
    still2 = still.copy()
    still2["is_latest"] = still2["date"] == global_last
    print(still2[["date", "ticker", "close", "ret", "is_latest"]]
          .sort_values("date", ascending=False).to_string(index=False))
    n_not_latest = (~still2["is_latest"]).sum()
    print(f"\n최신일이 아닌데 남은 건: {n_not_latest}건 (0이어야 정상)")
