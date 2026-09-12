# -*- coding: utf-8 -*-
"""
매일 실행용: 스캔 → CSV → PDF 리포트 → HTML 대시보드

사용법:
  python3 run_daily.py                          # 한국+미국 전체 (세션: 수동조회)
  python3 run_daily.py --market US               # 미국만
  python3 run_daily.py --session AM               # 장전 스캔으로 표시
  python3 run_daily.py --session PM               # 장마감 스캔으로 표시
  python3 run_daily.py --no-open                  # 브라우저 자동 실행 안 함

데이터 기준일 고정(장전 스캔 권장):
  python3 run_daily.py --session AM --data-date 20260903 --no-open
  파일명·표지 날짜는 오늘로 두고, 데이터만 지정한 날짜까지로 잘라서 본다.

  장전 스캔에 이게 필요한 이유:
    FinanceDataReader 는 장중에 조회하면 '오늘'의 미완성 봉을 종가처럼 돌려준다.
    GitHub Actions 는 예약 실행이 수십 분~수 시간 밀릴 수 있고, 전종목 스캔
    자체도 수십 분이 걸린다. 그래서 07:20 에 예약해도 09:00(장 시작) 이후에
    데이터를 읽게 되는 경우가 생기고, 그러면 "전일 종가 기준"이라는 전제가
    조용히 깨진다. 실제로 2026-09-04 장전 스캔이 그날 09:17 시세를 담았다.
    --data-date 로 기준일을 못박으면 실행 시각과 소요 시간에 관계없이
    항상 같은 결과가 나온다.

과거 날짜 소급 스캔(백필):
  python3 run_daily.py --date 20260824 --no-open
  여러 날짜를 채우고 싶으면 반드시 오래된 날짜부터 순서대로 실행할 것.
  (RS90 최초진입일 기록이 날짜 순서에 의존하는 부분이 있어, 거꾸로 실행하면
   일부 종목의 '진입 후 경과일'이 부정확해질 수 있다)

  --date 와 --data-date 의 차이:
    --date       파일명·표지·데이터를 모두 그 날짜로 (과거 기록을 새로 만들 때)
    --data-date  파일명·표지는 오늘, 데이터만 그 날짜까지 (오늘자 브리핑을
                 전일 종가로 만들 때)

  한계: 시가총액은 항상 '지금 이 순간' 값이 들어간다(과거 시점 값 불가).
       미국 종목 목록도 위키피디아의 현재 S&P500 구성을 쓰므로, 그 과거
       날짜에 실제로 지수에 속했는지는 반영되지 않는다.

하루 두 번(장전/장마감) 자동 실행은 daily.yml 의 cron 스케줄을 사용한다.
로컬 cron 등록 예시(평일 16:30):
  30 16 * * 1-5 cd /파일이있는폴더 && /usr/bin/python3 run_daily.py --session PM --no-open >> run.log 2>&1
"""

import os
import shutil
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
        session="MANUAL", date=None, data_date=None):
    session = (session or "MANUAL").upper()
    if date:
        stamp = date
        # 소급 스캔인데 세션을 따로 안 정했으면 자동으로 HIST로 표시한다.
        if session == "MANUAL":
            session = "HIST"
    else:
        stamp = dt.date.today().strftime("%Y%m%d")

    # 데이터를 어느 날짜까지 볼 것인가.
    # --data-date 가 우선, 없으면 --date, 둘 다 없으면 None(=오늘까지).
    # stamp(파일명·표지 날짜)와 분리되어 있다는 점이 핵심이다.
    as_of = data_date or date

    print(f"\n===== SEPA 일일 스캔 {dt.datetime.now():%Y-%m-%d %H:%M} "
          f"[{session}] 대상일자={stamp} =====")
    print(f"시장: {market} / RS 기준: {min_rs} 이상")
    if as_of:
        print(f"데이터 기준일: {as_of} 까지 (이 날짜 이후 시세는 보지 않음)")
    else:
        print("데이터 기준일: 제한 없음 — 장중에 실행하면 당일 미완성 봉이 "
              "섞일 수 있습니다. 장전 스캔은 --data-date 사용을 권합니다.")

    if session == "AM":
        if data_date:
            print(f"[안내] 장전 스캔입니다. 데이터를 {data_date} 종가로 고정했으므로 "
                  "실행 시각이 밀려도 결과가 달라지지 않습니다.")
        else:
            print("[경고] 장전 스캔인데 --data-date 가 없습니다. 실행이 09:00 이후로 "
                  "밀리면 당일 장중 시세가 섞입니다.")
    if session == "HIST":
        print("[안내] 과거 시점 소급 스캔입니다. 여러 날짜를 채울 계획이면 "
              "반드시 오래된 날짜부터 순서대로 실행하세요.")

    # 1단계: 트렌드템플릿 스캔
    run(market, min_rs, kr_source, as_of=as_of)

    # sepa_scanner 는 CSV 파일명을 '데이터 기준일'로 붙인다.
    # as_of=20260904 를 주면 sepa_scan_20260904.csv 가 만들어진다.
    # 반면 리포트·대시보드는 파일명과 표지 날짜를 stamp(오늘)로 써야
    # 기존 기록과 충돌하지 않는다. 두 이름이 다를 때 오늘 이름으로 복사해
    # 이후 단계가 전부 stamp 를 따르게 맞춘다.
    scan_stamp = as_of or stamp
    src_csv = os.path.join(OUT_DIR, f"sepa_scan_{scan_stamp}.csv")
    csv_path = os.path.join(OUT_DIR, f"sepa_scan_{stamp}.csv")
    if os.path.abspath(src_csv) != os.path.abspath(csv_path):
        shutil.copy2(src_csv, csv_path)
        print(f"CSV 사본 생성: {os.path.basename(src_csv)} "
              f"-> {os.path.basename(csv_path)} (데이터는 {scan_stamp} 종가)")

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
    # data_as_of=scan_stamp: 파일명은 오늘(stamp)로 맞춰도, 대시보드 상단에는
    # 실제 가격 데이터의 기준일을 정확히 보여줘야 한다. 장전 스캔에서 이 둘이
    # 갈라지는 게 "몇 일 종가인지 헷갈린다"는 혼선의 원인이었다.
    html_path = make_dashboard.build(csv_path, open_browser=open_browser,
                                     hist_dir=make_dashboard.HIST_DIR, session=session,
                                     data_as_of=scan_stamp)

    print(f"\n완료 [{session}] 대상일자={stamp}")
    if as_of:
        print(f"  데이터 기준 : {as_of} 종가")
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
                    help="YYYYMMDD. 과거 특정 날짜를 소급 스캔(백필). "
                         "파일명·표지·데이터가 모두 그 날짜가 된다. 생략 시 오늘.")
    ap.add_argument("--data-date", default=None, metavar="YYYYMMDD",
                    help="YYYYMMDD. 파일명·표지는 오늘로 두고 데이터만 이 날짜까지 본다. "
                         "장전 스캔에서 전일 종가를 고정할 때 사용. "
                         "--date 와 함께 주면 이쪽이 우선한다.")
    a = ap.parse_args()
    try:
        main(a.market, a.min_rs, a.kr_source, open_browser=not a.no_open,
            session=a.session, date=a.date, data_date=a.data_date)
    except Exception:
        print("\n실패:")
        traceback.print_exc()
        raise
