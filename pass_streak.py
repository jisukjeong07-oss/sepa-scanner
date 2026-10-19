# -*- coding: utf-8 -*-
"""
pass_streak.py — 8조건(트렌드템플릿) 연속 통과일수 장부.

[2026-10-19] 대시보드 "통과일수(D)" 열을 위해 만들었다.

무엇을 세나 (엄격 방식)
- 그 시장의 거래일 기준으로 "오늘까지 며칠 연속 8조건을 통과했는가".
  하루라도 빠지면 다음 통과일이 다시 1일째다.
- 한국은 한국 거래일, 미국은 뉴욕 거래일로 따로 센다. 같은 거래일에 실행이
  여러 번 있으면(새벽 미국마감·오후 장마감) 그 날짜는 한 번만 센다 —
  같은 날짜를 다시 기록하면 덮어쓴다.
- 재진입(Re): 이번 연속 통과가 1~3일째이고, 그 직전 10거래일 안에 통과했던
  기록이 있으면 "Re1"처럼 표시한다. 10일 이상 오래 버틴 종목에 몇 주 전
  하루 빠졌던 일을 표시하는 건 의미가 없어서 3일째까지만 붙인다.

장부 파일: history/pass_history.json (daily.yml이 history/ 를 통째로 커밋한다)
  {"version": 1,
   "KR": {"2026-10-19": {"p": [통과 종목코드...],
                         "f": {종목코드: [미달 조건 이름...]}}, ...},
   "US": {...}}
  - "p": 그날 통과한 종목 전부.
  - "f": 그날 통과하지 못한 종목 중 "최근 10거래일 안에 통과한 적이 있는" 종목의
    미달 사유만. 마우스를 올렸을 때 "10/15·10/16 탈락 — ○○ 미달"을 보여주는 용도라
    전 종목을 저장할 필요가 없다(파일 크기 절약).

시장별 "그날 날짜"는 호출하는 쪽이 정한다. make_dashboard.build()는 CSV의
price_date 중 그 시장의 가장 늦은 날짜를 쓴다(달력 추정이 아니라 실제 데이터 날짜).
"""
from __future__ import annotations

import os
import json

FILE = "pass_history.json"
LOOKBACK = 10      # 재진입 판정·탈락 사유 보관 범위(거래일)
RE_MAX = 3         # 재진입 표시는 연속 통과 3일째까지만
KEEP_DAYS = 400    # 시장별로 보관할 최대 거래일 수


def load(hist_dir: str) -> dict:
    path = os.path.join(hist_dir, FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("KR", {})
            data.setdefault("US", {})
            return data
    except Exception:
        pass
    return {"version": 1, "KR": {}, "US": {}}


def save(hist_dir: str, data: dict):
    os.makedirs(hist_dir, exist_ok=True)
    for m in ("KR", "US"):
        days = sorted(data.get(m, {}))
        for d in days[:-KEEP_DAYS]:
            data[m].pop(d, None)
    path = os.path.join(hist_dir, FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def record(data: dict, market: str, date: str, passed: set, fails: dict):
    """
    market 시장의 date(YYYY-MM-DD) 결과를 기록한다. 같은 날짜가 있으면 덮어쓴다.
    passed: 그날 통과한 종목코드 집합
    fails : {종목코드: [미달 조건 이름...]} — 통과 못 한 종목 전부를 넘겨도 된다.
            여기서 최근 LOOKBACK 거래일 안에 통과한 적 있는 종목만 남긴다.
    """
    book = data.setdefault(market, {})
    earlier = [d for d in sorted(book) if d < date][-LOOKBACK:]
    recent = set()
    for d in earlier:
        recent.update(book[d].get("p", []))
    keep_f = {t: list(v) for t, v in (fails or {}).items()
              if t in recent and t not in passed and v}
    book[date] = {"p": sorted(passed), "f": keep_f}


def compute(data: dict, market: str, date: str) -> dict:
    """
    date 기준 통과 종목 각각의 연속 통과 정보.
    반환: {종목코드: {"d": 연속일수, "label": "3" 또는 "Re1", "re": bool,
                      "start": 이번 통과 시작일, "prev": [시작, 끝, 일수] 또는 None,
                      "gap": [탈락한 날짜...], "why": [탈락 사유...],
                      "from_first": 장부 첫날부터 계속 통과 중이면 True}}
    """
    book = data.get(market, {})
    days = [d for d in sorted(book) if d <= date]
    if not days or days[-1] != date:
        return {}
    sets = [set(book[d].get("p", [])) for d in days]
    last = len(days) - 1
    out = {}
    for t in sets[last]:
        i = last
        while i >= 0 and t in sets[i]:
            i -= 1
        n = last - i
        start_idx = i + 1
        # 직전 통과 구간 찾기 (이번 시작일 이전 LOOKBACK 거래일 안)
        prev = None
        gap = []
        j = start_idx - 1
        lo = max(0, start_idx - LOOKBACK)
        while j >= lo and t not in sets[j]:
            gap.append(days[j])
            j -= 1
        if j >= lo and t in sets[j]:
            k = j
            while k >= 0 and t in sets[k]:
                k -= 1
            prev = [days[k + 1], days[j], j - k]
        else:
            gap = []
        re_ = prev is not None and n <= RE_MAX
        why = []
        if re_:
            for g in reversed(gap):
                for reason in book[g].get("f", {}).get(t, []):
                    if reason not in why:
                        why.append(reason)
        out[t] = {
            "d": n,
            "label": (f"Re{n}" if re_ else str(n)),
            "re": re_,
            "start": days[start_idx],
            "prev": prev,
            "gap": list(reversed(gap)) if re_ else [],
            "why": why,
            "from_first": start_idx == 0,
        }
    return out


def summary(streaks: dict) -> dict:
    """오늘 새로 통과(1일째) 종목 수: 처음 / 재진입."""
    new = [v for v in streaks.values() if v["d"] == 1]
    return {"new": len(new), "first": sum(1 for v in new if not v["re"]),
            "re": sum(1 for v in new if v["re"])}
