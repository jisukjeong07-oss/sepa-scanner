"""
export_latest.py — 하루치 스캔 결과 전체를 latest.json 하나로 내보낸다.

Claude(또는 다른 도구)가 PDF 6개를 받지 않고 이 파일 하나만 읽으면 되도록
만든 것이다. 관찰 중·이미 놓침·시장 폭·매크로까지 한 파일에 담는다.

run_daily.py 안에서 쓰는 법 (make_dashboard.build(...) 바로 뒤):

    import export_latest
    export_latest.write_latest(
        csv_path,
        out_dir=make_dashboard.HIST_DIR,     # history/ — 커밋되는 폴더여야 한다
        stage2_csv=stage2_path,
        session=session,
        data_as_of=scan_stamp,
        missed_patterns=missed_patterns_snapshot,
        breadth=breadth_snapshot,
        macro=macro_snapshot,
    )

왜 history/ 인가:
    GitHub Actions 러너는 매 실행마다 새로 뜬다. 커밋되지 않는 폴더에 쓰면
    다음 실행 때 어제 파일이 없어 prev_price(전일 종가)를 채울 수 없다.
    history/ 는 daily.yml의 Push 스텝이 `git add -A history/` 로 통째로
    커밋하므로 추가 설정 없이 그대로 쌓인다. breadth/ 가 매일 사라지던
    문제(2026-09-14)와 같은 함정이다.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

KST = timezone(timedelta(hours=9))
FILENAME = "latest.json"

# 원본 컬럼 -> latest.json 키 (짧게 줄여 파일 크기를 절반으로)
FIELDS = {
    "ticker": "tic", "name": "name", "market": "mkt", "sector": "sector",
    "price": "price", "RS": "rs", "conditions_met": "cond", "PASS": "pass",
    "vs_52w_high_%": "high52", "vs_MA50_%": "ma50dev", "vs_MA200_%": "ma200dev",
    "MA200_slope_%": "slope200", "dev_days": "devdays", "dev_trend": "devtrend",
    "avg_turnover_20d": "turnover20d", "trade_value_today": "value_today",
    "market_cap": "mcap", "turnover_ratio": "turn",
    "vcp_status": "vcp", "pivot_price": "pivot", "stop_price": "stop",
    "pct_from_pivot": "pivot_pct", "risk_pct": "risk",
    "contraction_count": "legs", "is_tightening": "tight", "vol_dryup": "dryup",
    "vol_ratio": "volratio", "vol_ratio_label": "vollabel",
    "F_PASS": "f_pass", "SEPA12_PASS": "sepa12",
    "순이익증가율_YoY_%": "eps_yoy", "매출증가율_YoY_%": "rev_yoy", "이익가속": "accel",
}

# object dtype 으로 읽히면 숫자가 문자열로 나가므로 강제 변환할 컬럼
NUMERIC = {"price", "RS", "conditions_met", "vs_52w_high_%", "vs_MA50_%",
           "vs_MA200_%", "MA200_slope_%", "dev_days", "avg_turnover_20d",
           "trade_value_today", "market_cap", "turnover_ratio", "pivot_price",
           "stop_price", "pct_from_pivot", "risk_pct", "contraction_count",
           "vol_ratio", "순이익증가율_YoY_%", "매출증가율_YoY_%"}

STAGE2_EXTRA = ["순이익증가율_YoY_%", "매출증가율_YoY_%", "이익가속", "F_PASS", "SEPA12_PASS", "최근분기"]


def _clean(v):
    """JSON이 못 담는 값(NaN, inf, numpy 타입)을 정리한다."""
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        # vs_52w_low_% 에 inf 가 들어오는 사례가 실제로 있었다(금호전기)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(v, (pd.Timestamp, datetime)):
        return str(v)[:10]
    s = str(v)
    return None if s in ("nan", "NaT", "None", "") else s


def _jsonable(obj):
    """중첩 딕셔너리·리스트 안의 numpy·NaN 까지 재귀로 정리한다."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return _clean(obj)


def _normalize_ticker(df: pd.DataFrame) -> pd.DataFrame:
    if "ticker" not in df.columns:
        if "Unnamed: 0" in df.columns:
            df = df.rename(columns={"Unnamed: 0": "ticker"})
        else:
            df = df.reset_index().rename(columns={"index": "ticker"})
    df["ticker"] = df["ticker"].astype(str).str.strip()
    # 한국 종목코드 앞자리 0이 날아가는 문제 (1210 -> 001210)
    if "market" in df.columns:
        kr = df["market"].astype(str).eq("KR") & df["ticker"].str.fullmatch(r"\d{1,6}")
        df.loc[kr, "ticker"] = df.loc[kr, "ticker"].str.zfill(6)
    return df


def _pick_rows(df: pd.DataFrame) -> pd.DataFrame:
    """전체 3,000여 종목 중 실제로 볼 필요가 있는 행만 남긴다."""
    cond = pd.to_numeric(df.get("conditions_met"), errors="coerce").fillna(0)
    passed = df.get("PASS", pd.Series(False, index=df.index)).astype(str).str.lower().eq("true")
    vcp = df.get("vcp_status", pd.Series("", index=df.index)).astype(str)
    return df[passed | (cond >= 7) | vcp.isin(["developing", "entry_ready", "extended"])].copy()


def _prev_prices(path: str, new_data_date):
    """
    전일 종가 맵을 만든다.

    같은 날 AM·PM 두 번 도는 구조라, 직전 파일을 무조건 "어제"로 쓰면
    AM 회차가 같은 날 PM과 비교되어 등락이 0으로 나온다. 데이터 기준일이
    바뀌었을 때만 갱신하고, 같으면 이전 맵을 그대로 물려준다.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            old = json.load(fp)
    except Exception:
        return {}
    rows = old.get("rows", [])
    key = lambda r: f"{r.get('mkt')}:{r.get('tic')}"
    if old.get("data_date") and new_data_date and str(old["data_date"]) != str(new_data_date):
        return {key(r): r.get("price") for r in rows if r.get("tic")}
    return {key(r): r.get("prev_price") for r in rows if r.get("tic")}


def write_latest(csv_path, out_dir="history", stage2_csv=None, session="",
                 data_as_of=None, missed_patterns=None, breadth=None, macro=None,
                 sector_status=None):
    """스캔 CSV + 부가 스냅샷을 latest.json 한 파일로 저장하고 경로를 돌려준다."""
    df = _normalize_ticker(pd.read_csv(csv_path, dtype={"Unnamed: 0": str}))

    # 2단계 펀더멘털을 티커 기준으로 붙인다(있을 때만)
    if stage2_csv and os.path.exists(stage2_csv):
        try:
            s2 = _normalize_ticker(pd.read_csv(stage2_csv, dtype={"Unnamed: 0": str}))
            cols = ["ticker"] + [c for c in STAGE2_EXTRA if c in s2.columns]
            df = df.merge(s2[cols], on="ticker", how="left", suffixes=("", "_s2"))
        except Exception as e:
            print(f"[export_latest] 2단계 병합 건너뜀: {e}")

    data_date = str(data_as_of) if data_as_of else _clean(
        df["data_date"].iloc[0]) if "data_date" in df.columns and len(df) else None

    out_path = os.path.join(out_dir, FILENAME)
    prev = _prev_prices(out_path, data_date)

    sel = _pick_rows(df)
    for col in NUMERIC & set(sel.columns):
        sel[col] = pd.to_numeric(sel[col], errors="coerce")

    rows = []
    for _, r in sel.iterrows():
        row = {dst: _clean(r[src]) for src, dst in FIELDS.items() if src in sel.columns}
        row["prev_price"] = _clean(prev.get(f"{row.get('mkt')}:{row.get('tic')}"))
        rows.append(row)

    cond = pd.to_numeric(df.get("conditions_met"), errors="coerce").fillna(0)
    passed = df.get("PASS", pd.Series(False, index=df.index)).astype(str).str.lower().eq("true")
    mk = df["market"].astype(str) if "market" in df.columns else pd.Series("", index=df.index)

    def n(mask, market=None):
        return int((mask & mk.eq(market)).sum()) if market else int(mask.sum())

    payload = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "session": {"AM": "장전", "PM": "장마감"}.get(session, session or "수동"),
        "data_date": data_date,
        "universe": {"total": int(len(df)), "kr": n(mk.eq("KR")), "us": n(mk.eq("US"))},
        "summary": {
            "kr_pass": n(passed, "KR"), "kr_cond7": n(cond.eq(7), "KR"),
            "us_pass": n(passed, "US"), "us_cond7": n(cond.eq(7), "US"),
            "rows_exported": len(rows),
        },
        # developing = 관찰 중, confirmed = 이미 놓침. PDF 4개를 대체한다.
        "watching": _jsonable((missed_patterns or {}).get("developing", [])),
        "missed": _jsonable((missed_patterns or {}).get("confirmed", [])),
        "breadth": _jsonable(breadth or []),
        "macro": _jsonable(macro or []),
        "sector_status": _jsonable(sector_status or {}),
        "rows": rows,
    }

    os.makedirs(out_dir, exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)   # 쓰다 실패해도 기존 파일이 깨지지 않게

    print(f"[export_latest] {out_path}  rows={len(rows)}  "
          f"watching={len(payload['watching'])}  missed={len(payload['missed'])}  "
          f"{os.path.getsize(out_path)/1024:.0f}KB")
    return out_path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="history")
    ap.add_argument("--stage2", default=None)
    ap.add_argument("--session", default="")
    ap.add_argument("--data-date", default=None)
    a = ap.parse_args()
    write_latest(a.csv, a.out, a.stage2, a.session, a.data_date)
