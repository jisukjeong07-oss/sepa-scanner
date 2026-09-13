# -*- coding: utf-8 -*-
"""
시장 폭(Market Breadth) 계산 — SEPA 1단계.

[2026-09-12] 첫 버전. 화면(대시보드·PDF) 변경은 포함하지 않는다.
market_breadth.py 는 breadth/{market}_{date}.json 파일을 쓰는 것까지만 한다.

지표 정의는 대화에서 확정한 스펙을 그대로 따른다:
  - tt8_count / tt8_pct : 8조건(1~7 + RS) 모두 충족
  - tt7_count           : 정확히 7조건 충족 (8개 중 7개, RS 포함해서 카운트)
  - trend_ex_rs_pct     : RS를 뺀 조건 1~7 충족 비율 (유니버스 내 %)
  - nh52 / nl52 / net_hl: 52주(252거래일) 신고가·신저가 종목 수, 그 차이
  - above200_pct/above50_pct : 종가가 200일·50일 단순이동평균 위인 비율
  - n_universe / n_valid: 유니버스 전체 종목 수 / 계산에 실제 쓰인 종목 수
  - status              : "live" | "incomplete" (품질 가드에 걸리면 incomplete)
  - source              : "live" | "backfill"

유니버스:
  - 한국: fetch_kr_krx_open()이 반환하는 유니버스에서 정리매매 의심 종목
    (kr_data_fdr._adjust_splits의 delisting_suspects)과 우선주를 제외.
    스캐너의 --min-turnover 같은 유동성 필터는 적용하지 않는다 — 시장 폭은
    시장 전체를 재는 지표라 매일 분모가 흔들리면 안 된다.
  - 미국: sepa_scanner.us_universe()의 S&P500 구성 종목 전체 (yfinance는
    auto_adjust=True라 수정주가로 오므로 한국과 달리 별도 보정이 필요 없다).

신고가 기준은 실제 장중 고가·저가입니다(2026-09-13, kr_krx_daily 캐시에
open/high/low/volume/market_cap/shares 컬럼을 추가하면서 가능해짐).
없는 시장·구버전 캐시는 종가로 자동 근사하고 hilo_basis 필드로 어느
쪽인지 표시합니다.

52주 신고가·200일선 계산에는 252거래일 이력이 필요합니다. 현재 캐시
보유분(약 282영업일)을 기준으로 백필 가능 범위는 약 30일입니다.
"""

import os
import sys
import json
import argparse
import datetime as dt

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
BREADTH_DIR = os.path.join(BASE, "breadth")
os.makedirs(BREADTH_DIR, exist_ok=True)

sys.path.insert(0, BASE)  # kr_data_fdr / sepa_scanner를 같은 폴더에서 찾기 위함

HIST_WINDOW = 252          # 52주 신고가·신저가, 200일선 계산에 필요한 최소 이력
DEFAULT_BACKFILL = 30      # 캐시 282영업일 - 252 ≈ 30일
QUALITY_MIN_RATIO = 0.90   # n_valid가 전일의 이 비율 미만이면 incomplete


# ═════════════════════════════════════════════════════════════
# 데이터 로딩
# ═════════════════════════════════════════════════════════════

def _load_kr_data(as_of: dt.date) -> dict:
    """한국 종가·거래대금·시장구분. 정리매매·우선주 제외까지 마친 상태로 반환."""
    from kr_data_fdr import fetch_kr_krx_open, kr_listing
    from sepa_scanner import LOOKBACK_DAYS

    start = (as_of - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    data = fetch_kr_krx_open(start, end)

    suspects = [t for t in data.get("delisting_suspects", []) if t in data["close"].columns]
    if suspects:
        data["close"] = data["close"].drop(columns=suspects)
        data["value"] = data["value"].drop(columns=suspects, errors="ignore")
        data["meta"] = data["meta"].drop(index=suspects, errors="ignore")

    # 우선주 제외 — 종목명이 "...우" 또는 "...우B"로 끝나는 종목.
    # kr_listing()의 SPAC 제외(name.str.contains("스팩|제N호"))와 같은 계열의
    # 이름 기반 휴리스틱이다. 종목코드 마지막 자리로 구분하는 방법도 있지만
    # 예외가 있어(코드 체계가 항상 5로 끝나지 않음), 이름 쪽이 더 안정적이다.
    try:
        listing = kr_listing()
        names = listing["name"].astype(str) if "name" in listing.columns else pd.Series(dtype=str)
        pref = names[names.str.strip().str.match(r".+우[A-Z]?$", na=False)]
        pref_cols = [t for t in pref.index if t in data["close"].columns]
        if pref_cols:
            data["close"] = data["close"].drop(columns=pref_cols)
            data["value"] = data["value"].drop(columns=pref_cols, errors="ignore")
            data["meta"] = data["meta"].drop(index=pref_cols, errors="ignore")
        print(f"[KR breadth] 우선주 {len(pref_cols)}종목 제외 "
              f"(kr_listing 조회 실패 시 0으로 표시됨에 유의)")
    except Exception as e:
        print(f"[KR breadth] 우선주 판별 실패({str(e)[:100]}) — 이번 실행은 우선주 포함된 채로 진행합니다.")

    return data


def _load_us_data(as_of: dt.date) -> dict:
    """미국(S&P500) 종가·거래대금. yfinance auto_adjust=True라 수정주가로 온다."""
    from sepa_scanner import fetch_us, us_universe, LOOKBACK_DAYS

    tickers = us_universe()
    start = (as_of - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    return fetch_us(tickers, start=start, end=end)


# ═════════════════════════════════════════════════════════════
# 지표 계산
# ═════════════════════════════════════════════════════════════

def _rolling_conditions(close: pd.DataFrame, high: pd.DataFrame = None,
                        low: pd.DataFrame = None) -> dict:
    """
    트렌드템플릿 조건 1~7(RS 제외)을 전체 날짜에 대해 한 번에(벡터로) 계산한다.
    스캐너의 screen()은 '최신 하루'만 보지만, 시장 폭은 백필 구간의 여러
    날짜를 함께 계산해야 해서 rolling 계산 결과를 통째로 들고 있다가
    날짜별로 잘라 쓰는 방식을 쓴다.

    [2026-09-13] high/low가 주어지면 52주 고점·저점을 실제 장중 고가·
    저가 기준으로 계산한다(원래 정의가 이쪽이다 — "52주 신고가"는 종가가
    아니라 그 기간의 실제 최고가를 말한다). 없으면(과거 코드 호환, 또는
    아직 high/low를 못 받은 시장) 종가로 근사한다.
    """
    from sepa_scanner import MA200_SLOPE_DAYS, NEAR_HIGH_PCT, ABOVE_LOW_PCT

    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    hi_src = high if high is not None else close
    lo_src = low if low is not None else close
    hi252 = hi_src.rolling(HIST_WINDOW).max()
    lo252 = lo_src.rolling(HIST_WINDOW).min()
    ma200_prev = ma200.shift(MA200_SLOPE_DAYS)

    c1 = (close > ma150) & (close > ma200)
    c2 = ma150 > ma200
    c3 = ma200 > ma200_prev
    c4 = (ma50 > ma150) & (ma50 > ma200)
    c5 = close > ma50
    # 조건 6·7은 미네르비니 정의 그대로 "현재가(종가)"를 "52주 고점·저점
    # (장중 기준)"과 비교한다 — 분모만 high/low, 분자는 close.
    c6 = (close.div(lo252) - 1) * 100 >= ABOVE_LOW_PCT
    c7 = (close.div(hi252) - 1) * 100 >= -NEAR_HIGH_PCT

    return {"c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5, "c6": c6, "c7": c7,
            "ma50": ma50, "ma200": ma200, "hi252": hi252, "lo252": lo252}


def _breadth_for_date(close: pd.DataFrame, target_date: pd.Timestamp,
                      high: pd.DataFrame = None, low: pd.DataFrame = None) -> dict:
    """
    target_date 하루치 시장 폭 지표. close는 해당 시장(KOSPI/KOSDAQ/US 등)의
    wide 종가 DataFrame(전체 이력 포함, target_date까지만 있어도 됨).
    high/low가 있으면 같은 컬럼 구성의 wide DataFrame이어야 한다.
    """
    from sepa_scanner import MIN_RS, rs_rating

    if target_date not in close.index:
        return None
    pos = close.index.get_loc(target_date)
    sub_full = close.iloc[:pos + 1]

    n_universe = close.shape[1]

    # 252일 이력이 없는 종목(신규상장 등)은 이번 계산에서 제외
    enough_hist = sub_full.notna().sum() >= HIST_WINDOW
    cols = enough_hist[enough_hist].index
    n_valid = len(cols)

    if n_valid == 0:
        return {"n_universe": int(n_universe), "n_valid": 0, "status": "incomplete"}

    sub = sub_full[cols].ffill()
    sub_hi = high[cols].iloc[:pos + 1].ffill() if high is not None else None
    sub_lo = low[cols].iloc[:pos + 1].ffill() if low is not None else None
    cond = _rolling_conditions(sub, sub_hi, sub_lo)
    rs = rs_rating(sub)   # 이 날짜, 이 유니버스 안에서의 RS 백분위

    c1_t = cond["c1"].iloc[-1]; c2_t = cond["c2"].iloc[-1]; c3_t = cond["c3"].iloc[-1]
    c4_t = cond["c4"].iloc[-1]; c5_t = cond["c5"].iloc[-1]; c6_t = cond["c6"].iloc[-1]
    c7_t = cond["c7"].iloc[-1]
    c8_t = rs >= MIN_RS

    conds = pd.concat([c1_t, c2_t, c3_t, c4_t, c5_t, c6_t, c7_t, c8_t], axis=1)
    conds.columns = [f"c{i}" for i in range(1, 9)]
    met = conds.sum(axis=1)

    tt8 = int((met == 8).sum())
    tt7 = int((met == 7).sum())
    trend_ex_rs = (c1_t & c2_t & c3_t & c4_t & c5_t & c6_t & c7_t)
    trend_ex_rs_pct = round(100.0 * trend_ex_rs.sum() / n_valid, 2)

    px = sub.iloc[-1]
    hi = cond["hi252"].iloc[-1]
    lo = cond["lo252"].iloc[-1]
    ma200 = cond["ma200"].iloc[-1]
    ma50 = cond["ma50"].iloc[-1]

    # [2026-09-13] "오늘 52주 신고가/신저가를 찍었는가"는 오늘의 실제
    # 고가·저가로 판정한다(종가로 판정하면 장중 신고가를 찍고 밀려서
    # 마감한 종목을 놓친다). high/low가 없는 경우에만 종가로 대체한다.
    today_hi = sub_hi.iloc[-1] if sub_hi is not None else px
    today_lo = sub_lo.iloc[-1] if sub_lo is not None else px
    nh52 = int((today_hi >= hi).sum())
    nl52 = int((today_lo <= lo).sum())

    above200_mask = ma200.notna()
    above200_pct = (round(100.0 * (px[above200_mask] > ma200[above200_mask]).sum()
                           / above200_mask.sum(), 2) if above200_mask.sum() else None)
    above50_pct = round(100.0 * (px > ma50).sum() / n_valid, 2)

    return {
        "tt8_count": tt8,
        "tt8_pct": round(100.0 * tt8 / n_valid, 2),
        "tt7_count": tt7,
        "trend_ex_rs_pct": trend_ex_rs_pct,
        "nh52": nh52,
        "nl52": nl52,
        "net_hl": nh52 - nl52,
        "above200_pct": above200_pct,
        "above50_pct": above50_pct,
        "n_universe": int(n_universe),
        "n_valid": int(n_valid),
        "status": "live",
        "hilo_basis": "intraday" if sub_hi is not None else "close",
    }


# ═════════════════════════════════════════════════════════════
# 저장
# ═════════════════════════════════════════════════════════════

def _record_path(market: str, data_date: str) -> str:
    return os.path.join(BREADTH_DIR, f"{market}_{data_date}.json")


def _write_record(market: str, data_date: str, record: dict, source: str):
    record = dict(record)
    record["market"] = market
    record["data_date"] = data_date
    record["source"] = source
    record["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(_record_path(market, data_date), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def _apply_quality_guard(records_by_date: list, market: str):
    """
    n_valid가 직전(날짜순 이전) 기록의 90% 미만이면 status를 incomplete로
    바꾸고, 지표 값은 화면에서 '데이터 부족'으로 처리할 수 있도록 남겨는
    두되 신뢰하지 말라는 표시만 한다 (값 자체를 0으로 덮어쓰지 않는다).
    """
    prev_valid = None
    for date_str, record in records_by_date:
        if record.get("n_valid", 0) == 0:
            record["status"] = "incomplete"
            continue
        if prev_valid is not None and record["n_valid"] < QUALITY_MIN_RATIO * prev_valid:
            record["status"] = "incomplete"
            print(f"  [품질가드] {market} {date_str}: n_valid {record['n_valid']:,} "
                  f"(직전 {prev_valid:,}의 {record['n_valid']/prev_valid:.0%}) → incomplete 처리")
        prev_valid = record["n_valid"]


# ═════════════════════════════════════════════════════════════
# 실행
# ═════════════════════════════════════════════════════════════

def run(as_of: str = None, backfill: int = DEFAULT_BACKFILL, markets=("KR", "US")):
    """
    as_of: 'YYYYMMDD'. 안 주면 오늘.
    backfill: as_of 포함해서 최근 며칠을 계산할지. 1이면 당일만(평소 일일 실행),
              30이면 최근 30거래일을 전부 다시 계산해 breadth/ 폴더를 채운다
              (초기 백필용, 매일 실행할 땐 1로 충분).
    """
    today = (dt.datetime.strptime(as_of, "%Y%m%d").date() if as_of else dt.date.today())

    if "KR" in markets:
        print("[KR] 데이터 로딩...")
        kr = _load_kr_data(today)
        kr_close, kr_meta = kr["close"], kr["meta"]
        kr_high, kr_low = kr.get("high"), kr.get("low")
        if kr_high is None:
            print("[KR] 주의: 캐시에 고가·저가가 없어 종가로 52주 고점·저점을 근사합니다. "
                  "(kr_data_fdr.py 스키마 확장 후 재수집하면 사라지는 경고입니다)")

        groups = {
            "KOSPI": kr_meta.index[kr_meta["market"] == "KOSPI"],
            "KOSDAQ": kr_meta.index[kr_meta["market"] == "KOSDAQ"],
            "KR_TOTAL": kr_meta.index,
        }

        all_dates = kr_close.index[kr_close.index <= pd.Timestamp(today)]
        target_dates = list(all_dates[-backfill:]) if backfill > 0 else [all_dates[-1]]

        for market_label, cols in groups.items():
            cols = [c for c in cols if c in kr_close.columns]
            sub_close = kr_close[cols]
            sub_hi = kr_high[cols] if kr_high is not None else None
            sub_lo = kr_low[cols] if kr_low is not None else None
            print(f"[{market_label}] {len(cols)}종목, {len(target_dates)}일 계산...")

            records = []
            for i, d in enumerate(target_dates):
                rec = _breadth_for_date(sub_close, d, sub_hi, sub_lo)
                if rec is None:
                    continue
                date_str = d.strftime("%Y%m%d")
                source = "live" if i == len(target_dates) - 1 else "backfill"
                records.append((date_str, rec, source))

            recs_for_guard = [(ds, rec) for ds, rec, _ in records]
            _apply_quality_guard(recs_for_guard, market_label)

            for date_str, rec, source in records:
                _write_record(market_label, date_str, rec, source)
            if records:
                last_date, last_rec, _ = records[-1]
                print(f"  [{market_label}] {last_date}: tt8={last_rec.get('tt8_count')} "
                      f"tt7={last_rec.get('tt7_count')} "
                      f"above200%={last_rec.get('above200_pct')} "
                      f"status={last_rec.get('status')}")

    if "US" in markets:
        print("[US] 데이터 로딩...")
        us = _load_us_data(today)
        us_close = us["close"]
        us_high, us_low = us.get("high"), us.get("low")
        if us_high is None:
            print("[US] 주의: 고가·저가를 못 받아 종가로 52주 고점·저점을 근사합니다.")

        all_dates = us_close.index[us_close.index <= pd.Timestamp(today)]
        target_dates = list(all_dates[-backfill:]) if backfill > 0 else [all_dates[-1]]

        print(f"[SP500] {us_close.shape[1]}종목, {len(target_dates)}일 계산...")
        records = []
        for i, d in enumerate(target_dates):
            rec = _breadth_for_date(us_close, d, us_high, us_low)
            if rec is None:
                continue
            date_str = d.strftime("%Y%m%d")
            source = "live" if i == len(target_dates) - 1 else "backfill"
            records.append((date_str, rec, source))

        recs_for_guard = [(ds, rec) for ds, rec, _ in records]
        _apply_quality_guard(recs_for_guard, "SP500")

        for date_str, rec, source in records:
            _write_record("SP500", date_str, rec, source)
        if records:
            last_date, last_rec, _ = records[-1]
            print(f"  [SP500] {last_date}: tt8={last_rec.get('tt8_count')} "
                  f"tt7={last_rec.get('tt7_count')} "
                  f"above200%={last_rec.get('above200_pct')} "
                  f"status={last_rec.get('status')}")

    print(f"\n완료. {BREADTH_DIR} 에 저장했습니다.")


def build_history():
    """
    breadth/*.json 을 시장별로 모아 breadth_history.json 하나로 합친다.
    regen_manifest.py와 같은 패턴 — 대시보드가 나중에 이 파일 하나만
    읽으면 되게 하기 위한 준비 단계다 (이번 1단계에서는 대시보드가 아직
    이 파일을 쓰지 않는다).
    """
    by_market = {}
    for fname in sorted(os.listdir(BREADTH_DIR)):
        if not fname.endswith(".json") or fname == "breadth_history.json":
            continue
        with open(os.path.join(BREADTH_DIR, fname), encoding="utf-8") as f:
            rec = json.load(f)
        by_market.setdefault(rec["market"], []).append(rec)

    for market in by_market:
        by_market[market].sort(key=lambda r: r["data_date"])

    out_path = os.path.join(BREADTH_DIR, "breadth_history.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(by_market, f, ensure_ascii=False, indent=2)
    print(f"[build_history] {sum(len(v) for v in by_market.values())}건 → {out_path}")


# ═════════════════════════════════════════════════════════════
# 대시보드·리포트용 조회 헬퍼
# ═════════════════════════════════════════════════════════════
# [2026-09-12] run_daily.py가 스캔 직후 market_breadth.run()을 실행한
# 다음, 이 함수들로 방금 만든(또는 예전) 기록을 읽어 make_dashboard.build()
# / make_report.build() 에 그대로 넘긴다. 여기서 다시 계산하지 않고
# breadth/*.json 에 이미 있는 값을 읽기만 한다 — "계산은 한 곳에서만"
# 원칙을 지키기 위함이다.

_MARKET_ORDER = ["KOSPI", "KOSDAQ", "KR_TOTAL", "SP500"]


def _list_dates(market: str) -> list:
    """이 시장의 breadth 기록 날짜(YYYYMMDD) 목록. 오름차순."""
    out = []
    prefix = f"{market}_"
    for fname in os.listdir(BREADTH_DIR):
        if fname.startswith(prefix) and fname.endswith(".json"):
            d = fname[len(prefix):-5]
            if d.isdigit() and len(d) == 8:
                out.append(d)
    return sorted(out)


def load_latest(market: str) -> dict:
    """이 시장의 가장 최근 기록. 없으면 None."""
    dates = _list_dates(market)
    if not dates:
        return None
    with open(_record_path(market, dates[-1]), encoding="utf-8") as f:
        return json.load(f)


def get_dashboard_snapshot(markets=_MARKET_ORDER) -> list:
    """
    make_dashboard.build(breadth_snapshot=...) 에 그대로 넘길 리스트.
    각 항목에 전일 대비 above200_pct 변화(above200_pct_delta)를 붙인다.
    """
    out = []
    for market in markets:
        dates = _list_dates(market)
        if not dates:
            continue
        with open(_record_path(market, dates[-1]), encoding="utf-8") as f:
            rec = json.load(f)
        if len(dates) >= 2:
            with open(_record_path(market, dates[-2]), encoding="utf-8") as f:
                prev = json.load(f)
            if rec.get("above200_pct") is not None and prev.get("above200_pct") is not None:
                rec["above200_pct_delta"] = round(rec["above200_pct"] - prev["above200_pct"], 1)
        out.append(rec)
    return out


def summary_line(markets=("KR_TOTAL", "SP500")) -> str:
    """
    PDF 헤더에 넣을 한 줄 요약. 예:
    "시장 폭 · 한국 전체 200일선 위 21.7% · 신고가 52/신저가 116 · 8조건 101종목 · S&P500 200일선 위 59.4%"
    기록이 없는 시장은 조용히 건너뛴다.
    """
    label = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KR_TOTAL": "한국 전체", "SP500": "S&P500"}
    parts = []
    for market in markets:
        rec = load_latest(market)
        if not rec or rec.get("status") == "incomplete":
            continue
        parts.append(
            f"{label.get(market, market)} 200일선 위 {rec['above200_pct']}% · "
            f"신고가 {rec['nh52']}/신저가 {rec['nl52']} · 8조건 {rec['tt8_count']}종목"
        )
    if not parts:
        return None
    return "시장 폭 · " + " · ".join(parts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SEPA 시장 폭 계산")
    ap.add_argument("--as-of", default=None, help="YYYYMMDD, 안 주면 오늘")
    ap.add_argument("--backfill", type=int, default=1,
                     help="최근 며칠 계산할지. 초기 채우기는 --backfill 30, "
                          "평소 일일 실행은 기본값 1이면 충분합니다.")
    ap.add_argument("--market", default="ALL", choices=["KR", "US", "ALL"])
    ap.add_argument("--build-history", action="store_true",
                     help="계산 후 breadth_history.json도 함께 생성")
    args = ap.parse_args()

    markets = ("KR", "US") if args.market == "ALL" else (args.market,)
    run(as_of=args.as_of, backfill=args.backfill, markets=markets)

    if args.build_history:
        build_history()
