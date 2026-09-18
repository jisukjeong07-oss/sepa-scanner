# -*- coding: utf-8 -*-
"""
top_trades.py — 백테스트 결과(backtest_trades_*.csv) 중 수익률 상위 N개를
종목명까지 붙여서 보기 좋은 표로 정리한다.

[2026-09-17] 만든 이유
- backtest.py가 만드는 CSV는 종목코드만 있고 종목명이 없다(계산 속도를
  위해 굳이 이름까지 안 붙였다). 실전 투자 감각으로 보려면 이름이 있어야
  알아보기 편하니, 여기서 kr_listing 캐시와 대조해서 붙인다.
- 지금 시뮬레이션은 진입 1번·청산 1번 구조라(2차 진입·분할매도 없음),
  "2차 진입" 컬럼은 애초에 만들 데이터가 없다 — 표에도 안 넣는다.
"""
import os
import sys
import glob
import argparse

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "output")


def _latest_trades_csv() -> str:
    files = sorted(glob.glob(os.path.join(OUT_DIR, "backtest_trades_*.csv")))
    if not files:
        raise FileNotFoundError(
            "output/backtest_trades_*.csv 를 못 찾았습니다. backtest.py를 먼저 실행해 주세요."
        )
    return files[-1]


def _load_name_map() -> dict:
    """
    cache/kr_listing.parquet에서 {종목코드: 종목명}을 만든다. 이 캐시가
    없거나 형식이 다르면(컬럼명이 다를 수 있음) 이름 없이 코드만으로
    진행한다 — 이름이 안 붙어도 나머지 정보는 여전히 유효하다.
    """
    path = os.path.join(BASE_DIR, "cache", "kr_listing.parquet")
    if not os.path.exists(path):
        print(f"[경고] {path} 가 없어 종목명 없이 진행합니다.")
        return {}
    try:
        df = pd.read_parquet(path)
        # 이 프로젝트에서 kr_listing 캐시는 보통 종목코드가 인덱스, 이름 컬럼이
        # Name/name/종목명 중 하나다 — 존재하는 걸 찾아서 쓴다.
        name_col = next((c for c in ("Name", "name", "종목명") if c in df.columns), None)
        if name_col is None:
            print(f"[경고] {path} 에서 종목명 컬럼을 못 찾아 코드만 씁니다. "
                  f"실제 컬럼: {list(df.columns)}")
            return {}
        idx = df.index.astype(str).str.zfill(6)
        return dict(zip(idx, df[name_col]))
    except Exception as e:
        print(f"[경고] 종목명 캐시 읽기 실패({e}) — 코드만 씁니다.")
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="집계할 거래 CSV 경로 (생략하면 가장 최근 것)")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    csv_path = args.csv or _latest_trades_csv()
    print(f"[상위거래] 원본: {csv_path}")
    trades = pd.read_csv(csv_path, dtype={"ticker": str})
    trades["ticker"] = trades["ticker"].str.zfill(6)

    name_map = _load_name_map()
    trades["종목명"] = trades["ticker"].map(name_map).fillna("(이름 미확인)")

    top = trades.sort_values("return_pct", ascending=False).head(args.top).copy()
    top.insert(0, "순위", range(1, len(top) + 1))

    out = top[[
        "순위", "종목명", "ticker", "entry_date", "entry_price",
        "exit_date", "exit_price", "return_pct", "holding_days", "exit_reason",
    ]].rename(columns={
        "ticker": "종목코드",
        "entry_date": "진입일",
        "entry_price": "진입가",
        "exit_date": "청산일",
        "exit_price": "청산가",
        "return_pct": "수익률(%)",
        "holding_days": "보유일수",
        "exit_reason": "청산사유",
    })

    stamp = os.path.basename(csv_path).replace("backtest_trades_", "").replace(".csv", "")
    out_path = os.path.join(OUT_DIR, f"top{args.top}_trades_{stamp}.csv")
    out.to_csv(out_path, index=False, encoding="utf-8-sig")

    print()
    print(out.to_string(index=False))
    print()
    print(f"[상위거래] 저장 완료: {out_path}")


if __name__ == "__main__":
    main()
