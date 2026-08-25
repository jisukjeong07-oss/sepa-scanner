# -*- coding: utf-8 -*-
"""
SEPA 2단계: DART 기반 펀더멘털 스크리닝 (한국)

OpenDART '다중회사 주요계정' API(fnlttMultiAcnt) 사용.
- 회사 100개씩 묶어서 1회 호출 (초과 시 에러 021)
- 일일 요청 한도 20,000건 (초과 시 에러 020)
- 2015년 이후 데이터 제공
- 금융업(은행/보험/증권)은 주요계정 API 대상에서 제외됨

API 키 발급: https://opendart.fss.or.kr  (무료)
환경변수 DART_API_KEY 에 설정하거나 --key 인자로 전달.

사용법:
  export DART_API_KEY="발급받은40자리키"
  python dart_fundamentals.py --tickers 005930,000660,034020
  python dart_fundamentals.py --from-scan output/sepa_scan_20260820.csv
"""

import os
import io
import json
import time
import zipfile
import argparse
import datetime as dt
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

# ── .env 자동 로딩 ────────────────────────────────────────────
# 같은 폴더의 .env 파일에서 API 키를 읽어 환경변수로 등록한다.
# python-dotenv 가 설치돼 있으면 그것을 쓰고, 없으면 직접 파싱한다.
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




BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE, "cache")
OUT_DIR = os.path.join(BASE, "output")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

API_ROOT = "https://opendart.fss.or.kr/api"

# 보고서 코드 (분기 순서대로)
REPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}  # 1분기 / 반기 / 3분기 / 사업보고서

# ─────────────────────────────────────────────────────────────
# 미네르비니 SEPA 2단계 기준 (조정 가능)
# ─────────────────────────────────────────────────────────────
MIN_EPS_GROWTH = 25.0      # 최근 분기 순이익 YoY 증가율 하한 (%)
MIN_REV_GROWTH = 20.0      # 최근 분기 매출 YoY 증가율 하한 (%)
REQUIRE_ACCEL = False      # True면 이익 증가율 가속(직전분기 대비 확대) 필수
MIN_QUARTERS = 5           # 최소 확보 분기 수 (YoY 계산에 필요)


def _key(explicit=None) -> str:
    k = explicit or os.environ.get("DART_API_KEY", "").strip()
    if not k:
        raise RuntimeError(
            "DART API 키가 없습니다. https://opendart.fss.or.kr 에서 무료 발급 후\n"
            '  export DART_API_KEY="발급받은40자리키"'
        )
    return k


# ═════════════════════════════════════════════════════════════
# 1. 종목코드 → 고유번호(corp_code) 매핑
# ═════════════════════════════════════════════════════════════

def corp_map(api_key: str, refresh_days: int = 7) -> dict:
    """
    corpCode.xml(zip)을 받아 {종목코드6자리: 고유번호8자리} 매핑 생성.
    전체 공시대상 기업이라 용량이 크므로 로컬 캐시(기본 7일) 사용.
    """
    cache = os.path.join(CACHE_DIR, "corp_map.json")
    if os.path.exists(cache):
        age = (time.time() - os.path.getmtime(cache)) / 86400
        if age < refresh_days:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)

    print("[DART] 고유번호 목록 다운로드...")
    r = requests.get(f"{API_ROOT}/corpCode.xml",
                     params={"crtfc_key": api_key}, timeout=60)
    r.raise_for_status()

    # 키 오류 시 zip이 아니라 XML 에러 응답이 옴
    if not r.content[:2] == b"PK":
        raise RuntimeError(f"corpCode 응답이 zip이 아닙니다: {r.text[:300]}")

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml_bytes = z.read("CORPCODE.xml")

    root = ET.fromstring(xml_bytes)
    mp = {}
    for item in root.iter("list"):
        sc = (item.findtext("stock_code") or "").strip()
        cc = (item.findtext("corp_code") or "").strip()
        if sc and sc != " " and len(sc) == 6:      # 상장사만
            mp[sc] = cc

    with open(cache, "w", encoding="utf-8") as f:
        json.dump(mp, f)
    print(f"[DART] 상장사 {len(mp):,}개 매핑 캐시 저장")
    return mp


# ═════════════════════════════════════════════════════════════
# 2. 재무데이터 수집
# ═════════════════════════════════════════════════════════════

def _to_num(s) -> float:
    if s is None:
        return np.nan
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def _classify(account_nm: str) -> str | None:
    """계정명이 회사마다 조금씩 달라서 부분일치로 정규화."""
    a = str(account_nm).replace(" ", "")
    if "매출" in a or "영업수익" in a:
        return "revenue"
    if "영업이익" in a:
        return "op_income"
    if "당기순이익" in a or "분기순이익" in a or "반기순이익" in a:
        return "net_income"
    return None


def fetch_accounts(corp_codes: list, year: int, quarter: int, api_key: str) -> pd.DataFrame:
    """특정 연도/분기 보고서의 주요계정을 최대 100개사씩 조회."""
    rows = []
    for i in range(0, len(corp_codes), 100):
        batch = corp_codes[i:i + 100]
        params = {
            "crtfc_key": api_key,
            "corp_code": ",".join(batch),
            "bsns_year": str(year),
            "reprt_code": REPRT[quarter],
        }
        try:
            r = requests.get(f"{API_ROOT}/fnlttMultiAcnt.json", params=params, timeout=30)
            js = r.json()
        except Exception as e:
            print(f"  [warn] {year}Q{quarter} batch{i}: {e}")
            continue

        status = js.get("status")
        if status == "013":          # 조회된 데이터 없음 (미공시 분기 등) — 정상 상황
            continue
        if status != "000":
            print(f"  [warn] {year}Q{quarter} status={status} {js.get('message')}")
            continue

        for it in js.get("list", []):
            if it.get("sj_div") != "IS":        # 손익계산서만
                continue
            kind = _classify(it.get("account_nm"))
            if kind is None:
                continue
            rows.append({
                "stock_code": str(it.get("stock_code", "")).strip(),
                "fs_div": it.get("fs_div"),
                "kind": kind,
                "year": year,
                "quarter": quarter,
                # 누적금액 우선. 분기보고서의 thstrm_amount는 3개월/누적 표기가
                # 회사마다 달라 신뢰도가 낮으므로 누적을 받아 직접 차분한다.
                "cum": _to_num(it.get("thstrm_add_amount")),
                "amt": _to_num(it.get("thstrm_amount")),
            })
        time.sleep(0.15)
    return pd.DataFrame(rows)


def build_quarterly(tickers: list, api_key: str, years: int = 3) -> pd.DataFrame:
    """
    최근 N년치 분기 손익을 수집해 '순수 3개월 분기값'으로 변환.

    분기값 = 누적(당분기) - 누적(전분기)
      Q1 = 1분기 누적
      Q2 = 반기 누적 - Q1
      Q3 = 3분기 누적 - 반기 누적
      Q4 = 연간 - 3분기 누적
    이 방식이 thstrm_amount를 그대로 쓰는 것보다 회사간 일관성이 높다.
    """
    mp = corp_map(api_key)
    pairs = [(t, mp[t]) for t in tickers if t in mp]
    missing = [t for t in tickers if t not in mp]
    if missing:
        print(f"[DART] 고유번호 미매칭 {len(missing)}종목: {missing[:10]}")
    if not pairs:
        return pd.DataFrame()

    codes = [c for _, c in pairs]
    this_year = dt.date.today().year
    frames = []
    for y in range(this_year - years + 1, this_year + 1):
        for q in (1, 2, 3, 4):
            print(f"[DART] {y} Q{q} 수집...")
            df = fetch_accounts(codes, y, q, api_key)
            if not df.empty:
                frames.append(df)

    if not frames:
        return pd.DataFrame()

    raw = pd.concat(frames, ignore_index=True)

    # 연결(CFS) 우선, 없으면 개별(OFS)
    raw["pref"] = (raw["fs_div"] == "CFS").astype(int)
    raw = (raw.sort_values("pref", ascending=False)
              .drop_duplicates(["stock_code", "kind", "year", "quarter"], keep="first"))

    # 누적금액이 비어 있으면(사업보고서 등) 당기금액을 누적으로 간주
    raw["cum"] = raw["cum"].fillna(raw["amt"])

    wide = raw.pivot_table(index=["stock_code", "year", "quarter"],
                           columns="kind", values="cum").reset_index()

    return cum_to_quarterly(wide)


def cum_to_quarterly(wide: pd.DataFrame) -> pd.DataFrame:
    """
    누적 손익 → 순수 3개월 분기 손익으로 차분.
      Q1 = 1분기 누적
      Q2 = 반기 누적 - Q1 누적
      Q3 = 3분기 누적 - 반기 누적
      Q4 = 연간 - 3분기 누적
    직전 분기 데이터가 없으면(미공시 등) NaN 처리해 잘못된 값이 흘러들지 않게 한다.
    """
    cols = ("revenue", "op_income", "net_income")
    wide = wide.sort_values(["stock_code", "year", "quarter"])
    out = []
    for sc, g in wide.groupby("stock_code"):
        g = g.set_index(["year", "quarter"]).sort_index()
        for col in cols:
            if col not in g.columns:
                g[col] = np.nan
        for (y, q), row in g.iterrows():
            rec = {"stock_code": sc, "year": int(y), "quarter": int(q),
                   "period": int(y) * 10 + int(q)}
            for col in cols:
                cur = row[col]
                if q == 1:
                    val = cur
                else:
                    prev = g.loc[(y, q - 1), col] if (y, q - 1) in g.index else np.nan
                    val = cur - prev if pd.notna(cur) and pd.notna(prev) else np.nan
                rec[col] = val
            out.append(rec)

    return pd.DataFrame(out).sort_values(["stock_code", "period"]).reset_index(drop=True)


# ═════════════════════════════════════════════════════════════
# 3. SEPA 2단계 스크리닝
# ═════════════════════════════════════════════════════════════

def screen_fundamentals(qdf: pd.DataFrame,
                        min_eps: float = MIN_EPS_GROWTH,
                        min_rev: float = MIN_REV_GROWTH,
                        require_accel: bool = REQUIRE_ACCEL) -> pd.DataFrame:
    """
    최근 분기 기준 YoY 성장률과 가속 여부를 계산.
    주의: 주요계정 API에 EPS가 없어 '당기순이익 증가율'을 EPS 증가율의 대용치로 사용.
          증자·감자가 있었던 종목은 실제 EPS 증가율과 괴리가 생길 수 있음.
    """
    recs = []
    for sc, g in qdf.groupby("stock_code"):
        g = g.sort_values("period")
        if len(g) < MIN_QUARTERS:
            continue

        def yoy(col, back=0):
            """back=0이면 최근 분기, back=1이면 직전 분기의 YoY."""
            if len(g) < 5 + back:
                return np.nan
            cur = g.iloc[-1 - back]
            tgt_period = (cur["year"] - 1) * 10 + cur["quarter"]
            prior = g[g["period"] == tgt_period]
            if prior.empty:
                return np.nan
            p, c = prior.iloc[0][col], cur[col]
            if pd.isna(p) or pd.isna(c) or p <= 0:   # 적자 전환 등은 비율 계산 불가
                return np.nan
            return (c / p - 1) * 100

        ni_now, ni_prev = yoy("net_income"), yoy("net_income", 1)
        rev_now, rev_prev = yoy("revenue"), yoy("revenue", 1)
        last = g.iloc[-1]

        accel = (pd.notna(ni_now) and pd.notna(ni_prev) and ni_now > ni_prev)
        pass_eps = pd.notna(ni_now) and ni_now >= min_eps
        pass_rev = pd.notna(rev_now) and rev_now >= min_rev
        passed = pass_eps and pass_rev and (accel if require_accel else True)

        recs.append({
            "stock_code": sc,
            "최근분기": f"{int(last['year'])}Q{int(last['quarter'])}",
            "순이익증가율_YoY_%": round(ni_now, 1) if pd.notna(ni_now) else None,
            "직전분기_순이익증가율_%": round(ni_prev, 1) if pd.notna(ni_prev) else None,
            "매출증가율_YoY_%": round(rev_now, 1) if pd.notna(rev_now) else None,
            "직전분기_매출증가율_%": round(rev_prev, 1) if pd.notna(rev_prev) else None,
            "이익가속": accel,
            "F_PASS": passed,
        })

    return pd.DataFrame(recs).set_index("stock_code")


# ═════════════════════════════════════════════════════════════
# 4. 1단계 스캔 결과와 결합
# ═════════════════════════════════════════════════════════════

def combine(scan_csv: str, api_key: str, out_csv: str = None) -> pd.DataFrame:
    """1단계(트렌드템플릿) 통과 한국 종목만 2단계 펀더멘털 검증."""
    scan = pd.read_csv(scan_csv, index_col=0, encoding="utf-8-sig")
    scan.index = [str(i).zfill(6) if str(i).isdigit() else str(i) for i in scan.index]

    kr_pass = scan[(scan["market"] == "KR") & (scan["PASS"] == True)]
    if kr_pass.empty:
        print("1단계 통과 한국 종목이 없습니다.")
        return pd.DataFrame()

    tickers = list(kr_pass.index)
    print(f"[DART] 1단계 통과 {len(tickers)}종목 펀더멘털 조회")

    qdf = build_quarterly(tickers, api_key)
    if qdf.empty:
        print("재무데이터를 가져오지 못했습니다.")
        return pd.DataFrame()

    fund = screen_fundamentals(qdf)
    merged = kr_pass.join(fund, how="left")
    merged["SEPA12_PASS"] = merged["PASS"] & merged["F_PASS"].fillna(False)

    out_csv = out_csv or os.path.join(
        OUT_DIR, f"sepa_stage2_{dt.date.today():%Y%m%d}.csv")
    merged.to_csv(out_csv, encoding="utf-8-sig")

    final = merged[merged["SEPA12_PASS"]]
    print(f"\n{'='*60}")
    print(f"1단계 {len(kr_pass)}종목 → 1+2단계 모두 통과 {len(final)}종목")
    print(f"저장: {out_csv}")
    print(f"{'='*60}")
    if not final.empty:
        cols = ["name", "price", "RS", "순이익증가율_YoY_%", "매출증가율_YoY_%", "이익가속"]
        print(final[[c for c in cols if c in final.columns]].to_string())
    return merged


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None)
    ap.add_argument("--tickers", default=None, help="쉼표구분 종목코드")
    ap.add_argument("--from-scan", default=None, help="1단계 스캔 CSV 경로")
    a = ap.parse_args()
    k = _key(a.key)

    if a.tickers:
        ts = [t.strip().zfill(6) for t in a.tickers.split(",")]
        q = build_quarterly(ts, k)
        print(q.to_string())
        print()
        print(screen_fundamentals(q).to_string())
    else:
        scan = a.from_scan or os.path.join(
            OUT_DIR, f"sepa_scan_{dt.date.today():%Y%m%d}.csv")
        combine(scan, k)
