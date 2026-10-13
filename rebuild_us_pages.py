#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_us_pages.py — 9/18~10/3 장전·장마감판 중 "미국 시세가 묵은 값"이었던
페이지만 골라, 미국 부분을 올바른 데이터로 바꿔 끼우고 PDF 리포트도 다시 만든다.

사용법 (auto_sepa_new 폴더에서)
    python3 rebuild_us_pages.py --check     # 대상만 확인 (아무것도 안 바꿈)
    python3 rebuild_us_pages.py             # 실제 교체

전제: backfill_us.py 를 한 번 돌려서 output/backfill_us_prices.pkl
      (미국 503종목 가격, 2024-09 ~ 2026-10-02)이 있어야 한다. 야후에 가격을
      다시 요청하지 않는다.

[2026-10-05] 하는 일
 1) 대상 찾기: 각 페이지가 "미국 OO일 종가"라고 표시한 날짜(S&P500 시장 폭의
    data_date)의 실제 종가와, 페이지에 들어 있는 미국 종목 가격을 비교한다.
    ±0.3% 안에서 일치하는 종목이 80% 미만이면 "묵은 데이터"로 보고 교체 대상.
    (실측: 정상 페이지 94%, 묵은 페이지 3~23% — 경계가 뚜렷하다)
 2) 미국 부분 새로 계산: 그 날짜 기준으로 미국 스캔을 다시 한다.
    RS 기준은 GitHub 자동 실행과 같은 90(--min-rs 90). 소급판(backfill_us.py)은
    기본값 70으로 돌아가 "통과" 기준이 달랐다 — 이번엔 맞춘다.
 3) 한국 부분은 원래 페이지에 들어 있던 값을 그대로 꺼내 쓴다(다시 계산 안 함).
    VCP 차트·놓친 패턴·업종 상태도 한국 것은 원래 것, 미국 것만 새것.
    시장 폭·매크로 지표는 원래 페이지 값 그대로(시장 폭은 원래도 정상이었다).
 4) 같은 파일 이름으로 대시보드를 다시 만들고, PDF 리포트도 다시 만든다.
    PDF의 "1+2단계 동시 통과(한국)" 표는 DART에서 재무를 다시 받아 만든다
    (DART_API_KEY 가 .env 에 있을 때만. 분기 재무는 그때와 지금이 같다).
 5) 페이지 상단에 "미국 데이터 10/05 재계산" 안내 띠를 넣는다.
 6) 9/21~10/2 소급판(_HIST) 10개와 그 PDF를 지우고 날짜 목록을 다시 만든다.
 7) 바꾸기 전 원본은 전부 output/rebuild_backup_날짜시각/ 에 복사해 둔다.
 history/latest.json, history/rs90_registry.json 은 건드리지 않는다.
"""
import os
import re
import sys
import json
import glob
import shutil
import pickle
import argparse
import tempfile
import unicodedata
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

HIST = os.path.join(HERE, "history")
OUT = os.path.join(HERE, "output")
PKL = os.path.join(OUT, "backfill_us_prices.pkl")
CAPS = os.path.join(OUT, "backfill_us_caps.json")
SCAN_FROM, SCAN_TO = "20260918", "20261003"
HIST_DELETE = ["20260921", "20260922", "20260923", "20260924", "20260925",
               "20260928", "20260929", "20260930", "20261001", "20261002"]
MIN_RS_US = 90
TOL, OK_RATIO = 0.003, 0.80
TODAY = dt.date.today()
NOTE = (f'<div style="background:#FFF4E5;border:1px solid #F0B46A;color:#7A4A00;'
        f'padding:8px 12px;margin:8px 16px;border-radius:6px;font-size:13px;line-height:1.5">'
        f'※ 미국 종목 데이터는 {TODAY:%Y-%m-%d}에 다시 계산해 바꿔 넣었습니다. '
        f'원래 이 페이지의 미국 시세가 며칠 묵은 값이었기 때문입니다. '
        f'한국 종목 데이터는 원래 그대로입니다.</div>')


# ─────────────────────────────────────────────────────────────
# 페이지 안의 데이터(const NAME = {...}) 읽기·바꾸기
# ─────────────────────────────────────────────────────────────
_DEC = json.JSONDecoder()


def get_blob(html, name):
    m = re.search(r"const " + name + r" ?= ?", html)
    if not m:
        return None, None, None
    try:
        v, end = _DEC.raw_decode(html, m.end())
        return v, m.end(), end
    except Exception:
        return None, None, None


def set_blob(html, name, value):
    _, s, e = get_blob(html, name)
    if s is None:
        return html
    return html[:s] + json.dumps(value, ensure_ascii=False) + html[e:]


def nfc(s):
    return unicodedata.normalize("NFC", s)


def find_pages():
    """history/ 의 9/18~10/3 장전·장마감 대시보드 → [(stamp, sess, path)]"""
    out = []
    for p in glob.glob(os.path.join(HIST, "*.html")):
        m = re.match(r"SEPA대시보드_(\d{8})_(AM|PM)\.html$", nfc(os.path.basename(p)))
        if m and SCAN_FROM <= m.group(1) <= SCAN_TO:
            out.append((m.group(1), m.group(2), p))
    return sorted(out)


def find_hist_file(prefix, stamp, sess, ext):
    for p in glob.glob(os.path.join(HIST, "*" + ext)):
        if nfc(os.path.basename(p)) == nfc(f"{prefix}_{stamp}_{sess}{ext}"):
            return p
    return None


def breadth_date(html, market):
    snap, _, _ = get_blob(html, "BREADTH_SNAPSHOT")
    for b in snap or []:
        if b.get("market") == market and b.get("data_date"):
            return str(b["data_date"])
    return None


def load_full():
    if not os.path.exists(PKL):
        print("output/backfill_us_prices.pkl 이 없습니다. 먼저 python3 backfill_us.py 를 "
              "실행해 미국 가격을 받아 두세요.")
        sys.exit(1)
    with open(PKL, "rb") as f:
        return pickle.load(f)


def match_ratio(html, full, us_date):
    import pandas as pd
    data, _, _ = get_blob(html, "DATA")
    ts = pd.Timestamp(us_date)
    if ts not in full["close"].index:
        return None
    row = full["close"].loc[ts]
    n = ok = 0
    for r in data or []:
        if r.get("market") != "US" or not r.get("price"):
            continue
        ref = row.get(r["ticker"])
        if ref is None or pd.isna(ref) or ref <= 0:
            continue
        n += 1
        ok += abs(r["price"] / ref - 1) <= TOL
    return ok / n if n else None


def survey(full):
    rows = []
    for stamp, sess, path in find_pages():
        html = open(path, encoding="utf-8").read()
        us_date = breadth_date(html, "SP500")
        ratio = match_ratio(html, full, us_date) if us_date else None
        pdf = find_hist_file("SEPA리포트", stamp, sess, ".pdf")
        bad = ratio is not None and ratio < OK_RATIO
        rows.append(dict(stamp=stamp, sess=sess, path=path, us_date=us_date,
                         ratio=ratio, bad=bad, pdf=pdf))
    return rows


def print_survey(rows):
    lab = {"AM": "장전", "PM": "장마감"}
    print(f"\n{'페이지':<16}{'표시된 미국 날짜':<14}{'가격 일치율':>10}  판정")
    for r in rows:
        d = f"{r['stamp'][4:6]}/{r['stamp'][6:]} {lab[r['sess']]}"
        u = f"{r['us_date'][4:6]}/{r['us_date'][6:]}" if r["us_date"] else "-"
        q = f"{r['ratio']*100:.0f}%" if r["ratio"] is not None else "확인불가"
        v = "교체" if r["bad"] else ("정상" if r["ratio"] is not None else "건너뜀")
        print(f"{d:<16}{u:<14}{q:>10}  {v}{'' if r['pdf'] else '  (PDF 없음)'}")
    n = sum(r["bad"] for r in rows)
    print(f"\n교체 대상: 페이지 {n}개, PDF {sum(1 for r in rows if r['bad'] and r['pdf'])}개")
    return n


# ─────────────────────────────────────────────────────────────
# 미국 스캔 (저장해 둔 가격으로, 야후 요청 없이)
# ─────────────────────────────────────────────────────────────
CUTOFF = [None]
_US_CACHE = {}


def install_patches(full):
    import pandas as pd
    import sepa_scanner

    def fetch_us_from_saved(tickers, start=None, end=None, period="2y"):
        cols = [t for t in tickers if t in full["close"].columns]
        idx = full["close"].index
        if start and end:
            m = (idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
        else:
            m = (idx > CUTOFF[0] - pd.Timedelta(days=730)) & (idx <= CUTOFF[0])
        c = full["close"].loc[m, cols]
        v = full["volume"].loc[m, cols]
        h = full["high"].loc[m, cols]
        lo = full["low"].loc[m, cols]
        good = c.notna().mean(axis=1).ge(0.5) & v.notna().mean(axis=1).ge(0.5)
        c, v, h, lo = c[good], v[good], h[good], lo[good]
        return {"close": c, "value": c * v, "high": h, "low": lo,
                "meta": pd.DataFrame({"market": "US"}, index=c.columns)}

    orig_caps = sepa_scanner.us_market_caps

    def caps_with_memo(tickers, sleep_sec=0.25):
        memo = json.load(open(CAPS, encoding="utf-8")) if os.path.exists(CAPS) else {}
        need = [t for t in tickers if t not in memo]
        if need:
            memo.update(orig_caps(need, sleep_sec))
            json.dump(memo, open(CAPS, "w", encoding="utf-8"))
        return {t: memo.get(t) for t in tickers}

    sepa_scanner.fetch_us = fetch_us_from_saved
    sepa_scanner.us_market_caps = caps_with_memo


def us_scan(us_date):
    if us_date in _US_CACHE:
        return _US_CACHE[us_date]
    import pandas as pd
    import sepa_scanner
    import missed_patterns_data
    CUTOFF[0] = pd.Timestamp(us_date)
    for p in glob.glob(os.path.join(HERE, "cache", "us_prices_*.parquet")):
        os.remove(p)   # 앞 날짜의 임시 캐시를 재사용하지 않게
    print(f"  [미국 {us_date}] 스캔 (RS 기준 {MIN_RS_US})")
    sepa_scanner.run("US", MIN_RS_US, as_of=us_date)
    df = pd.read_csv(os.path.join(OUT, f"sepa_scan_{us_date}.csv"),
                     index_col=0, encoding="utf-8-sig")
    df.index = [str(i) for i in df.index]
    cpath = os.path.join(OUT, f"sepa_vcp_charts_{us_date}.json")
    charts = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
    sector = getattr(sepa_scanner, "LAST_SECTOR_STATUS", {}).get("US", {"status": "ok"})
    try:
        missed = missed_patterns_data.compute_missed_patterns_snapshot(markets=("US",))
    except Exception as e:
        print(f"  [미국 {us_date}] 놓친 패턴 계산 실패(원래 값 유지): {e}")
        missed = None
    for p in glob.glob(os.path.join(HERE, "cache", "us_prices_*.parquet")):
        os.remove(p)
    _US_CACHE[us_date] = (df, charts, sector, missed)
    return _US_CACHE[us_date]


# ─────────────────────────────────────────────────────────────
# 한국 부분: 원래 페이지의 표 데이터를 스캔 결과(CSV) 형태로 되돌리기
# ─────────────────────────────────────────────────────────────
def kr_frame(data, kr_date):
    import pandas as pd
    from make_dashboard import CONDITIONS
    rows, idx = [], []
    for r in data:
        if r.get("market") != "KR":
            continue
        fails = set(r.get("fails") or [])
        d = {
            "name": r.get("name"), "market": "KR", "price": r.get("price"),
            "RS": r.get("rs"), "vs_52w_high_%": r.get("high"), "vs_52w_low_%": r.get("low"),
            "vs_MA50_%": r.get("ma50"), "MA200_slope_%": r.get("slope"),
            "avg_turnover_20d": r.get("turnover"), "market_cap": r.get("cap"),
            "sector": r.get("sector"), "turnover_ratio": r.get("turnoverRatio"),
            "turnover_ratio_5d": r.get("turnoverRatio5d"), "dev_days": r.get("devDays"),
            "dev_days_now": r.get("devDaysNow"), "dev_peak": r.get("devPeak"),
            "dev_trend": r.get("devTrend"), "conditions_met": r.get("met"),
            "PASS": bool(r.get("pass")), "pct_from_pivot": r.get("pivot"),
            "risk_pct": r.get("risk"), "pivot_price": r.get("pivotPrice"),
            "stop_price": r.get("stopPrice"), "contraction_count": r.get("legs"),
            "is_tightening": r.get("tight"), "vol_dryup": r.get("dryup"),
            "vcp_status": r.get("vcp"), "vol_ratio": r.get("volRatio"),
            "vol_ratio_label": r.get("volRatioLabel"), "data_date": kr_date,
        }
        for col, label in CONDITIONS:
            d[col] = label not in fails
        rows.append(d)
        idx.append(str(r["ticker"]))
    return pd.DataFrame(rows, index=idx)


def breadth_summary(snap):
    label = {"KR_TOTAL": "한국 전체", "SP500": "S&P500"}
    parts = []
    for mk in ("KR_TOTAL", "SP500"):
        rec = next((b for b in snap or [] if b.get("market") == mk), None)
        if not rec or rec.get("status") == "incomplete":
            continue
        try:
            parts.append(f"{label[mk]} 200일선 위 {rec['above200_pct']}% · "
                         f"신고가 {rec['nh52']}/신저가 {rec['nl52']} · 8조건 {rec['tt8_count']}종목")
        except KeyError:
            continue
    return ("시장 폭 · " + " · ".join(parts)) if parts else None


# ─────────────────────────────────────────────────────────────
# DART 2단계 (한 번에 모아서 조회)
# ─────────────────────────────────────────────────────────────
def dart_union(kr_frames):
    import pandas as pd
    try:
        import dart_fundamentals as dfm
        key = dfm._key()
    except Exception as e:
        print(f"[DART] 키를 찾지 못해 PDF의 '1+2단계 동시 통과' 표는 빠집니다: {str(e)[:80]}")
        return None
    allpass = pd.concat([f[f["PASS"] == True] for f in kr_frames])
    allpass = allpass[~allpass.index.duplicated(keep="last")]
    if allpass.empty:
        return None
    tmp = os.path.join(OUT, "rebuild_kr_pass_union.csv")
    allpass.to_csv(tmp, encoding="utf-8-sig")
    try:
        merged = dfm.combine(tmp, key, out_csv=os.path.join(OUT, "rebuild_stage2_union.csv"))
    except Exception as e:
        print(f"[DART] 조회 실패 — PDF의 '1+2단계 동시 통과' 표는 빠집니다: {str(e)[:120]}")
        return None
    if merged is None or merged.empty:
        return None
    fund_cols = [c for c in merged.columns if c not in allpass.columns and c != "SEPA12_PASS"]
    return merged[fund_cols]


# ─────────────────────────────────────────────────────────────
# 페이지 하나 다시 만들기
# ─────────────────────────────────────────────────────────────
def rebuild_one(r, manifest_days, fund):
    import pandas as pd
    import make_dashboard
    import make_report

    old = open(r["path"], encoding="utf-8").read()
    data, _, _ = get_blob(old, "DATA")
    old_charts, _, _ = get_blob(old, "VCP_CHARTS")
    old_strat, _, _ = get_blob(old, "STRATEGY")
    old_missed, _, _ = get_blob(old, "MISSED_SNAPSHOT")
    old_mstatus, _, _ = get_blob(old, "MISSED_STATUS")
    old_sector, _, _ = get_blob(old, "SECTOR_STATUS")
    breadth, _, _ = get_blob(old, "BREADTH_SNAPSHOT")
    macro, _, _ = get_blob(old, "MACRO_SNAPSHOT")
    m = re.search(r"이 화면 조회 시각: <b>(.*?)</b>", old)
    old_gen = m.group(1) if m else None

    kd = breadth_date(old, "KR_TOTAL")
    kr_date = f"{kd[:4]}-{kd[4:6]}-{kd[6:]}" if kd else None
    kr = kr_frame(data, kr_date)
    us_df, us_charts, us_sector, us_missed = us_scan(r["us_date"])

    stamp, sess = r["stamp"], r["sess"]
    merged = pd.concat([kr, us_df], axis=0, sort=False)
    csv_path = os.path.join(OUT, f"sepa_scan_{stamp}.csv")
    merged.to_csv(csv_path, encoding="utf-8-sig")

    us_old = {x["ticker"] for x in data if x.get("market") == "US"}
    charts = {k: v for k, v in (old_charts or {}).items() if k not in us_old}
    charts.update(us_charts)
    with open(os.path.join(OUT, f"sepa_vcp_charts_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(charts, f, ensure_ascii=False)

    missed = {"confirmed": [], "developing": []}
    for k in missed:
        missed[k] = [x for x in (old_missed or {}).get(k, []) if x.get("market") != "US"]
        missed[k] += (us_missed or {}).get(k, []) if us_missed else \
            [x for x in (old_missed or {}).get(k, []) if x.get("market") == "US"]
    sector = dict(old_sector or {})
    sector["US"] = us_sector

    # 매매전략의 'RS90 진입 후 경과일'이 원래 페이지와 같도록, 원래 표의 최초진입일로
    # 임시 레지스트리를 만들어 쓴다(history/rs90_registry.json 은 안 건드림).
    tmpd = tempfile.mkdtemp(prefix="rebuild_", dir=OUT)
    reg = {}
    for t in (old_strat or {}).get("table", []) or []:
        if t.get("first_date"):
            reg[f"{t.get('market')}:{t.get('ticker')}"] = t["first_date"]
    json.dump(reg, open(os.path.join(tmpd, "rs90_registry.json"), "w", encoding="utf-8"))

    make_dashboard.build(csv_path, out_path=os.path.join(tmpd, "out.html"), open_browser=False,
                         hist_dir=tmpd, session=sess, macro_snapshot=macro,
                         breadth_snapshot=breadth, missed_patterns_snapshot=missed,
                         sector_status=sector, missed_status=old_mstatus)
    new = open(os.path.join(tmpd, "out.html"), encoding="utf-8").read()

    new = set_blob(new, "DAYS", manifest_days)
    strat, _, _ = get_blob(new, "STRATEGY")
    if strat and old_strat:
        strat["generated_at"] = old_strat.get("generated_at", strat.get("generated_at"))
        if old_strat.get("session"):
            strat["session"] = old_strat["session"]
        new = set_blob(new, "STRATEGY", strat)
    if old_gen:
        new = re.sub(r"(이 화면 조회 시각: <b>)(.*?)(</b>)",
                     lambda mm: mm.group(1) + old_gen + f" · 미국 데이터 {TODAY:%m/%d} 재계산"
                     + mm.group(3), new, count=1)
    new = new.replace("<header>", NOTE + "\n<header>", 1)
    with open(r["path"], "w", encoding="utf-8") as f:
        f.write(new)
    shutil.rmtree(tmpd, ignore_errors=True)

    # PDF
    stage2 = None
    if fund is not None:
        kp = kr[kr["PASS"] == True]
        if not kp.empty:
            s2 = kp.join(fund, how="left")
            if "F_PASS" in s2.columns:
                s2["SEPA12_PASS"] = s2["PASS"] & s2["F_PASS"].fillna(False).astype(bool)
                stage2 = os.path.join(OUT, f"sepa_stage2_{stamp}.csv")
                s2.to_csv(stage2, encoding="utf-8-sig")
    make_report.build(csv_path, stage2_csv=stage2, session=sess,
                      breadth_summary=breadth_summary(breadth))
    n_us_pass = int(us_df["PASS"].sum())
    return n_us_pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="대상만 확인하고 아무것도 바꾸지 않음")
    a = ap.parse_args()

    full = load_full()
    rows = survey(full)
    n = print_survey(rows)
    if a.check:
        print("\n(확인만 했습니다. 실제로 바꾸려면 --check 없이 실행하세요.)")
        return
    if n == 0:
        print("바꿀 페이지가 없습니다.")
        return

    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["TELEGRAM_CHAT_ID"] = ""
    targets = [r for r in rows if r["bad"]]

    # 0) 원본 백업
    bk = os.path.join(OUT, f"rebuild_backup_{dt.datetime.now():%Y%m%d_%H%M}")
    os.makedirs(bk, exist_ok=True)
    hist_del = []
    for d in HIST_DELETE:
        for pre, ext in (("SEPA대시보드", ".html"), ("SEPA리포트", ".pdf")):
            p = find_hist_file(pre, d, "HIST", ext)
            if p:
                hist_del.append(p)
    for r in targets:
        for p in (r["path"], r["pdf"]):
            if p:
                shutil.copy2(p, bk)
    for p in hist_del:
        shutil.copy2(p, bk)
    print(f"\n원본 백업: {os.path.relpath(bk)}")

    # 1) 소급판 삭제 + 날짜 목록 재생성
    import make_dashboard
    for p in hist_del:
        os.remove(p)
    print(f"소급판 삭제: {len(hist_del)}개 파일")
    make_dashboard.regen_manifest(HIST)
    manifest_days = json.load(open(os.path.join(HIST, "manifest.json"), encoding="utf-8"))

    # 2) DART 2단계 (한국 통과 종목을 모아 한 번만 조회)
    install_patches(full)
    kr_frames = []
    for r in targets:
        html = open(r["path"], encoding="utf-8").read()
        data, _, _ = get_blob(html, "DATA")
        kr_frames.append(kr_frame(data, None))
    fund = dart_union(kr_frames)

    # 3) 페이지별 교체
    done, fail = [], []
    for r in targets:
        tag = f"{r['stamp']}_{r['sess']}"
        print(f"\n===== {tag} (미국 {r['us_date']} 종가로 교체) =====")
        try:
            n_pass = rebuild_one(r, manifest_days, fund)
            done.append((tag, r["us_date"], n_pass))
        except Exception as e:
            import traceback
            traceback.print_exc()
            fail.append(tag)
            # 실패한 페이지는 원본으로 되돌린다
            for p in (r["path"], r["pdf"]):
                if p:
                    shutil.copy2(os.path.join(bk, os.path.basename(p)), p)

    make_dashboard.regen_manifest(HIST)
    print("\n========== 결과 ==========")
    for tag, u, n_pass in done:
        print(f"  완료 {tag}  ← 미국 {u} 종가, 미국 통과 {n_pass}종목")
    print("실패: " + (" ".join(fail) if fail else "없음"))
    print(f"원본 백업: {os.path.relpath(bk)}")


if __name__ == "__main__":
    main()
