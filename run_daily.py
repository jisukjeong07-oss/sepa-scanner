# -*- coding: utf-8 -*-
"""
매일 실행용: 스캔 → CSV → PDF 리포트 → HTML 대시보드

사용법:
  python3 run_daily.py                      # 한국+미국 전체
  python3 run_daily.py --market US          # 미국만
  python3 run_daily.py --market KR --min-rs 80
  python3 run_daily.py --no-open            # 브라우저 자동 실행 안 함

cron 등록 예시 (평일 18:30):
  30 18 * * 1-5 cd /파일이있는폴더 && /usr/bin/python3 run_daily.py --no-open >> run.log 2>&1
"""

import os
import argparse
import datetime as dt
import traceback


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

from sepa_scanner import run, OUT_DIR
import make_report
import make_dashboard


def main(market="ALL", min_rs=70, kr_source="fdr", open_browser=True):
    stamp = dt.date.today().strftime("%Y%m%d")
    print(f"\n===== SEPA 일일 스캔 {dt.datetime.now():%Y-%m-%d %H:%M} =====")
    print(f"시장: {market} / RS 기준: {min_rs} 이상")

    # 1단계: 트렌드템플릿 스캔
    run(market, min_rs, kr_source)
    csv_path = os.path.join(OUT_DIR, f"sepa_scan_{stamp}.csv")

    # 2단계: DART 펀더멘털 (한국 포함 + 키가 있을 때만)
    stage2_path = None
    if market in ("KR", "ALL") and os.environ.get("DART_API_KEY"):
        try:
            import dart_fundamentals
            dart_fundamentals.combine(csv_path, os.environ["DART_API_KEY"])
            p = os.path.join(OUT_DIR, f"sepa_stage2_{stamp}.csv")
            stage2_path = p if os.path.exists(p) else None
        except Exception as e:
            # 2단계 실패가 리포트 생성을 막지 않게 한다
            print(f"[경고] 2단계 펀더멘털 건너뜀: {e}")
    elif market in ("KR", "ALL"):
        print("[안내] DART_API_KEY 미설정 — 1단계 결과만 사용합니다.")

    # 산출물
    pdf_path = make_report.build(csv_path, stage2_csv=stage2_path)
    html_path = make_dashboard.build(csv_path, open_browser=open_browser)

    print(f"\n완료")
    print(f"  PDF        : {pdf_path}")
    print(f"  대시보드   : {html_path}")
    print(f"  원본 CSV   : {csv_path}")
    return html_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="ALL", choices=["KR", "US", "ALL"])
    ap.add_argument("--min-rs", type=int, default=70)
    ap.add_argument("--kr-source", default="fdr", choices=["fdr", "pykrx"])
    ap.add_argument("--no-open", action="store_true", help="브라우저 자동 실행 안 함")
    a = ap.parse_args()
    try:
        main(a.market, a.min_rs, a.kr_source, open_browser=not a.no_open)
    except Exception:
        print("\n실패:")
        traceback.print_exc()
        raise
