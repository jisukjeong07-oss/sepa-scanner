# -*- coding: utf-8 -*-
"""
backfill_pass_history.py — 저장된 대시보드(history/SEPA대시보드_*.html)를 훑어
history/pass_history.json(8조건 연속 통과 장부)을 처음부터 다시 만든다. 1회용.

[2026-10-19] 통과일수(D) 열을 도입하면서 첫날부터 정확한 일수가 나오도록
과거 기록을 채우는 용도. 다시 돌려도 결과가 같다(장부를 새로 만든다).

각 파일이 "한국·미국 각각 몇 일 종가"인지 정하는 방법
- 9/12 이후 파일: 화면의 기준일 줄(basis-item: "한국 2026-10-19(월) …",
  "미국 2026-10-16(금) …")을 그대로 읽는다.
- 그 이전 파일(기준일 줄 없음): 파일 날짜 D와 세션으로 추정한다.
    HIST(소급)          : 한국 D, 미국 D (그 시장이 열린 날일 때만)
    AM(장전)            : 한국 D 이전 마지막 개장일, 미국 D 이전 마지막 개장일
    PM(장마감)·세션 없음 : 한국 D(개장일이면), 미국 D 이전 마지막 개장일
    MANUAL              : 추정 불가 — 건너뜀
- 같은 시장·같은 날짜가 여러 파일에 있으면 나중 파일이 이긴다
  (파일 날짜, 그다음 HIST < AM < MANUAL < PM < US 순으로 처리).
- 종목이 0개인 파일(데이터 수집 실패일)은 건너뛴다.

사용: python3 backfill_pass_history.py            (history/ 기준)
      python3 backfill_pass_history.py --dry-run  (파일은 쓰지 않고 요약만)
"""
import os
import re
import sys
import json
import glob
import datetime as dt
import unicodedata

import market_calendar as mc
import pass_streak as ps

BASE = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(BASE, "history")
RANK = {"HIST": 0, "AM": 1, "MANUAL": 2, "": 3, "PM": 3, "US": 4}
FNAME_RE = re.compile(r"_(\d{8})(?:_(AM|PM|US|MANUAL|HIST))?\.html$")


def _rows(html: str) -> list:
    i = html.find("const DATA = ")
    if i < 0:
        return []
    try:
        rows, _ = json.JSONDecoder().raw_decode(html, i + len("const DATA = "))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _prev_open(status_fn, d: dt.date) -> dt.date:
    d = d - dt.timedelta(days=1)
    for _ in range(10):
        if status_fn(d.strftime("%Y-%m-%d"))["open"]:
            return d
        d -= dt.timedelta(days=1)
    return None


def _dates(html: str, fdate: dt.date, session: str) -> dict:
    basis = " ".join(re.findall(r'basis-item">([^<]*)', html))
    kr = re.search(r"한국 (\d{4}-\d{2}-\d{2})", basis)
    us = re.search(r"미국 (\d{4}-\d{2}-\d{2})", basis)
    if kr or us:
        return {"KR": kr.group(1) if kr else None, "US": us.group(1) if us else None}
    ds = fdate.strftime("%Y-%m-%d")
    kr_open = mc.kr_status(ds)["open"]
    us_open = mc.us_status(ds)["open"]
    if session == "HIST":
        return {"KR": ds if kr_open else None, "US": ds if us_open else None}
    if session == "AM":
        k, u = _prev_open(mc.kr_status, fdate), _prev_open(mc.us_status, fdate)
        return {"KR": k and k.strftime("%Y-%m-%d"), "US": u and u.strftime("%Y-%m-%d")}
    if session in ("PM", ""):
        u = _prev_open(mc.us_status, fdate)
        return {"KR": ds if kr_open else None, "US": u and u.strftime("%Y-%m-%d")}
    return {"KR": None, "US": None}


def main(dry_run=False):
    files = []
    for p in glob.glob(os.path.join(HIST_DIR, "*.html")):
        name = unicodedata.normalize("NFC", os.path.basename(p))
        m = FNAME_RE.search(name)
        if not name.startswith("SEPA대시보드_") or not m:
            continue
        fdate = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        files.append((fdate, RANK.get(m.group(2) or "", 3), m.group(2) or "", p, name))
    files.sort()

    raw = {"KR": {}, "US": {}}   # 시장 → 날짜 → (통과, 미달사유). 나중 파일이 덮어쓴다.
    log = []
    for fdate, _, session, path, name in files:
        with open(path, encoding="utf-8") as f:
            html = f.read()
        rows = _rows(html)
        if not rows:
            log.append(f"  건너뜀(종목 0개) {name}")
            continue
        dates = _dates(html, fdate, session)
        for market in ("KR", "US"):
            d = dates.get(market)
            if not d:
                continue
            mrows = [r for r in rows if r.get("market") == market]
            if not mrows:
                continue
            passed = {str(r["ticker"]) for r in mrows if r.get("pass")}
            fails = {str(r["ticker"]): r.get("fails") or [] for r in mrows if not r.get("pass")}
            raw[market][d] = (passed, fails)
            log.append(f"  {name:<36} {market} {d}  통과 {len(passed):>3}")

    # 이상치 날짜 제외: 통과 종목 수가 앞뒤 3거래일 중앙값의 40% 미만이거나 2배 초과면
    # 데이터 수집이 일부 실패했거나(예: 미국 9/14 통과 0개, 8/31 8개) 기준이 달랐던 날로 보고
    # 기록하지 않는다. 기록하지 않은 날은 "모르는 날"이라 연속 계산에서 건너뛴다.
    for m in ("KR", "US"):
        ds = sorted(raw[m])
        cnt = {d: len(raw[m][d][0]) for d in ds}
        for i, d in enumerate(ds):
            nb = sorted(cnt[x] for x in ds[max(0, i - 3):i] + ds[i + 1:i + 4])
            if not nb:
                continue
            med = nb[len(nb) // 2]
            if cnt[d] < 0.4 * med or cnt[d] > 2.0 * med:
                log.append(f"  이상치 제외 {m} {d}: 통과 {cnt[d]}개 (주변 중앙값 {med}개)")
                raw[m].pop(d)

    # 날짜순으로 기록해야 "최근 10거래일 안에 통과한 종목의 탈락 사유"가 정확하다.
    data = {"version": 1, "KR": {}, "US": {}}
    for m in ("KR", "US"):
        for d in sorted(raw[m]):
            ps.record(data, m, d, *raw[m][d])

    print("\n".join(log))
    for m in ("KR", "US"):
        days = sorted(data[m])
        print(f"{m}: {len(days)}거래일 ({days[0] if days else '-'} ~ {days[-1] if days else '-'})")
    if not dry_run:
        ps.save(HIST_DIR, data)
        print(f"저장: {os.path.join(HIST_DIR, ps.FILE)}")
    return data


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
