# -*- coding: utf-8 -*-
"""
rec_ledger.py — 추천 종목 성적 장부.

[2026-10-19] "추천한 종목이 실제로 어떻게 됐나"를 매일 자동으로 기록한다.

추천의 출처 두 가지
1. rule  (스캐너 규칙): 매 거래일, 아래 조건을 모두 만족하는 종목을 그날 추천으로 기록한다.
     - 8조건 통과
     - VCP 상태가 "형성중" 또는 "진입가능" (패턴 없는 종목은 제외)
     - 종가가 기준가(피벗) 아래 0~3% (곧 돌파할 자리)
     - 50일선 이격 25% 미만(과열 아님)
     (스캐너 손절폭은 조건에 넣지 않는다 — 한국은 대부분 10% 이상이라 한 종목도 안 걸린다.
      대신 손절은 아래 규칙대로 매수가 -7%를 넘지 않게 자른다.)
     - 20일 평균 거래대금: 한국 30억원 이상, 미국 2천만 달러 이상
   같은 종목이 이미 대기·보유 중이면 다시 기록하지 않는다.
2. sheet (액션시트): Claude가 액션시트에 1·2순위 등으로 낸 후보.
   history/sheet_picks.json 에 사람이 적어 넣는다(날짜·기준가·손절).

매매 규칙 (액션시트에 적어 온 규칙 그대로)
- 매수: 추천 뒤 종가가 처음으로 [기준가, 기준가+5%] 안에 들어온 날, 그 종가에 산다.
  · 종가가 기준가+5%를 넘은 날은 추격하지 않고 다음 날 다시 본다.
  · 10거래일 안에 매수가 안 되면 끝. 그사이 +5%를 넘어 달아난 적이 있으면 "놓침", 아니면 "만료".
  · rule 추천은 추천 다음 거래일부터, sheet 추천은 액션시트 날짜(그날 장)부터 매수 가능.
- 손절: 스캐너 손절가와 매수가 -7% 중 가까운(높은) 쪽. 종가가 그 아래면 그 종가에 판다.
- 본전 보호: 종가가 매수가 +10% 이상 오른 적이 있으면 손절선을 매수가로 올린다.
- 그 밖에는 계속 보유(마지막 종가로 평가).
- 장중 고가·저가 데이터가 없어 모든 판정은 종가 기준이다(거래량 조건은 쓰지 않음).

계산 방식: 매번 history/ 의 대시보드 파일(종목별 종가가 들어 있음)과 오늘 데이터를
처음부터 다시 훑어 장부 전체를 새로 만든다(상태를 저장하지 않으므로 같은 날 여러 번 돌려도,
sheet_picks.json 에 과거 추천을 나중에 적어 넣어도 결과가 같다).
결과: history/rec_ledger.json (대시보드 "추천 성적표"가 그대로 보여 준다)
"""
from __future__ import annotations

import os
import re
import json
import glob
import datetime as dt
import unicodedata

import market_calendar as mc

LEDGER_FILE = "rec_ledger.json"
PICKS_FILE = "sheet_picks.json"
START = "2026-09-29"          # 이 날짜부터의 추천만 장부에 올린다
ENTRY_BAND = 0.05             # 기준가 위 5%까지만 매수
WATCH_DAYS = 10               # 매수 조건을 기다리는 최대 거래일
MAX_LOSS = 0.07               # 매수가 -7%
BREAKEVEN_AT = 0.10           # +10% 이상이면 손절선을 매수가로
RULE = {"pivot_lo": -3.0, "pivot_hi": 0.0, "ma50_max": 25.0,
        "turnover_min": {"KR": 3e9, "US": 2e7}, "vcp": ("developing", "entry_ready")}

_FNAME_RE = re.compile(r"_(\d{8})(?:_(AM|PM|US|MANUAL|HIST))?\.html$")
_RANK = {"HIST": 0, "AM": 1, "MANUAL": 2, "": 3, "PM": 3, "US": 4}


# ── 과거 대시보드에서 시장별·거래일별 종목 데이터 읽기 ─────────────
def _rows(html: str) -> list:
    i = html.find("const DATA = ")
    if i < 0:
        return []
    try:
        rows, _ = json.JSONDecoder().raw_decode(html, i + len("const DATA = "))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _prev_open(status_fn, d: dt.date):
    d = d - dt.timedelta(days=1)
    for _ in range(10):
        if status_fn(d.strftime("%Y-%m-%d"))["open"]:
            return d
        d -= dt.timedelta(days=1)
    return None


def _dates(html: str, fdate: dt.date, session: str) -> dict:
    """그 화면의 한국·미국 종가 날짜. backfill_pass_history.py 와 같은 규칙."""
    basis = " ".join(re.findall(r'basis-item">([^<]*)', html))
    kr = re.search(r"한국 (\d{4}-\d{2}-\d{2})", basis)
    us = re.search(r"미국 (\d{4}-\d{2}-\d{2})", basis)
    if kr or us:
        return {"KR": kr.group(1) if kr else None, "US": us.group(1) if us else None}
    ds = fdate.strftime("%Y-%m-%d")
    if session == "HIST":
        return {"KR": ds if mc.kr_status(ds)["open"] else None, "US": ds if mc.us_status(ds)["open"] else None}
    if session == "AM":
        k, u = _prev_open(mc.kr_status, fdate), _prev_open(mc.us_status, fdate)
        return {"KR": k and k.strftime("%Y-%m-%d"), "US": u and u.strftime("%Y-%m-%d")}
    if session in ("PM", ""):
        u = _prev_open(mc.us_status, fdate)
        return {"KR": ds if mc.kr_status(ds)["open"] else None, "US": u and u.strftime("%Y-%m-%d")}
    return {"KR": None, "US": None}


def _lite(r: dict) -> dict:
    return {"p": r.get("price"), "pass": bool(r.get("pass")), "name": r.get("name"),
            "pivot": r.get("pivot"), "pivotPrice": r.get("pivotPrice"), "stop": r.get("stopPrice"),
            "risk": r.get("risk"), "ma50": r.get("ma50"), "vcp": r.get("vcp"),
            "turnover": r.get("turnover"), "sector": r.get("sector")}


def load_book(hist_dir: str, start: str = START, today_rows: list = None, today_dates: dict = None) -> dict:
    """{시장: {날짜: {종목: 요약}}}. 같은 날짜는 나중 파일이 이긴다. 오늘 데이터가 맨 마지막에 덮어쓴다."""
    files = []
    for p in glob.glob(os.path.join(hist_dir, "*.html")):
        name = unicodedata.normalize("NFC", os.path.basename(p))
        m = _FNAME_RE.search(name)
        if not name.startswith("SEPA대시보드_") or not m:
            continue
        fdate = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        if fdate.strftime("%Y-%m-%d") < (dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat():
            continue
        files.append((fdate, _RANK.get(m.group(2) or "", 3), m.group(2) or "", p))
    files.sort()
    book = {"KR": {}, "US": {}}

    def add(rows, dates):
        for mk in ("KR", "US"):
            d = dates.get(mk)
            if not d:
                continue
            day = {str(r["ticker"]): _lite(r) for r in rows if r.get("market") == mk and r.get("price")}
            if len(day) >= 100:                  # 데이터 수집 실패한 화면은 버린다
                book[mk][d] = day

    for fdate, _, sess, p in files:
        with open(p, encoding="utf-8") as f:
            html = f.read()
        rows = _rows(html)
        if rows:
            add(rows, _dates(html, fdate, sess))
    if today_rows and today_dates:
        add(today_rows, today_dates)
    return book


# ── 추천 만들기 ─────────────────────────────────────────────
def _rule_hit(m: str, r: dict) -> bool:
    try:
        return (r["pass"] and r.get("vcp") in RULE["vcp"]
                and r.get("pivot") is not None and RULE["pivot_lo"] <= r["pivot"] <= RULE["pivot_hi"]
                and (r.get("ma50") is None or r["ma50"] < RULE["ma50_max"])
                and (r.get("turnover") or 0) >= RULE["turnover_min"][m]
                and r.get("pivotPrice"))
    except Exception:
        return False


def load_sheet_picks(hist_dir: str) -> list:
    try:
        with open(os.path.join(hist_dir, PICKS_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data.get("picks", []) if isinstance(data, dict) else data
    except Exception:
        return []


def _simulate(rec: dict, days: list, series: dict, last_day: str) -> dict:
    """한 추천의 매수·손절·보유 결과. series = {날짜: 종가}"""
    pivot, first = rec["pivot"], rec["first_day"]
    tradable = [d for d in days if d >= first and d in series]
    out = dict(rec, status="대기", entry_date=None, entry_price=None, stop=None,
               exit_date=None, exit_price=None, last_date=None, last_price=None, ret=None, hold_days=None)
    if not tradable:
        return out
    out["last_date"], out["last_price"] = tradable[-1], series[tradable[-1]]
    entry_i = None
    ran_away = False
    for i, d in enumerate(tradable):
        p = series[d]
        if p > pivot * (1 + ENTRY_BAND):
            ran_away = True                           # 그날은 추격 금지, 다음 날 다시 본다
        elif p >= pivot:
            entry_i = i
            break
        if i + 1 >= WATCH_DAYS:
            out.update(status="놓침" if ran_away else "만료", exit_date=d)
            return out
    if entry_i is None:
        return out                                    # 아직 대기
    e_day = tradable[entry_i]
    entry = series[e_day]
    stop = max(x for x in (rec.get("stop_scan") or 0, entry * (1 - MAX_LOSS)))
    out.update(status="보유", entry_date=e_day, entry_price=entry)
    peak = entry
    for j, d in enumerate(tradable[entry_i + 1:], start=1):
        p = series[d]
        if p <= stop:
            out.update(status="손절" if p < entry * 0.995 else "본전청산", exit_date=d, exit_price=p,
                       stop=round(stop, 4), ret=round((p / entry - 1) * 100, 2), hold_days=j)
            return out
        peak = max(peak, p)
        if peak >= entry * (1 + BREAKEVEN_AT):
            stop = max(stop, entry)
    lp = series[tradable[-1]]
    out.update(stop=round(stop, 4), ret=round((lp / entry - 1) * 100, 2),
               hold_days=len(tradable) - 1 - entry_i)
    return out


def build(hist_dir: str, today_rows: list = None, today_dates: dict = None, start: str = START) -> dict:
    book = load_book(hist_dir, start, today_rows, today_dates)
    recs = []
    # 1) 스캐너 규칙 추천 — 같은 종목은 앞선 추천의 결과가 끝난(손절·놓침·만료) 뒤에만 다시 기록
    for m in ("KR", "US"):
        days = sorted(book[m])
        last_rec = {}
        for i, d in enumerate(days):
            if d < start:
                continue
            for t, r in book[m][d].items():
                if not _rule_hit(m, r):
                    continue
                prev = last_rec.get(t)
                if prev:
                    upto = days[:i + 1]
                    series = {dd: book[m][dd][t]["p"] for dd in upto if t in book[m][dd]}
                    if _simulate(prev, upto, series, d)["status"] in ("대기", "보유"):
                        continue
                nxt = days[i + 1] if i + 1 < len(days) else "9999-12-31"
                rec = {"src": "rule", "market": m, "ticker": t, "name": r.get("name"),
                       "sector": r.get("sector"), "rec_date": d, "first_day": nxt,
                       "pivot": r["pivotPrice"], "stop_scan": r.get("stop"),
                       "risk": r.get("risk"), "rank": "", "note": ""}
                recs.append(rec)
                last_rec[t] = rec
    # 2) 액션시트 추천
    for pk in load_sheet_picks(hist_dir):
        m, t = pk.get("market"), str(pk.get("ticker"))
        if m not in book or not pk.get("date") or not pk.get("pivot"):
            continue
        days = sorted(book[m])
        prior = [d for d in days if d < pk["date"] and t in book[m][d]]
        rec_close = book[m][prior[-1]][t]["p"] if prior else None
        stop_scan = pk.get("stop")
        if not stop_scan and pk.get("risk") and rec_close:
            stop_scan = rec_close * (1 - float(pk["risk"]) / 100)
        name = pk.get("name") or (book[m][prior[-1]][t]["name"] if prior else t)
        recs.append({"src": "sheet", "market": m, "ticker": t, "name": name, "sector": None,
                     "rec_date": pk["date"], "first_day": pk["date"], "pivot": float(pk["pivot"]),
                     "stop_scan": stop_scan, "risk": pk.get("risk"), "rank": pk.get("rank", ""),
                     "note": pk.get("note", "")})
    # 최종 결과
    entries = []
    for rec in recs:
        m = rec["market"]
        days = sorted(book[m])
        series = {d: book[m][d][rec["ticker"]]["p"] for d in days if rec["ticker"] in book[m][d]}
        entries.append(_simulate(rec, days, series, days[-1] if days else None))
    entries.sort(key=lambda x: (x["rec_date"], x["src"], x["market"], x["ticker"]), reverse=True)
    return {"generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST"),
            "start": start,
            "last_dates": {m: (sorted(book[m])[-1] if book[m] else None) for m in ("KR", "US")},
            "rules": {"entry_band_pct": ENTRY_BAND * 100, "watch_days": WATCH_DAYS,
                      "max_loss_pct": MAX_LOSS * 100, "breakeven_at_pct": BREAKEVEN_AT * 100, "rule_pick": RULE},
            "summary": summarize(entries), "entries": entries}


def summarize(entries: list) -> dict:
    out = {}
    for src in ("rule", "sheet", "all"):
        for m in ("KR", "US", "ALL"):
            xs = [e for e in entries if (src == "all" or e["src"] == src) and (m == "ALL" or e["market"] == m)]
            filled = [e for e in xs if e["entry_price"]]
            rets = [e["ret"] for e in filled if e["ret"] is not None]
            avg = sum(rets) / len(rets) if rets else None
            out[f"{src}:{m}"] = {
                "recs": len(xs), "filled": len(filled),
                "open": sum(1 for e in xs if e["status"] == "보유"),
                "stopped": sum(1 for e in xs if e["status"] in ("손절", "본전청산")),
                "waiting": sum(1 for e in xs if e["status"] == "대기"),
                "missed": sum(1 for e in xs if e["status"] == "놓침"),
                "expired": sum(1 for e in xs if e["status"] == "만료"),
                "wins": sum(1 for r in rets if r > 0),
                "avg_ret": round(avg, 2) if avg is not None else None,
                "best": round(max(rets), 2) if rets else None,
                "worst": round(min(rets), 2) if rets else None,
                "krw_10m": round(10_000_000 * (1 + avg / 100)) if avg is not None else None,
            }
    return out


def update(hist_dir: str, today_rows: list = None, today_dates: dict = None) -> dict:
    ledger = build(hist_dir, today_rows, today_dates)
    path = os.path.join(hist_dir, LEDGER_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return ledger


if __name__ == "__main__":
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    hd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "history")
    lg = update(hd)
    for k, v in lg["summary"].items():
        if v["recs"]:
            print(f"{k:<10} 추천 {v['recs']:>3} 체결 {v['filled']:>3} 보유 {v['open']:>2} 손절 {v['stopped']:>2} "
                  f"대기 {v['waiting']:>2} 놓침 {v['missed']:>2} 만료 {v['expired']:>2} 평균 {v['avg_ret']} 1천만원→{v['krw_10m']}")
