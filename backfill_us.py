#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_us.py — 미국 데이터 소급 재계산 (2026-09-21 ~ 2026-10-02, 미국 거래일 10일)

사용법: auto_sepa_new 폴더에서
    python3 backfill_us.py

[2026-10-05] backfill_us.sh 를 대체한다.
예전 방식은 날짜마다 run_daily.py 를 그냥 돌려서, 날짜 하나에 미국 500종목
2년치를 3번(메인 스캔·시장 폭·놓친 패턴) 받았다. 10일이면 1만5천 번 넘게
야후에 요청하는 셈이라, 첫 날짜 뒤로 야후가 요청을 막아 빈 데이터만 돌아왔다
(로그: 대부분 배치에서 "99~100 Failed downloads", 이후 "데이터 부족: 0일치").

이 스크립트는
  1) 미국 500종목 가격을 딱 한 번만, 천천히(50종목씩, 동시요청 없이) 받아
     output/backfill_us_prices.pkl 로 저장한다. 다시 실행하면 이 파일을 재사용한다.
  2) 날짜마다 run_daily.py 를 실행하되, 그 안의 미국 가격 다운로드
     (sepa_scanner.fetch_us)를 "저장해 둔 데이터를 그 날짜까지 잘라서 돌려주는"
     함수로 바꿔 끼운다. 그래서 날짜별 재계산에서는 야후 가격 요청이 없다.
  3) 시가총액(통과+관찰 종목만 조회)도 한 번 받은 값을 날짜 사이에 재사용한다.
     어차피 '지금' 시가총액만 받을 수 있으므로 날짜마다 다시 물을 이유가 없다.
  4) 텔레그램 전송을 막고, history/latest.json 은 끝나면(중간에 멈춰도)
     원래대로 되돌린다.
결과는 history/SEPA대시보드_날짜_HIST.html 로 생기고 기존 AM/PM 파일은 그대로다.
"""
import os
import sys
import json
import glob
import time
import shutil
import pickle
import subprocess
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

DATES = ["20260921", "20260922", "20260923", "20260924", "20260925",
         "20260928", "20260929", "20260930", "20261001", "20261002"]

# 한 번에 받을 기간. 메인 스캔은 기준일에서 420일 전부터, 놓친 패턴(backtest)은
# 2년치를 쓰므로 둘 다 덮도록 넉넉히 잡는다. yfinance 의 end 는 그날 미포함.
DL_START = "2024-09-01"
DL_END = "2026-10-03"

OUT_DIR = os.path.join(HERE, "output")
PKL = os.path.join(OUT_DIR, "backfill_us_prices.pkl")
CAPS = os.path.join(OUT_DIR, "backfill_us_caps.json")
YF_CACHE = os.path.join(OUT_DIR, "yf_cache")
US_CACHE_GLOB = os.path.join(HERE, "cache", "us_prices_*.parquet")


def _yf():
    import yfinance as yf
    # yfinance 내부 기록 파일(sqlite)을 프로젝트 안(output/, 저장소에 안 올라감)에
    # 두어, ~/Library/Caches 쪽 파일이 깨지거나 잠기는 문제를 피한다.
    try:
        os.makedirs(YF_CACHE, exist_ok=True)
        yf.set_tz_cache_location(YF_CACHE)
    except Exception:
        pass
    return yf


# ─────────────────────────────────────────────────────────────
# 1) 가격 한 번에 받기
# ─────────────────────────────────────────────────────────────
def _as_frame(x, name):
    import pandas as pd
    return x.to_frame(name) if isinstance(x, pd.Series) else x


def download_once():
    import pandas as pd
    if os.path.exists(PKL):
        with open(PKL, "rb") as f:
            full = pickle.load(f)
        print(f"[1/2] 저장해 둔 가격 재사용: {full['close'].shape[1]}종목, "
              f"{full['close'].index[0].date()} ~ {full['close'].index[-1].date()}")
        return full

    yf = _yf()
    from sepa_scanner import us_universe
    tickers = us_universe()

    # 먼저 한 종목으로 야후가 응답하는지 본다. 막혀 있으면 500종목을 받아봐야
    # 빈 데이터만 쌓이고 차단만 길어진다.
    t = yf.download("AAPL", start="2026-09-15", end="2026-09-23",
                    progress=False, auto_adjust=True, threads=False)
    if t is None or t.empty:
        print("\n야후가 지금 요청을 받지 않습니다(짧은 시간에 너무 많이 요청해 "
              "일시적으로 막힌 상태로 보입니다).\n1시간쯤 뒤에 같은 명령을 다시 "
              "실행해 주세요.")
        sys.exit(2)

    fields = ["Close", "Volume", "High", "Low"]
    got = {f: [] for f in fields}
    missing = list(tickers)
    for attempt in range(3):
        if not missing:
            break
        if attempt:
            print(f"  못 받은 {len(missing)}종목 재시도 ({attempt}/2) — 60초 쉬었다가 시작")
            time.sleep(60)
        still = []
        for i in range(0, len(missing), 50):
            batch = missing[i:i + 50]
            try:
                df = yf.download(batch, start=DL_START, end=DL_END, auto_adjust=True,
                                 progress=False, group_by="column", threads=False)
            except Exception as e:
                print(f"  배치 오류: {str(e)[:80]}")
                df = None
            ok = []
            if df is not None and not df.empty:
                c = _as_frame(df["Close"], batch[0])
                ok = [x for x in batch if x in c.columns and c[x].notna().any()]
                for f in fields:
                    got[f].append(_as_frame(df[f], batch[0])[ok])
            still += [x for x in batch if x not in ok]
            done = len(tickers) - len(missing) + i + len(batch)
            print(f"  가격 받는 중... {min(done, len(tickers))}/{len(tickers)} "
                  f"(이번 배치 성공 {len(ok)}/{len(batch)})")
            time.sleep(3)
        missing = still

    if not got["Close"]:
        print("\n한 종목도 받지 못했습니다. 1시간쯤 뒤에 다시 실행해 주세요.")
        sys.exit(3)

    full = {k.lower(): pd.concat(v, axis=1).sort_index() for k, v in got.items()}
    for k in full:
        full[k] = full[k].loc[:, ~full[k].columns.duplicated()]
    n = full["close"].shape[1]
    print(f"[1/2] 가격 받기 완료: {n}/{len(tickers)}종목 "
          f"({full['close'].index[0].date()} ~ {full['close'].index[-1].date()})")
    if missing:
        print(f"  끝내 못 받은 종목({len(missing)}): {', '.join(missing[:20])}"
              f"{' …' if len(missing) > 20 else ''}")
    if n < len(tickers) * 0.95:
        print("\n받은 종목이 95%에 못 미칩니다. 이 상태로 계산하면 결과가 틀어지므로 "
              "저장하지 않고 멈춥니다. 1시간쯤 뒤에 다시 실행해 주세요.")
        sys.exit(3)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(PKL, "wb") as f:
        pickle.dump(full, f)
    return full


# ─────────────────────────────────────────────────────────────
# 2) 날짜 하나 재계산 (자식 프로세스에서 실행)
# ─────────────────────────────────────────────────────────────
def run_one(date_str):
    import pandas as pd
    _yf()
    with open(PKL, "rb") as f:
        full = pickle.load(f)
    cutoff = pd.Timestamp(date_str)

    import sepa_scanner

    def fetch_us_from_saved(tickers, start=None, end=None, period="2y"):
        cols = [t for t in tickers if t in full["close"].columns]
        idx = full["close"].index
        if start and end:
            m = (idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
        else:
            # period="2y"(놓친 패턴 계산) — 원래는 '오늘'까지 받지만, 소급 계산이므로
            # 기준일까지만 쓴다.
            m = (idx > cutoff - pd.Timedelta(days=730)) & (idx <= cutoff)
        close = full["close"].loc[m, cols]
        volume = full["volume"].loc[m, cols]
        high = full["high"].loc[m, cols]
        low = full["low"].loc[m, cols]
        # 원래 fetch_us 와 같은 규칙: 종목 절반 이상이 비어 있는 날짜 행은 뺀다.
        good = close.notna().mean(axis=1).ge(0.5) & volume.notna().mean(axis=1).ge(0.5)
        close, volume, high, low = close[good], volume[good], high[good], low[good]
        print(f"[소급] 저장해 둔 미국 가격 사용: {len(cols)}종목, "
              f"{close.index[0].date()} ~ {close.index[-1].date()}")
        return {"close": close, "value": close * volume,
                "high": high, "low": low,
                "meta": pd.DataFrame({"market": "US"}, index=close.columns)}

    orig_caps = sepa_scanner.us_market_caps

    def caps_with_memo(tickers, sleep_sec=0.25):
        memo = {}
        if os.path.exists(CAPS):
            with open(CAPS, encoding="utf-8") as f:
                memo = json.load(f)
        need = [t for t in tickers if t not in memo]
        if need:
            memo.update(orig_caps(need, sleep_sec))
            with open(CAPS, "w", encoding="utf-8") as f:
                json.dump(memo, f)
        print(f"[소급] 시가총액: {len(tickers)}종목 중 새로 조회 {len(need)}종목")
        return {t: memo.get(t) for t in tickers}

    sepa_scanner.fetch_us = fetch_us_from_saved
    sepa_scanner.us_market_caps = caps_with_memo

    import run_daily
    run_daily.main(market="US", open_browser=False, session="HIST", date=date_str)


# ─────────────────────────────────────────────────────────────
# 전체 흐름
# ─────────────────────────────────────────────────────────────
def _clear_us_cache():
    for p in glob.glob(US_CACHE_GLOB):
        try:
            os.remove(p)
        except OSError:
            pass


def main():
    if not os.path.exists(os.path.join(HERE, "run_daily.py")):
        print("run_daily.py 가 없습니다. auto_sepa_new 폴더 안에서 실행하세요.")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    log_path = os.path.join(OUT_DIR, f"backfill_us_{dt.datetime.now():%Y%m%d_%H%M}.log")
    latest = os.path.join(HERE, "history", "latest.json")
    backup = latest + ".backfill_backup"
    if os.path.exists(latest):
        shutil.copy2(latest, backup)

    env = dict(os.environ, TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
    ok, fail = [], []
    t0 = time.time()
    try:
        download_once()
        print(f"[2/2] 날짜별 재계산 (기록: {os.path.relpath(log_path)})")
        for d in DATES:
            _clear_us_cache()   # 앞 날짜의 임시 캐시를 다음 날짜가 재사용하지 않게
            print(f"  [{dt.datetime.now():%H:%M:%S}] {d} 재계산 중...", flush=True)
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n==================== {d} ====================\n")
                lf.flush()
                rc = subprocess.run([sys.executable, os.path.abspath(__file__),
                                     "--run-date", d],
                                    stdout=lf, stderr=subprocess.STDOUT, env=env).returncode
            made = glob.glob(os.path.join(HERE, "history", f"*_{d}_HIST.html"))
            if rc == 0 and made:
                ok.append(d)
                print("      완료")
            else:
                fail.append(d)
                print(f"      실패 — 기록 파일 확인: {os.path.relpath(log_path)}")
    finally:
        _clear_us_cache()
        if os.path.exists(backup):
            shutil.move(backup, latest)
            print("history/latest.json 원래대로 복원했습니다.")

    print(f"\n========== 결과 ({(time.time() - t0) / 60:.0f}분 소요) ==========")
    print("성공: " + (" ".join(ok) or "없음"))
    print("실패: " + (" ".join(fail) or "없음"))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run-date":
        run_one(sys.argv[2])
    else:
        main()
