# -*- coding: utf-8 -*-
"""
매일 실행용: 스캔 → CSV → PDF 리포트 → HTML 대시보드

사용법:
  python3 run_daily.py                          # 한국+미국 전체 (세션: 수동조회)
  python3 run_daily.py --market US               # 미국만
  python3 run_daily.py --session AM               # 장전 스캔으로 표시
  python3 run_daily.py --session PM               # 장마감 스캔으로 표시
  python3 run_daily.py --no-open                  # 브라우저 자동 실행 안 함

과거 날짜 소급 스캔(백필):
  python3 run_daily.py --date 20260824 --no-open
  여러 날짜를 채우고 싶으면 반드시 오래된 날짜부터 순서대로 실행할 것.
  (RS90 최초진입일 기록이 날짜 순서에 의존하는 부분이 있어, 거꾸로 실행하면
   일부 종목의 '진입 후 경과일'이 부정확해질 수 있다)

  한계: 시가총액은 항상 '지금 이 순간' 값이 들어간다(과거 시점 값 불가).
       미국 종목 목록도 위키피디아의 현재 S&P500 구성을 쓰므로, 그 과거
       날짜에 실제로 지수에 속했는지는 반영되지 않는다.

하루 두 번(장전/장마감) 자동 실행은 daily.yml 의 cron 스케줄을 사용한다.
로컬 cron 등록 예시(평일 16:30):
  30 16 * * 1-5 cd /파일이있는폴더 && /usr/bin/python3 run_daily.py --session PM --no-open >> run.log 2>&1
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


def main(market="ALL", min_rs=70, kr_source="fdr", open_browser=True,
        session="MANUAL", date=None):
    session = (session or "MANUAL").upper()
    if date:
        stamp = date
        # 소급 스캔인데 세션을 따로 안 정했으면 자동으로 HIST로 표시한다.
        if session == "MANUAL":
            session = "HIST"
    else:
        stamp = dt.date.today().strftime("%Y%m%d")

    print(f"\n===== SEPA 일일 스캔 {dt.datetime.now():%Y-%m-%d %H:%M} "
          f"[{session}] 대상일자={stamp} =====")
    print(f"시장: {market} / RS 기준: {min_rs} 이상")
    if session == "AM":
        print("[안내] 장전 스캔입니다. 한국 종목은 개장 전이라 사실상 전날 종가와 "
              "같고, 새로 확정되는 것은 간밤 미국 종가입니다.")
    if session == "HIST":
        print("[안내] 과거 시점 소급 스캔입니다. 여러 날짜를 채울 계획이면 "
              "반드시 오래된 날짜부터 순서대로 실행하세요.")

    # 1단계: 트렌드템플릿 스캔
    run(market, min_rs, kr_source, as_of=date)
    csv_path = os.path.join(OUT_DIR, f"sepa_scan_{stamp}.csv")

    # 2단계: DART 펀더멘털 (한국 포함 + 키가 있을 때만)
    stage2_path = None
    if market in ("KR", "ALL") and os.environ.get("DART_API_KEY"):
        try:
            import dart_fundamentals
            out_stage2 = os.path.join(OUT_DIR, f"sepa_stage2_{stamp}.csv")
            dart_fundamentals.combine(csv_path, os.environ["DART_API_KEY"],
                                      out_csv=out_stage2)
            stage2_path = out_stage2 if os.path.exists(out_stage2) else None
        except Exception as e:
            # 2단계 실패가 리포트 생성을 막지 않게 한다
            print(f"[경고] 2단계 펀더멘털 건너뜀: {e}")
    elif market in ("KR", "ALL"):
        print("[안내] DART_API_KEY 미설정 — 1단계 결과만 사용합니다.")

    # 산출물 (파일명에 세션이 붙어 장전/장마감/소급 기록이 각각 남는다)
    pdf_path = make_report.build(csv_path, stage2_csv=stage2_path, session=session)
    html_path = make_dashboard.build(csv_path, open_browser=open_browser,
                                     hist_dir=make_dashboard.HIST_DIR, session=session)

    print(f"\n완료 [{session}] 대상일자={stamp}")
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
    ap.add_argument("--session", default="MANUAL", choices=["AM", "PM", "MANUAL", "HIST"],
                    help="AM=장전, PM=장마감, MANUAL=수동 조회, HIST=소급조회 (기본 MANUAL)")
    ap.add_argument("--date", default=None,
                    help="YYYYMMDD. 과거 특정 날짜를 소급 스캔(백필). 생략 시 오늘.")
    a = ap.parse_args()
    try:
        main(a.market, a.min_rs, a.kr_source, open_browser=not a.no_open,
            session=a.session, date=a.date)
    except Exception:
        print("\n실패:")
        traceback.print_exc()
        raise
