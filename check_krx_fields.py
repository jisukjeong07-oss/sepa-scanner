# -*- coding: utf-8 -*-
"""
KRX Open API 일별매매정보 응답에 실제로 어떤 필드가 오는지 확인한다.
auto_sepa_new 폴더에서 실행: python3 check_krx_fields.py
"""
import os
import json


# ── .env 자동 로딩 (run_daily.py와 동일한 방식) ──────────────
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

from kr_data_fdr import _krx_open_api_call

# 확실히 데이터가 있는 것으로 확인된 날짜들. 9/11 하나만 실패하면 일시적
# API 문제, 전부 실패하면 키·엔드포인트 자체 문제로 구분하기 위한 대조군.
CANDIDATE_DATES = ["20260911", "20260910", "20260909"]


def _call_with_retry(api_id, bas_dd, tries=3):
    import time
    last_err = None
    for i in range(tries):
        try:
            rows = _krx_open_api_call(api_id, bas_dd)
            if rows:
                return rows, None
            last_err = "빈 응답(OutBlock_1=[])"
        except Exception as e:
            last_err = str(e)[:200]
        if i < tries - 1:
            time.sleep(2)
    return None, last_err

for api_id, label in (("stk_bydd_trd", "KOSPI"), ("ksq_bydd_trd", "KOSDAQ")):
    print("=" * 70)
    print(f"{label} ({api_id})")

    rows = None
    used_date = None
    for bas_dd in CANDIDATE_DATES:
        print(f"  {bas_dd} 시도 중 (최대 3회 재시도)...")
        rows, err = _call_with_retry(api_id, bas_dd)
        if rows:
            used_date = bas_dd
            break
        print(f"    실패: {err}")

    if not rows:
        print(f"  {CANDIDATE_DATES} 전부 실패 — API 키·엔드포인트 자체 문제로 보입니다.")
        continue

    print(f"  성공 (기준일 {used_date}) · 총 {len(rows):,}건")
    print(f"  필드 목록: {list(rows[0].keys())}")
    print()
    print("  --- 첫 종목 원본 ---")
    print(json.dumps(rows[0], ensure_ascii=False, indent=2))
    print()

    # VCP에 필요한 필드가 있는지 개별 확인
    want = ["TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "MKTCAP", "LIST_SHRS"]
    print("  --- 필요한 필드 존재 여부 ---")
    for w in want:
        print(f"    {w}: {'있음' if w in rows[0] else '없음'}")
    print()
