# -*- coding: utf-8 -*-
"""
_adjust_splits() 재검증용 (정리매매 필터 추가 버전).
auto_sepa_new 폴더에서 실행: python3 verify_split_adjust_v2.py
"""
import pandas as pd
from kr_data_fdr import _adjust_splits

df = pd.read_parquet("cache/kr_krx_daily.parquet")
df["date"] = pd.to_datetime(df["date"])

adjusted, stats = _adjust_splits(df)

print("=== 보정 통계 ===")
for k, v in stats.items():
    if k == "delisting_suspect_tickers":
        print(f"  {k}: {len(v)}종목 -> {v}")
    else:
        print(f"  {k}: {v}")

print()
print("=== 정리매매 의심 종목 상세 (조정하지 않고 원본 유지되는지 확인) ===")
for t in stats["delisting_suspect_tickers"]:
    raw_last = df[df.ticker == t].sort_values("date").tail(5)
    adj_last = adjusted[adjusted.ticker == t].sort_values("date").tail(5)
    same = (raw_last["close"].values == adj_last["close"].values).all()
    print(f"-- {t} (원본==보정 유지: {same}) --")
    print(raw_last[["date", "close"]].to_string(index=False))
    print()

print("=== 보정 후 잔여 이상치 재검사 (정리매매 의심 종목은 제외하고 검사) ===")
chk = adjusted[~adjusted.ticker.isin(stats["delisting_suspect_tickers"])].copy()
chk = chk.sort_values(["ticker", "date"])
chk["ret"] = chk.groupby("ticker")["close"].pct_change()
still = chk[chk.ret.abs() > 0.32]
print(f"잔여 이상치: {len(still)}건 (전량 최신일 판정보류分이면 정상)")
if len(still):
    print(still[["date", "ticker", "close", "ret"]].sort_values("date", ascending=False)
          .to_string(index=False))

print()
print("=== 대한제분(001130) 재확인 ===")
a = adjusted[(adjusted.ticker == "001130") & (adjusted.date.between("2026-05-14", "2026-05-20"))]
print(a[["date", "close"]].to_string(index=False))
