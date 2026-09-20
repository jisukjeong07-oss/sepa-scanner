# -*- coding: utf-8 -*-
"""
backtest.py — VCP "진입가능" 신호의 백테스트 (8조건 게이트 + 포트폴리오 시뮬레이션).

[2026-09-17] v3에서 추가된 것
- MAX_HOLD_DAYS를 CLI 옵션(--max-hold)으로 뺐다. v2 결과(상위 10건 중 8건이
  60일 만기청산)를 보니, 60일이 수익을 일찍 자르고 있을 가능성이 있어서
  90·120일도 쉽게 비교해볼 수 있어야 했다.
- 포트폴리오 시뮬레이션(simulate_portfolio) 추가. v2까지는 "신호 하나당
  평균 몇 % 벌었나"만 봤는데, 이건 실전과 거리가 있다 — 실제로는 동시에
  들고 갈 수 있는 종목 수가 제한되고(자금이 한정돼 있으니), 신호가
  자금보다 많이 뜨면 일부는 못 산다. 그 현실을 반영해서 "이 기간에
  실제로 이 규칙대로 매매했다면 계좌가 얼마나 불었을까"를 재현한다.
    - 동시 최대 보유 종목 수(max_concurrent)를 정해두고, 그 슬롯이 찼으면
      새 신호가 떠도 못 산다(재시도 없음 — 그날 놓친 기회로 처리).
    - 같은 날 신호가 슬롯보다 많으면, 그날 RS가 높은 종목부터 채운다
      (추세가 더 강한 쪽을 우선한다는 미네르비니 철학과 일치).
    - 포지션 크기는 "진입 시점의 총자산 ÷ max_concurrent" — 균등 배분,
      복리로 자연스럽게 자산이 늘면 다음 포지션도 커진다.
    - 매일 보유 종목을 그날 종가로 평가(mark-to-market)해서 계좌 평가액을
      추적하고, 이걸로 최종 수익률·MDD(최대낙폭)를 계산한다.
"""
import os
import sys
import time
import json
import argparse
import datetime as dt

import numpy as np
import pandas as pd


# ── .env 자동 로딩 ────────────────────────────────────────────
def _load_env():
    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        from dotenv import load_dotenv
        load_dotenv(_path)
        return
    except ImportError:
        pass
    if not os.path.exists(_path):
        return
    with open(_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


_load_env()
# ─────────────────────────────────────────────────────────────

import vcp as _vcp
from sepa_scanner import (
    MIN_RS, NEAR_HIGH_PCT, ABOVE_LOW_PCT, MA200_SLOPE_DAYS,
)

STOP_EXIT = "손절"
MA50_EXIT = "추세이탈"
TIMEOUT_EXIT = "만기청산"
DATA_END_EXIT = "데이터끝(미청산)"

GATE_MIN_HISTORY = 252
CACHE_CALENDAR_DAYS_BACK = 800  # 넉넉하게 2년+ (영업일로 대략 550~560일)
DEFAULT_MAX_HOLD_DAYS = 60

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def _load_kr_prices(min_rows: int):
    """
    기존 kr_data_fdr.py의 캐시 읽기 경로를 그대로 재사용한다. 캐시가 이미
    이 범위를 덮으면(두 번째 실행부터는 보통 그렇다) 새 API 호출 없이
    캐시만 읽어온다.
    """
    from kr_data_fdr import fetch_kr_krx_open

    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=CACHE_CALENDAR_DAYS_BACK)).strftime("%Y%m%d")
    data = fetch_kr_krx_open(start, end)
    close, high, low, value = data["close"], data["high"], data["low"], data["value"]

    if len(close) < min_rows:
        raise RuntimeError(
            f"캐시 영업일이 {len(close)}일뿐입니다(최소 {min_rows}일 필요). "
            f"KRX 쪽에서 과거분을 충분히 못 받아왔을 수 있습니다 — 로그의 "
            f"[빈 응답]·[warn] 줄을 확인해 주세요."
        )
    return close, high, low, value


def _load_us_prices(min_rows: int):
    """
    [2026-09-20] sepa_scanner.fetch_us()를 us_data_cache.fetch_us_cached()로
    감싸서 쓴다. 예전엔 캐시가 없어 이 함수를 부를 때마다(백테스트,
    놓친패턴 계산 등) 매번 500종목을 새로 받았는데, 메인 스캔이 이미
    같은 날 한 번 받아둔 걸 재사용하지 않고 또 받으면서 yfinance가
    느려지고(2배 시간) 내부 캐시 충돌로 일부 종목이 실패하는 문제
    (OperationalError: unable to open database file)가 실제로 있었다.
    이제 하루 안에서는 먼저 받은 쪽의 결과를 그대로 재사용한다.
    """
    from sepa_scanner import us_universe
    from us_data_cache import fetch_us_cached

    tickers = us_universe()
    print(f"[백테스트] 미국 유니버스 {len(tickers)}종목 · 2년치 시세 확보 중 "
          f"(오늘 이미 받은 게 있으면 재사용합니다)...")
    data = fetch_us_cached(tickers, period="2y")
    close, high, low, value = data["close"], data["high"], data["low"], data["value"]

    if len(close) < min_rows:
        raise RuntimeError(
            f"받아온 영업일이 {len(close)}일뿐입니다(최소 {min_rows}일 필요). "
            f"yfinance 응답이 예상보다 짧았을 수 있습니다."
        )
    return close, high, low, value


def _build_trend_template_gate(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame):
    """
    sepa_scanner.screen()의 8조건 공식을 "모든 날"에 대해 한 번에(벡터화)
    계산한다. (gate, rs) 튜플을 반환한다 — gate는 8조건 통과 여부(불리언),
    rs는 그날그날의 RS 값(같은 날 여러 신호가 뜰 때 우선순위 판단에 쓴다).
    rolling·pct_change·rank(axis=1)은 전부 그 행(그 날짜)까지의 값만
    쓰므로 미래 데이터가 섞이지 않는다.
    """
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    ma200_prev = ma200.shift(MA200_SLOPE_DAYS)
    hi52 = high.rolling(252).max()
    lo52 = low.rolling(252).min()

    ret3, ret6, ret9, ret12 = (close.pct_change(d) for d in (63, 126, 189, 252))
    score = 2 * ret3 + ret6 + ret9 + ret12
    rs = (score.rank(axis=1, pct=True) * 98 + 1).round(0)

    c1 = (close > ma150) & (close > ma200)
    c2 = ma150 > ma200
    c3 = ma200 > ma200_prev
    c4 = (ma50 > ma150) & (ma50 > ma200)
    c5 = close > ma50
    c6 = (close / lo52 - 1) * 100 >= ABOVE_LOW_PCT
    c7 = (close / hi52 - 1) * 100 >= -NEAR_HIGH_PCT
    c8 = rs >= MIN_RS

    gate = (c1 & c2 & c3 & c4 & c5 & c6 & c7 & c8).fillna(False)
    return gate, rs


def _find_trades_for_ticker(dates, close, high, low, value, gate, rs, start_t, entry_end_t,
                             data_end_t, max_hold_days):
    """
    한 종목을 start_t~entry_end_t 구간에서 하루씩 되감으며, "그날 8조건을
    통과했는가"(gate)와 "그날 VCP가 entry_ready인가"를 같이 확인한다.
    이미 포지션 보유 중엔 새 신호를 찾지 않는다. entry_idx/exit_idx(정수
    날짜 인덱스)와 진입 시점 RS도 같이 남긴다 — 포트폴리오 시뮬레이션이
    여러 종목을 날짜 기준으로 정렬·우선순위 매길 때 쓴다.

    [2026-09-18] entry_end_t와 data_end_t를 분리한 이유 — 예전엔 "새 신호를
    찾는 마지막 날"과 "청산을 확인하는 마지막 날"이 같은 값(entry_end_t)
    이었다. run_backtest()가 entry_end_t를 "n-1-max_hold_days"로 미리
    reserve해두는 건 "가장 늦게 진입한 거래도 만기까지 관찰할 여유를
    준다"는 뜻인데, 정작 이 함수의 청산 확인 루프가 그 여유분(reserve한
    구간)을 안 쓰고 entry_end_t에서 멈춰버려서, 구간 끝자락에 진입한
    거래들이 만기를 다 못 채우고 "데이터끝(미청산)"으로 잘못 잘렸다.
    실사례: 만기 60일로 지정했는데도 324건 중 31건이 이 사유로 나왔다
    (60일을 지정했으면 이 사유 자체가 나오면 안 된다). data_end_t(진짜
    마지막 날)까지 청산 확인을 계속하도록 분리해서 고친다.

    max_hold_days가 None이면 만기청산 규칙 자체를 안 쓴다(미네르비니
    원칙엔 고정 보유일수 규칙이 없다 — 손절가·추세이탈로만 청산). 이
    경우 data_end_t까지 가도 포지션이 안 끝났으면, 그건 "청산된 거래"가
    아니라 "데이터가 거기서 끝나서 결과를 모르는 거래"이므로 DATA_END_EXIT
    로 따로 표시한다 — 실제 청산 사유와 섞이면 안 된다.
    """
    ma50 = pd.Series(close).rolling(50).mean().to_numpy()

    trades = []
    t = start_t
    in_position = False
    entry_idx = entry_price = stop_price = None

    while t <= data_end_t:
        if not in_position:
            if t <= entry_end_t and gate[t]:
                res = _vcp.analyze_ticker(high[:t + 1], low[:t + 1], close[:t + 1], value[:t + 1])
                if res and res.get("vcp_status") == "entry_ready":
                    entry_idx = t
                    entry_price = float(close[t])
                    stop_price = float(res["stop_price"])
                    in_position = True
        else:
            days_held = t - entry_idx
            px = float(close[t])
            reason = None
            if px < stop_price:
                reason = STOP_EXIT
            elif not np.isnan(ma50[t]) and px < ma50[t]:
                reason = MA50_EXIT
            elif max_hold_days is not None and days_held >= max_hold_days:
                reason = TIMEOUT_EXIT

            if reason:
                trades.append({
                    "entry_idx": entry_idx,
                    "exit_idx": t,
                    "entry_date": str(dates[entry_idx]),
                    "exit_date": str(dates[t]),
                    "entry_price": entry_price,
                    "exit_price": px,
                    "return_pct": round((px / entry_price - 1) * 100, 2),
                    "holding_days": days_held,
                    "exit_reason": reason,
                    "rs_at_entry": float(rs[entry_idx]) if not np.isnan(rs[entry_idx]) else None,
                })
                in_position = False
        t += 1

    if in_position:
        # data_end_t(진짜 마지막 날)까지 가도 손절도 추세이탈도 만기도 안
        # 걸린 채 살아있던 포지션. 실제로 팔린 게 아니므로, 그 시점 가격으로
        # "지금 이 시점 기준 평가상 얼마인지"만 참고용으로 기록하고 사유를
        # 명확히 구분한다. max_hold_days가 설정돼 있었다면 entry_end_t가
        # 이미 충분한 여유를 reserve해뒀으므로 이 분기는 사실상 발생하지
        # 않아야 정상이다 — 발생한다면 --no-timeout 모드이거나 데이터
        # 자체가 예상보다 짧은 경우다.
        px = float(close[data_end_t])
        trades.append({
            "entry_idx": entry_idx,
            "exit_idx": data_end_t,
            "entry_date": str(dates[entry_idx]),
            "exit_date": str(dates[data_end_t]),
            "entry_price": entry_price,
            "exit_price": px,
            "return_pct": round((px / entry_price - 1) * 100, 2),
            "holding_days": data_end_t - entry_idx,
            "exit_reason": DATA_END_EXIT,
            "rs_at_entry": float(rs[entry_idx]) if not np.isnan(rs[entry_idx]) else None,
        })

    return trades


def run_backtest(market: str = "KR", max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
                  max_tickers: int = None, progress_every: int = 200):
    """반환값: (trades_df, close_df, start_t, data_end_t) — close_df·인덱스
    범위는 포트폴리오 시뮬레이션에서 그대로 재사용한다(다시 안 받아오려고).
    data_end_t(진짜 마지막 날)를 반환하는 이유 — 만기가 설정된 거래도
    entry_end_t(신호 탐색 마지막 날) 이후, 즉 reserve해둔 여유 구간에서
    실제로 청산될 수 있다. 포트폴리오 시뮬레이션이 그 청산을 놓치지 않고
    현금을 회수하려면 data_end_t까지 날짜를 훑어야 한다.
    max_hold_days=None이면 만기청산 규칙을 안 쓴다 — 이 경우 데이터 끝까지
    구간을 다 쓴다(뒤쪽에 결과 추적용 여유를 안 떼어놓아도 되므로).
    market: "KR" 또는 "US" — 8조건 게이트·VCP 로직은 시장과 무관하게
    완전히 동일하다(가격 배열만 넣으면 되므로). 데이터를 어디서 받아오는지만
    다르다."""
    loader = {"KR": _load_kr_prices, "US": _load_us_prices}[market]
    _reserve = max_hold_days if max_hold_days is not None else 0
    close, high, low, value = loader(min_rows=GATE_MIN_HISTORY + _reserve + 20)

    valid = close.notna().sum() >= GATE_MIN_HISTORY
    close, high, low, value = (df.loc[:, valid] for df in (close, high, low, value))
    close, high, low = close.ffill(), high.ffill(), low.ffill()

    n = len(close)
    start_t = GATE_MIN_HISTORY
    data_end_t = n - 1                    # 진짜 마지막 날 — 청산 확인·포트폴리오 시뮬레이션용
    entry_end_t = n - 1 - _reserve        # 신호 탐색 마지막 날 — 새 진입은 여기까지만
    if entry_end_t < start_t:
        raise RuntimeError(
            f"신호 탐색 가능 구간이 없습니다(영업일 {n}일로는 부족합니다). "
            f"{'CACHE_CALENDAR_DAYS_BACK을 늘려서' if market=='KR' else 'fetch_us의 기간을 늘려서'} "
            f"더 받아와야 합니다."
        )

    hold_label = f"만기 {max_hold_days}일" if max_hold_days is not None else "만기 없음(손절·추세이탈만)"
    print(f"[백테스트] [{market}] 데이터 정리 후 {len(close.columns)}종목 / {n}영업일 · "
          f"신호 탐색 구간: {start_t}~{entry_end_t} (약 {entry_end_t - start_t}거래일) · {hold_label}")

    print("[백테스트] 8조건 게이트 계산 중(벡터화, 전 구간 한 번에)...")
    t_gate0 = time.time()
    gate_df, rs_df = _build_trend_template_gate(close, high, low)
    print(f"[백테스트] 게이트 계산 완료 · {time.time()-t_gate0:.1f}초 · "
          f"탐색 구간 내 평균 일일 통과 종목 수: "
          f"{gate_df.iloc[start_t:entry_end_t+1].sum(axis=1).mean():.0f}개")

    dates = close.index.to_numpy()
    tickers = list(close.columns)
    if max_tickers:
        tickers = tickers[:max_tickers]

    all_trades = []
    t0 = time.time()
    for i, ticker in enumerate(tickers):
        if progress_every and i % progress_every == 0 and i > 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(tickers) - i)
            print(f"  {i}/{len(tickers)}종목 처리 · 경과 {elapsed:.0f}초 · 예상 잔여 {eta:.0f}초")
        try:
            c = close[ticker].to_numpy(dtype=float)
            h = high[ticker].to_numpy(dtype=float)
            l = low[ticker].to_numpy(dtype=float)
            v = value[ticker].to_numpy(dtype=float)
            g = gate_df[ticker].to_numpy(dtype=bool)
            r = rs_df[ticker].to_numpy(dtype=float)
        except Exception:
            continue
        if np.isnan(c).all():
            continue
        trades = _find_trades_for_ticker(dates, c, h, l, v, g, r, start_t,
                                          entry_end_t, data_end_t, max_hold_days)
        for tr in trades:
            tr["ticker"] = ticker
            all_trades.append(tr)

    df = pd.DataFrame(all_trades)
    print(f"[백테스트] 완료 · {len(df)}건의 거래 발견 · 총 {time.time()-t0:.0f}초")
    return df, close, start_t, data_end_t


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0}
    wins = df[df["return_pct"] > 0]
    losses = df[df["return_pct"] <= 0]
    summary = {
        "trades": len(df),
        "win_rate_pct": round(len(wins) / len(df) * 100, 1),
        "avg_return_pct": round(df["return_pct"].mean(), 2),
        "avg_win_pct": round(wins["return_pct"].mean(), 2) if len(wins) else None,
        "avg_loss_pct": round(losses["return_pct"].mean(), 2) if len(losses) else None,
        "profit_factor": (
            round(wins["return_pct"].sum() / abs(losses["return_pct"].sum()), 2)
            if len(losses) and losses["return_pct"].sum() != 0 else None
        ),
        "avg_holding_days": round(df["holding_days"].mean(), 1),
        "exit_reason_breakdown": df["exit_reason"].value_counts().to_dict(),
    }
    return summary


def simulate_portfolio(trades: pd.DataFrame, close: pd.DataFrame, start_t: int, end_t: int,
                        initial_capital: float = 100_000_000, max_concurrent: int = 10) -> dict:
    """
    "이 기간에 실제로 이 규칙대로 매매했다면 계좌가 얼마나 불었을까"를
    하루 단위로 재현한다.

    자금 배분: 매수 시점의 총자산(현금+보유종목 평가액) ÷ max_concurrent를
    그 한 종목에 투입한다 — 균등 배분, 복리로 자연 반영.
    우선순위: 같은 날 신호가 남은 슬롯보다 많으면 진입 시점 RS가 높은
    종목부터 채우고, 나머지는 그날 놓친 기회로 처리한다(재시도 없음).
    """
    if trades.empty:
        return {"trades_taken": 0}

    dates = close.index.to_numpy()
    # 종목별 진입 인덱스 순서로 빠르게 조회하기 위해 ticker -> 거래 리스트로 정리
    by_ticker_trades = {t: g.to_dict("records") for t, g in trades.groupby("ticker")}
    # 그날 시작 가능한 신규 진입 후보를 날짜별로 미리 묶어둔다
    entries_by_day = {}
    for tr in trades.to_dict("records"):
        entries_by_day.setdefault(tr["entry_idx"], []).append(tr)

    cash = initial_capital
    open_positions = {}   # ticker -> {shares, entry_price, exit_idx, exit_price}
    equity_curve = []     # (date, total_equity)
    trades_taken, trades_skipped = 0, 0

    for t in range(start_t, end_t + 1):
        # 1) 오늘 청산 예정인 포지션 정리
        for ticker in [tk for tk, pos in open_positions.items() if pos["exit_idx"] == t]:
            pos = open_positions.pop(ticker)
            cash += pos["shares"] * pos["exit_price"]

        # 2) 보유 종목 평가액(그날 종가 기준, 없으면 직전 유효값 ffill 처리된 close 사용)
        mtm = 0.0
        for ticker, pos in open_positions.items():
            try:
                px = float(close[ticker].iloc[t])
                if np.isnan(px):
                    px = pos["entry_price"]  # 극히 드문 결측 방어
            except Exception:
                px = pos["entry_price"]
            mtm += pos["shares"] * px
        total_equity = cash + mtm
        equity_curve.append((dates[t], total_equity))

        # 3) 오늘 진입 가능한 신호 중, 남은 슬롯만큼 RS 높은 순으로 채움
        todays = [c for c in entries_by_day.get(t, []) if c["ticker"] not in open_positions]
        free_slots = max_concurrent - len(open_positions)
        if todays:
            todays.sort(key=lambda c: -(c["rs_at_entry"] or 0))
            for c in todays[:free_slots]:
                slot_size = total_equity / max_concurrent
                slot_size = min(slot_size, cash)   # 현금이 모자라면 있는 만큼만
                if slot_size <= 0:
                    trades_skipped += 1
                    continue
                shares = slot_size / c["entry_price"]
                cash -= shares * c["entry_price"]
                open_positions[c["ticker"]] = {
                    "shares": shares, "entry_price": c["entry_price"],
                    "exit_idx": c["exit_idx"], "exit_price": c["exit_price"],
                }
                trades_taken += 1
            trades_skipped += max(0, len(todays) - free_slots)

    equity_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    final_equity = equity_df["equity"].iloc[-1]
    total_return_pct = round((final_equity / initial_capital - 1) * 100, 2)

    days_span = (pd.Timestamp(equity_df.index[-1]) - pd.Timestamp(equity_df.index[0])).days
    cagr_pct = (
        round(((final_equity / initial_capital) ** (365 / days_span) - 1) * 100, 2)
        if days_span > 0 else None
    )

    running_max = equity_df["equity"].cummax()
    drawdown = equity_df["equity"] / running_max - 1
    max_dd_pct = round(drawdown.min() * 100, 2)

    return {
        "trades_taken": trades_taken,
        "trades_skipped": trades_skipped,
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 0),
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": max_dd_pct,
        "period_days": days_span,
        "equity_curve": equity_df,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=["KR", "US"],
                     help="백테스트 대상 시장 (기본 KR)")
    ap.add_argument("--max-hold", type=int, default=DEFAULT_MAX_HOLD_DAYS,
                     help=f"만기청산까지 최대 보유일수 (기본 {DEFAULT_MAX_HOLD_DAYS}). "
                          f"--no-timeout과 같이 쓰면 무시된다")
    ap.add_argument("--no-timeout", action="store_true",
                     help="만기청산 규칙을 아예 안 쓴다(미네르비니 원칙대로 손절·추세이탈로만 청산)")
    ap.add_argument("--max-concurrent", type=int, default=10,
                     help="포트폴리오 시뮬레이션 동시 최대 보유 종목 수 (기본 10)")
    ap.add_argument("--capital", type=float, default=None,
                     help="포트폴리오 시뮬레이션 초기 자본금. 생략하면 시장별 기본값 "
                          "(한국 1억원 / 미국 10만달러)")
    ap.add_argument("--no-portfolio", action="store_true",
                     help="포트폴리오 시뮬레이션은 건너뛰고 거래 단위 통계만 본다")
    args = ap.parse_args()

    max_hold = None if args.no_timeout else args.max_hold
    hold_tag = "notimeout" if args.no_timeout else f"h{args.max_hold}"
    hold_label = "만기 없음" if args.no_timeout else f"만기 {args.max_hold}일"
    currency = "원" if args.market == "KR" else "달러"
    capital = args.capital if args.capital is not None else (100_000_000 if args.market == "KR" else 100_000)

    df, close, start_t, end_t = run_backtest(market=args.market, max_hold_days=max_hold)
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    csv_path = os.path.join(OUT_DIR, f"backtest_trades_{stamp}_{args.market}_{hold_tag}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    summary = summarize(df)
    print()
    print("=" * 50)
    print(f" 백테스트 결과 요약 [{args.market}] (8조건 게이트, {hold_label})")
    print("=" * 50)
    if summary["trades"] == 0:
        print(" 거래 표본이 0건입니다.")
    else:
        print(f" 거래 건수      : {summary['trades']}건")
        print(f" 승률           : {summary['win_rate_pct']}%")
        print(f" 평균 수익률     : {summary['avg_return_pct']}%")
        print(f" 평균 이익(승리) : {summary['avg_win_pct']}%")
        print(f" 평균 손실(패배) : {summary['avg_loss_pct']}%")
        print(f" 손익비(PF)     : {summary['profit_factor']}")
        print(f" 평균 보유일수   : {summary['avg_holding_days']}일")
        print(f" 청산 사유 분포  : {summary['exit_reason_breakdown']}")
    print("=" * 50)
    print(f" 거래 상세 저장: {csv_path}")

    if not args.no_portfolio and summary["trades"] > 0:
        print()
        print("[포트폴리오 시뮬레이션] 계산 중...")
        port = simulate_portfolio(df, close, start_t, end_t,
                                   initial_capital=capital,
                                   max_concurrent=args.max_concurrent)
        equity_df = port.pop("equity_curve")
        eq_path = os.path.join(OUT_DIR, f"backtest_equity_{stamp}_{args.market}_{hold_tag}.csv")
        equity_df.to_csv(eq_path, encoding="utf-8-sig")

        print()
        print("=" * 50)
        print(f" 포트폴리오 시뮬레이션 [{args.market}] (동시 최대 {args.max_concurrent}종목, "
              f"초기자본 {capital:,.0f}{currency})")
        print("=" * 50)
        print(f" 실제 매수 건수    : {port['trades_taken']}건 "
              f"(자리가 없어 놓친 신호 {port['trades_skipped']}건)")
        print(f" 최종 평가액       : {port['final_equity']:,.0f}{currency}")
        print(f" 총 수익률         : {port['total_return_pct']}%")
        print(f" 연환산 수익률(CAGR): {port['cagr_pct']}%")
        print(f" 최대 낙폭(MDD)     : {port['max_drawdown_pct']}%")
        print(f" 기간              : {port['period_days']}일")
        print("=" * 50)
        print(f" 일별 평가액 저장: {eq_path}")


if __name__ == "__main__":
    main()
