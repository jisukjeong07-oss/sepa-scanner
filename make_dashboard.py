# -*- coding: utf-8 -*-
"""
SEPA 스캔 결과 → 단일 HTML 대시보드

CSV를 읽어 데이터를 HTML 안에 직접 심는다. 외부 라이브러리도 서버도 필요 없고,
파일 하나만 있으면 어느 기기에서든 열린다.

사용법:
    python3 make_dashboard.py                 # 오늘자 결과로 생성 후 브라우저 열기
    python3 make_dashboard.py --no-open       # 파일만 생성
    python3 make_dashboard.py --csv output/sepa_scan_20260824.csv
"""

import os
import json
import argparse
import datetime as dt
import webbrowser
from zoneinfo import ZoneInfo

import pandas as pd

import unicodedata

import market_calendar as mc
import strategy as strat

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "output")
HIST_DIR = os.path.join(BASE, "history")   # 저장소에 커밋되어 영구 보관되는 폴더

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _last_open_date(status_fn, ref_date: dt.date, max_back: int = 10) -> dt.date:
    """
    ref_date 기준으로 그 시장이 실제로 열려 있던 가장 최근 날짜를 찾는다.
    장전(AM) 스캔은 데이터가 --data-date 로 고정된 '직전 영업일' 종가인데,
    그 직전 영업일이 한쪽 시장에만 휴장일(예: 미국 공휴일)이었을 수 있다.
    그 경우 실제로 화면에 반영된 종가는 그보다 더 이전 날짜의 것이므로,
    "언제 종가인지"를 정확히 보여주려면 이렇게 거슬러 올라가야 한다.
    """
    d = ref_date
    for _ in range(max_back):
        if status_fn(d.strftime("%Y-%m-%d"))["open"]:
            return d
        d -= dt.timedelta(days=1)
    return ref_date   # 열린 날을 못 찾으면 기준일 그대로 반환(표시만 부정확, 죽지는 않음)


def _market_basis_labels(kr_close: dt.date, us_close: dt.date) -> tuple:
    """
    한국·미국 각 시장의 실제 종가 기준 시각을, 현지 시각과 KST 둘 다 보여주는
    문자열로 만든다. 미국 쪽은 zoneinfo로 실제 타임존 변환을 하므로 서머타임
    전환 시기에도 자동으로 정확한 KST 시각이 나온다(수작업 보정 불필요).
    KRX는 정규장이 항상 15:30 KST에 끝난다(조기폐장일은 드물어 고려하지 않음).
    """
    kr_wd = _WEEKDAY_KR[kr_close.weekday()]
    kr_label = f"한국 {kr_close:%Y-%m-%d}({kr_wd}) 15:30 KST 종가"

    us_wd = _WEEKDAY_KR[us_close.weekday()]
    us_close_ny = dt.datetime.combine(us_close, dt.time(16, 0), tzinfo=ZoneInfo("America/New_York"))
    us_close_kst = us_close_ny.astimezone(ZoneInfo("Asia/Seoul"))
    us_label = (f"미국 {us_close:%Y-%m-%d}({us_wd}) 16:00 현지 종가 "
               f"→ KST {us_close_kst:%Y-%m-%d %H:%M}")

    return kr_label, us_label

# 트렌드템플릿 8조건: (CSV 컬럼명, 화면 표기)
CONDITIONS = [
    ("C1_above_150_200", "현재가 > 150일선·200일선"),
    ("C2_150_over_200", "150일선 > 200일선"),
    ("C3_200_rising", "200일선 1개월 이상 상승"),
    ("C4_50_over_150_200", "50일선 > 150일선·200일선"),
    ("C5_above_50", "현재가 > 50일선"),
    ("C6_above_low_30", "52주 저점 대비 +30% 이상"),
    ("C7_near_high_25", "52주 고점 대비 -25% 이내"),
    ("C8_rs_pass", "RS Rating 기준 충족"),
]


def _rows(df: pd.DataFrame) -> list:
    out = []
    for idx, r in df.iterrows():
        fails = [label for col, label in CONDITIONS
                 if col in df.columns and not bool(r.get(col, False))]
        turnover = r.get("avg_turnover_20d")
        try:
            turnover = float(turnover)
        except (TypeError, ValueError):
            turnover = None

        cap = r.get("market_cap")
        try:
            cap = float(cap)
            if pd.isna(cap):
                cap = None
        except (TypeError, ValueError):
            cap = None

        # [2026-09-15] 회전율 · 이격 지속일수 — sepa_scanner.py의
        # add_turnover_ratio() / deviation_days() 가 계산해 CSV에 붙여준 값을 그대로 읽는다.
        dev_days_raw = r.get("dev_days")
        dev_days_now_raw = r.get("dev_days_now")

        out.append({
            "ticker": str(idx),
            "name": str(r.get("name", idx)),
            "market": str(r.get("market", "")),
            "price": _f(r.get("price")),
            "rs": _f(r.get("RS")),
            "high": _f(r.get("vs_52w_high_%")),
            "low": _f(r.get("vs_52w_low_%")),
            "ma50": _f(r.get("vs_MA50_%")),
            "slope": _f(r.get("MA200_slope_%")),
            "turnover": turnover,
            "cap": cap,
            "sector": (str(r.get("sector")) if pd.notna(r.get("sector")) else None),
            "turnoverRatio": _f(r.get("turnover_ratio")),
            "turnoverRatio5d": _f(r.get("turnover_ratio_5d")),
            "devDays": (int(dev_days_raw) if pd.notna(dev_days_raw) else None),
            "devDaysNow": (None if pd.isna(dev_days_now_raw) else bool(dev_days_now_raw)),
            "devPeak": _f(r.get("dev_peak")),
            "devTrend": (str(r.get("dev_trend")) if pd.notna(r.get("dev_trend")) else None),
            "met": int(r.get("conditions_met", 0) or 0),
            "pass": bool(r.get("PASS", False)),
            "fails": fails,
            # [2026-09-13] VCP(변동성 수축 패턴) — 통과·관찰(7조건 이상)만
            # 값이 있고, 나머지는 None. sepa_scanner.screen()에서 계산.
            "pivot": _f(r.get("pct_from_pivot")),
            "risk": _f(r.get("risk_pct")),
            "pivotPrice": _f(r.get("pivot_price")),
            "stopPrice": _f(r.get("stop_price")),
            "legs": (int(r["contraction_count"]) if pd.notna(r.get("contraction_count")) else None),
            "tight": (None if pd.isna(r.get("is_tightening")) else bool(r.get("is_tightening"))),
            "dryup": (None if pd.isna(r.get("vol_dryup")) else bool(r.get("vol_dryup"))),
            "vcp": (str(r.get("vcp_status")) if pd.notna(r.get("vcp_status")) else None),
            "volRatio": _f(r.get("vol_ratio")),
            "volRatioLabel": (str(r.get("vol_ratio_label")) if pd.notna(r.get("vol_ratio_label")) else None),
        })
    return out


def _f(v):
    try:
        f = float(v)
        return None if pd.isna(f) else round(f, 2)
    except (TypeError, ValueError):
        return None


def _render_changelog_md(path: str) -> str:
    """
    CHANGELOG.md를 아주 단순한 규칙으로만 HTML로 바꾼다:
      '## '로 시작 -> 소제목(h3)
      '- '로 시작 -> 목록 항목(연속되면 하나의 ul로 묶음)
      '---' 단독 줄 -> 구분선
      그 외 내용 있는 줄 -> 문단(p)
    본격적인 마크다운 파서가 아니라, 이 파일을 직접 쓸 사람(우리)이
    정해진 형식만 지킨다는 전제로 짠 가벼운 변환기다.
    """
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return ""

    html = []
    in_ul = False
    started = False  # 첫 '---' 이전은 "파일 쓰는 법" 안내문이라 화면엔 안 띄운다

    def close_ul():
        nonlocal in_ul
        if in_ul:
            html.append("</ul>")
            in_ul = False

    for raw in lines:
        line = raw.strip()
        if not started:
            if line == "---":
                started = True
            continue
        if not line:
            close_ul()
            continue
        if line == "---":
            close_ul()
            html.append('<hr style="border:none;border-top:1px solid var(--line);margin:14px 0">')
        elif line.startswith("## "):
            close_ul()
            html.append(f"<h3>{_esc_html(line[3:])}</h3>")
        elif line.startswith("# "):
            close_ul()
            # 문서 맨 위 제목 줄은 팝업 자체에 제목이 따로 있으니 건너뛴다
            continue
        elif line.startswith("- "):
            if not in_ul:
                html.append("<ul>")
                in_ul = True
            html.append(f"<li>{_esc_html(line[2:])}</li>")
        else:
            close_ul()
            html.append(f"<p>{_esc_html(line)}</p>")
    close_ul()
    return "\n".join(html)


def _esc_html(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_SESSION_LABEL_KO = {"AM": "장전", "PM": "장마감", "MANUAL": "수동", "HIST": "소급"}


def _render_run_log(path: str, max_rows: int = 50) -> str:
    """
    run_log.jsonl(run_daily.py가 실행마다 한 줄씩 남긴 기록)을 최근 것부터
    표로 렌더링한다. 파일이 없거나 비어 있으면 빈 문자열을 반환한다.
    """
    if not os.path.exists(path):
        return ""
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 한 줄이 깨져도 나머지 로그는 살린다
    except Exception:
        return ""

    if not entries:
        return ""

    entries = entries[::-1][:max_rows]  # 최근 순

    rows = []
    for e in entries:
        ts = e.get("timestamp", "")
        try:
            ts_disp = dt.datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts_disp = ts
        status = e.get("status", "")
        session = _SESSION_LABEL_KO.get(e.get("session", ""), e.get("session", ""))
        status_html = (
            '<span class="runlog-ok">성공</span>' if status == "success"
            else f'<span class="runlog-fail">실패</span>'
        )
        dur = e.get("duration_sec")
        dur_disp = f"{dur:.0f}초" if isinstance(dur, (int, float)) else "–"
        data_asof = e.get("data_as_of") or "–"
        err = e.get("error")
        err_html = f'<div class="runlog-err">{_esc_html(err)}</div>' if err else ""
        rows.append(
            f"<tr><td>{_esc_html(ts_disp)}</td><td>{_esc_html(session)}</td>"
            f"<td>{status_html}</td><td>{_esc_html(str(data_asof))}</td>"
            f"<td>{dur_disp}</td></tr>{err_html and f'<tr><td colspan=\"5\">{err_html}</td></tr>'}"
        )

    return (
        '<table class="runlog-table">'
        '<tr><th>실행 시각</th><th>세션</th><th>결과</th><th>데이터 기준일</th><th>소요시간</th></tr>'
        + "".join(rows) + "</table>"
    )


HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEPA 스캔 · __DATE__</title>
<style>
:root{
  --paper:#EDF0F3; --surface:#FFFFFF; --ink:#12161C; --ink-2:#4A525C;
  --muted:#8B939D; --line:#D5DBE1;
  --up:#C0343B;      /* 한국 시장 관행: 강세는 빨강 */
  --down:#1B5FA6;    /* 약세는 파랑 */
  --rail:#DDE3E9;
}*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--paper); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",
              "Noto Sans KR",sans-serif;
  font-size:14px; line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}

/* 헤더 */
header{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:22px}
.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;
         color:var(--muted);margin-bottom:6px}
h1{margin:0;font-size:26px;font-weight:800;letter-spacing:-.02em}
.sub{color:var(--ink-2);font-size:13px;margin-top:4px}
.hrow{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;flex-wrap:wrap}
.hact{display:flex;gap:8px;align-items:center}
.basis-row{margin-top:6px;text-align:right;font-size:11px;color:var(--muted,#8A8F98);
  font-variant-numeric:tabular-nums}
.basis-item{white-space:nowrap}
.basis-sep{margin:0 8px;opacity:.5}
@media (max-width:640px){
  .basis-row{text-align:left;font-size:10px;line-height:1.6}
  .basis-sep{display:block;margin:0;opacity:0}
}
.btn{border:1px solid var(--ink);background:var(--ink);color:#fff;border-radius:7px;
     padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{opacity:.88}
#pdf.btn{background:#000;border-color:#000;color:#fff;font-weight:800}
#excelBtn.btn{background:#000;border-color:#000;color:#fff;font-weight:800}
.btn-outline{border:1px solid var(--line);background:var(--surface);color:var(--ink-2);
     border-radius:7px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;
     font-family:inherit;text-decoration:none;display:inline-flex;align-items:center}
.btn-outline:hover{background:#F2F5F8;color:var(--ink)}
#sessSeg button:disabled{opacity:.35;cursor:not-allowed}

/* 시황 · 리스크 패널 (화면 최상단) */
.macro{margin-bottom:20px}
.macro-h{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-bottom:8px}
.macro-h-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.macro-h-row .macro-h{margin-bottom:0}
.macro-collapse-btn{border:1px solid var(--line);background:var(--surface);color:var(--ink-2);
  border-radius:6px;padding:4px 12px;font-size:11.5px;cursor:pointer;font-family:inherit}
.macro-collapse-btn:hover{background:#F2F5F8;color:var(--ink)}
.sector-panel .seg button{font-size:11.5px;padding:5px 10px}
.treemap-view{overflow-x:auto}
.treemap-view svg{display:block;width:100%;height:auto;max-width:100%}
.treemap-view rect{cursor:pointer;stroke:#fff;stroke-width:1.5}
.treemap-view rect:hover{opacity:.85}
.treemap-view text{pointer-events:none;font-family:inherit}
#sectorTable th{background:#1B2A4A;color:#fff;padding:7px 8px;font-size:11px}
#sectorTable td{padding:7px 8px;font-size:12px;border-bottom:1px solid #EEF1F4}
#sectorTable tr[data-sector]{cursor:pointer}
#sectorTable tr[data-sector]:hover{background:#F7F9FB}
#macroCollapseBody.collapsed{display:none}
#sectorCollapseBody.collapsed{display:none}
.idx-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.idx-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.idx-card .nm{font-size:11.5px;color:var(--ink-2)}
.idx-card .val{font-size:19px;font-weight:800;letter-spacing:-.01em;margin-top:2px}
.idx-card .chg{font-size:12px;font-weight:700;margin-top:1px}
.idx-card .chg.pos{color:var(--up)} .idx-card .chg.neg{color:var(--down)}
.idx-card.fail{color:var(--muted)}
.idx-card.warn{border-color:#e8b4b4;background:#fff8f8}
.idx-card .sub{font-size:10.5px;color:var(--muted);margin-top:3px}
.idx-card.compact{padding:8px 13px}
.idx-card.compact .valrow{display:flex;align-items:baseline;gap:8px;margin-top:2px}
.idx-card.compact .val{font-size:19px;margin-top:0}
.idx-card.compact .chg{font-size:13.5px;margin-top:0}
.macro-breadth{margin-bottom:20px}
.risk-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.risk-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:10px 12px;position:relative}
.risk-card .nm{font-size:10.5px;color:var(--muted);font-weight:600}
.risk-card .val{font-size:17px;font-weight:800;margin-top:2px}
.risk-card .lbl{font-size:11px;color:var(--ink-2);margin-top:1px}
.risk-card .asof{font-size:9.5px;color:var(--muted);margin-top:5px}
.risk-card.fail .val{color:var(--muted);font-size:12px;font-weight:600}
.risk-card.warn{border-color:#e8b4b4;background:#fff8f8}
input#day{border:1px solid var(--line);border-radius:7px;padding:7px 10px;
  font-size:13px;font-family:inherit;background:var(--surface);color:var(--ink);
  cursor:pointer;color-scheme:light}
.day-wrap{position:relative;display:inline-flex;flex-direction:column}
.day-msg{position:absolute;top:calc(100% + 5px);right:0;white-space:nowrap;
  font-size:11.5px;color:#a13c3c;background:#fff6f6;border:1px solid #f0c9c9;
  border-radius:6px;padding:5px 9px;display:none;z-index:5}
.day-msg.show{display:block}

/* 인쇄(=PDF 저장) 시 화면 조작부는 감추고 표만 남긴다 */
@media print{
  @page{size:A4 portrait;margin:10mm}
  body{background:#fff}
  .controls,.hact,footer .noprint{display:none !important}
  .wrap{max-width:none;padding:0}
  table{border:1px solid #999;width:100% !important;min-width:0 !important;table-layout:auto}
  tbody tr{page-break-inside:avoid}
  thead{display:table-header-group}
  .hide-s{display:table-cell !important}
  th,td{font-size:6.6pt !important;padding:2.5px 3px !important;letter-spacing:0}
  /* 세로(A4 portrait) 폭에 14개 컬럼을 다 넣어야 해서, 장식용 막대(rail)는
     자리만 차지하고 정보량은 적다 — 인쇄 시엔 숫자만 남기고 막대는 뺀다. */
  .rail .track,.rail .zone,.rail .dot,.rail .peak{display:none !important}
  .rail{width:auto !important}
  .railnum{min-width:0 !important}
  .ext-warn{-webkit-print-color-adjust:exact;print-color-adjust:exact;
            background:#FBEAEA !important;color:#C0343B !important}
  .ext-caut{color:#A96A0B !important}
  th{background:#1B2A4A !important;color:#fff !important;
     -webkit-print-color-adjust:exact;print-color-adjust:exact}
}

/* 요약 */
.stats{display:flex;gap:28px;flex-wrap:wrap;margin:18px 0 24px}
.stat .k{font-size:11px;color:var(--muted);letter-spacing:.06em}
.stat .v{font-size:26px;font-weight:800;letter-spacing:-.02em}
.stat .v.hl{color:var(--up)}

/* 조작부 */
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;
          background:var(--surface);border:1px solid var(--line);
          border-radius:10px;padding:12px 14px;margin-bottom:16px}
.seg{display:flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}
.seg button{border:0;background:var(--surface);padding:7px 13px;font-size:13px;
            cursor:pointer;color:var(--ink-2);font-family:inherit}
.seg button[aria-pressed="true"]{background:var(--ink);color:#fff;font-weight:600}
.seg button+button{border-left:1px solid var(--line)}
input[type=search]{border:1px solid var(--line);border-radius:7px;padding:7px 11px;
  font-size:13px;font-family:inherit;min-width:150px;background:var(--surface);color:var(--ink)}
.vcp-filter{border:1px solid var(--line);border-radius:7px;padding:7px 11px;
  font-size:13px;font-family:inherit;background:var(--surface);color:var(--ink);cursor:pointer}
.help-btn{width:26px;height:26px;border-radius:50%;border:1px solid var(--line);
  background:var(--surface);color:var(--ink-2);font-size:12px;font-weight:700;
  cursor:pointer;padding:0;line-height:1}
.help-btn:hover{background:#F2F5F8;color:var(--ink)}
.filtered-count{margin-left:auto;font-size:12.5px;font-weight:700;color:var(--ink-2);
  white-space:nowrap}
label.rs{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--ink-2)}
input[type=range]{width:80px;accent-color:var(--up)}
button:focus-visible,input:focus-visible,tr:focus-visible{outline:2px solid var(--up);outline-offset:2px}

/* 표 — 높이를 제한하고 내부에서만 스크롤, 헤더는 위에 고정 */
.tbl-scroll{max-height:52vh;overflow-y:auto;overflow-x:auto;background:var(--surface);
  border:1px solid var(--line);border-radius:10px;-webkit-overflow-scrolling:touch}
/* [2026-09-16] macOS는 스크롤 중이 아니면 스크롤바 자체를 숨긴다 — 그래서
   가로로 더 볼 수 있다는 걸 눈으로 알아채기 어려웠다. 항상 얇은 스크롤바가
   보이게 강제한다(크롬·사파리 계열 웹킷 스크롤바, 파이어폭스는 scrollbar-width). */
.tbl-scroll{scrollbar-width:thin}
.tbl-scroll::-webkit-scrollbar{height:10px;width:10px}
.tbl-scroll::-webkit-scrollbar-track{background:#F0F2F5}
.tbl-scroll::-webkit-scrollbar-thumb{background:#B4B2A9;border-radius:5px}
.tbl-scroll::-webkit-scrollbar-thumb:hover{background:#888780}
.tbl-note{text-align:right;font-size:10.5px;color:#111;margin-bottom:4px}
.cap-toggle{width:auto;height:auto;border-radius:5px;padding:2px 8px;
  font-size:10px;margin-left:6px;vertical-align:middle}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--surface)}
/* VCP 컬럼이 추가돼 컬럼 수가 많아지면, 표를 화면 폭에 욱여넣어 자르지
   말고 가로로 넓혀서 스크롤되게 한다 (min-width가 화면보다 크면 .tbl-scroll이
   가로 스크롤바를 만든다). 라벨을 짧게 줄인 만큼 기준값도 낮췄다. */
@media (min-width:821px){ table{min-width:980px} }
th{font-size:13px;color:#fff;text-align:center;vertical-align:middle;
   padding:9px 6px;line-height:1.3;
   border-bottom:1px solid var(--line);white-space:nowrap;cursor:pointer;
   font-weight:700;letter-spacing:.02em;user-select:none;
   position:sticky;top:0;background:#1B2A4A;z-index:2}
td:first-child{text-align:left;padding-left:8px;max-width:160px}
th[aria-sort]{background:#12203D}
td{padding:8px;text-align:right;border-bottom:1px solid #EEF1F4;white-space:nowrap}
tbody tr{cursor:pointer}
tbody tr:hover{background:#F6F8FA}
tbody tr.sel{background:#EEF3F8}
.tk{font-weight:700;letter-spacing:-.01em}
.nm{font-size:11px;color:var(--muted);display:block;font-weight:400}
.pos{color:var(--up)} .neg{color:var(--down)}
.badge{display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px;
       background:#F0F2F5;color:var(--ink-2);margin-left:4px;font-weight:600;cursor:help}
.badge.p{background:var(--up);color:#fff;cursor:default}
.hot-warn{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;
  border-radius:50%;background:#c0343b;color:#fff;font-size:10px;font-weight:800;
  margin-left:4px;cursor:help}

/* VCP 뱃지 */
.vcp-cell{line-height:1.3}
.vcp-badge{display:inline-block;font-size:10.5px;padding:2px 7px;border-radius:4px;font-weight:700}
.vcp-ready{background:var(--up);color:#fff}
.vcp-dev{background:#F0F2F5;color:var(--ink-2)}
.vcp-ext{background:#fdf0e0;color:#b06a00}
.vcp-sub{font-size:9.5px;color:var(--muted);margin-top:1px}

/* 돌파일 거래량 비율 */
.vr-badge{font-size:11px;font-weight:700;white-space:nowrap}
.vr-surge{color:#1a7f37}
.vr-normal{color:var(--ink-2)}
.vr-low{color:#c0343b}

/* 관심종목 별 */
.star{display:inline-block;width:20px;text-align:center;cursor:pointer;
  font-size:15px;line-height:1;color:#C9CFD6;user-select:none;margin-right:2px}
.star:hover{color:#E8A33D;transform:scale(1.15)}
.star.on{color:#E8952B}

/* 미달 조건 툴팁 (PC는 hover, 모바일은 배지 탭) */
.cellwrap{position:relative;display:inline-block}
.tip{position:absolute;left:0;top:calc(100% + 6px);z-index:20;min-width:190px;
  background:#1D242C;color:#fff;border-radius:8px;padding:9px 11px;
  font-size:11.5px;line-height:1.6;font-weight:400;white-space:normal;
  box-shadow:0 6px 20px rgba(0,0,0,.22);display:none;text-align:left}
.tip b{color:#FFC98A;font-weight:700;display:block;margin-bottom:3px}
.cellwrap:hover .tip{display:block}
.tip.pin{display:block}

/* 종목 차트 */
.chart-wrap{margin-top:14px;background:var(--surface);border:1px solid var(--line);
  border-radius:10px;overflow:hidden}
.chart-head{display:flex;justify-content:space-between;align-items:center;
  padding:10px 14px;border-bottom:1px solid var(--line);font-size:13px;font-weight:700}
.chart-hint{font-size:11px;color:var(--muted);font-weight:400;margin-left:8px}
.chart-box{height:460px}
.chart-box iframe{border:0;width:100%;height:100%}
.vcp-chart-box{height:280px;padding:8px}
.vcp-chart-box svg{width:100%;height:100%;display:block}
.vcp-wrap .chart-head{background:#FBFCFD}
.btn-sm{border:1px solid var(--line);background:var(--surface);color:var(--ink-2);
  border-radius:6px;padding:5px 11px;font-size:12px;cursor:pointer;font-family:inherit}
.btn-sm:hover{background:#F2F5F8;color:var(--ink)}
.btn-sm.danger{color:#A13C3C;border-color:#E8C9C9}

/* 관심종목 동기화 영역 */
.fav-sync{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:13px 15px;margin-bottom:12px}
.fav-sync-h{font-size:12.5px;font-weight:700;margin-bottom:4px}
.fav-sync-p{font-size:11.5px;color:var(--muted);margin:0 0 8px;line-height:1.5}
#favCode{width:100%;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;
  border:1px solid var(--line);border-radius:7px;padding:7px 9px;resize:vertical;
  background:#FAFBFC;color:var(--ink)}
.fav-sync-btns{display:flex;gap:7px;align-items:center;margin-top:8px;flex-wrap:wrap}
.fav-msg{font-size:11.5px;color:#2F7A4E;font-weight:600}
@media print{.chart-wrap,.fav-sync,.star{display:none !important}
  .tbl-scroll{max-height:none;overflow:visible}}

/* 50일선 이격 경고 (25%+ 과열 / 15%+ 주의) */
.ext-warn{color:#C0343B;font-weight:800;background:#FBEAEA;
          padding:1px 5px;border-radius:3px;white-space:nowrap}
.ext-caut{color:#A96A0B;font-weight:700}

/* 52주 고점 대비: 숫자 + 근접도 트랙 */
.railwrap{display:flex;align-items:center;justify-content:flex-end;gap:5px}
.railnum{font-variant-numeric:tabular-nums;font-size:12px;font-weight:600;
         white-space:nowrap;min-width:40px;text-align:right}
.railnum.near{color:var(--up)}          /* 고점 3% 이내 = 돌파 임박 */
.railnum.out{color:var(--muted)}        /* 조건7 미달 */

/* 시그니처: 52주 고점 근접도 트랙 (조건7 = 고점 -25% 이내) */
.rail{position:relative;width:44px;height:16px;margin-left:auto}
.rail .track{position:absolute;top:7px;left:0;right:0;height:2px;background:var(--rail)}
.rail .zone{position:absolute;top:5px;right:0;width:100%;height:6px;
            background:linear-gradient(90deg,rgba(192,52,59,0) 0%,rgba(192,52,59,.16) 100%)}
.rail .dot{position:absolute;top:3px;width:10px;height:10px;border-radius:50%;
           background:var(--up);border:2px solid var(--surface);transform:translateX(-50%)}
.rail .dot.out{background:var(--muted)}
.rail .peak{position:absolute;top:1px;right:0;width:2px;height:14px;background:var(--ink)}

/* 조건 상세 */
tr.detail td{background:#F8FAFB;padding:14px 14px 16px;text-align:left;
             border-bottom:1px solid var(--line)}
.cond{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12px;padding:4px 9px;border-radius:6px;background:#E8F0E9;color:#245C35}
.chip.no{background:#FBE9EA;color:#8E2229;font-weight:600}
.detail h4{margin:0 0 9px;font-size:12px;color:var(--muted);letter-spacing:.06em;font-weight:600}
.empty{padding:40px 14px;text-align:center;color:var(--muted);background:var(--surface);
       border:1px solid var(--line);border-radius:10px}
footer{margin-top:26px;font-size:11.5px;color:var(--muted);line-height:1.7;
       border-top:1px solid var(--line);padding-top:14px}
.footer-link-row{margin-top:10px;text-align:right;display:flex;justify-content:flex-end;gap:8px}
.filter-btn{background:#E8630C;border:0;color:#fff;font-size:12.5px;font-weight:700;
  padding:9px 18px;border-radius:7px;cursor:pointer;letter-spacing:.01em}
.filter-btn:hover{background:#CF5709}
.log-btn{background:#1B2A4A;border:0;color:#fff;font-size:12.5px;font-weight:700;
  padding:9px 18px;border-radius:7px;cursor:pointer;letter-spacing:.01em}
.log-btn:hover{background:#12203D}
.runlog-btn{background:#3F4552;border:0;color:#fff;font-size:12.5px;font-weight:700;
  padding:9px 18px;border-radius:7px;cursor:pointer;letter-spacing:.01em}
.runlog-btn:hover{background:#2E3340}
.runlog-table{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:8px}
.runlog-table th{text-align:left;padding:6px 8px;color:var(--muted);
  border-bottom:1px solid var(--line);font-weight:700;background:none}
.runlog-table td{padding:6px 8px;border-bottom:1px solid #EEF1F4}
.runlog-ok{color:#1a7f37;font-weight:700}
.runlog-fail{color:#c0343b;font-weight:700}
.runlog-err{font-size:10.5px;color:#c0343b;background:#FBEAEA;border-radius:5px;
  padding:5px 8px;margin-top:2px}

/* 필터링 기준 팝업 */
.modal-overlay{position:fixed;inset:0;background:rgba(15,17,21,.5);
  display:flex;align-items:center;justify-content:center;z-index:200;padding:20px}
.modal-overlay[hidden]{display:none !important}
.modal{background:#fff;border-radius:12px;max-width:680px;width:100%;
  max-height:86vh;overflow:hidden;display:flex;flex-direction:column;
  box-shadow:0 20px 60px rgba(0,0,0,.25)}
.modal-head{display:flex;justify-content:space-between;align-items:center;
  padding:16px 20px;border-bottom:1px solid var(--line)}
.modal-head h2{font-size:16px;margin:0}
.modal-head-btns{display:flex;gap:8px;align-items:center}
.modal-body{padding:16px 20px 22px;overflow-y:auto;font-size:12.5px;
  line-height:1.7;color:var(--ink-2)}
.modal-body h3{font-size:13.5px;margin:20px 0 6px;color:var(--ink);
  padding-top:14px;border-top:1px solid var(--line)}
.modal-body h3:first-child{margin-top:0;padding-top:0;border-top:0}
.modal-body h4{font-size:12.5px;margin:10px 0 4px;color:var(--ink)}
.modal-body p{margin:0 0 6px}
.modal-body ul,.modal-body ol{margin:4px 0 6px;padding-left:20px}
.modal-body li{margin-bottom:3px}
.modal-body .cond-list{padding-left:22px}
.modal-body .note{background:var(--surface);border:1px solid var(--line);
  border-radius:8px;padding:8px 10px;font-size:11.5px;color:var(--muted);margin-top:8px}
.modal-body .term-box{background:#F7F9FB;border:1px solid var(--line);border-radius:8px;
  padding:10px 12px;margin:6px 0 10px}
.modal-body .term-box b{color:var(--ink)}
.modal-body .formula{background:#EFF3F8;border-radius:6px;padding:6px 10px;
  font-family:ui-monospace,Menlo,monospace;font-size:11.5px;margin:5px 0;
  color:var(--ink);display:inline-block}
.modal-body .example{background:#FFF8ED;border:1px solid #F0DDB8;border-radius:8px;
  padding:12px 14px;margin:8px 0}
.modal-body .example h4{margin-top:0;color:#8a5a00}
.modal-body .example ol{padding-left:18px}
.modal-body .example li{margin-bottom:6px}
@media print{
  body.printing-filter-info > *:not(#filterInfoOverlay){display:none !important}
  body.printing-filter-info #filterInfoOverlay{position:static !important;
    background:none !important;padding:0 !important;display:block !important}
  body.printing-filter-info .modal{max-width:none !important;max-height:none !important;
    box-shadow:none !important;border-radius:0}
  body.printing-filter-info .modal-body{overflow:visible !important}
  body.printing-filter-info #filterInfoClose,
  body.printing-filter-info #filterInfoPdf{display:none !important}
  /* 팝업 배경색·뱃지 색이 인쇄 시 사라지지 않게 강제로 살린다
     (기본값은 인쇄할 때 배경색을 전부 지운다) */
  body.printing-filter-info .modal-head,
  body.printing-filter-info .modal-body,
  body.printing-filter-info .modal-body *{
    -webkit-print-color-adjust:exact !important;print-color-adjust:exact !important}
  /* 화면 그대로의 디자인을 유지하면서, 본문 글자는 최소 9pt를 보장한다 */
  body.printing-filter-info .modal-body{font-size:9.5pt !important;line-height:1.6 !important}
  body.printing-filter-info .modal-body h3{font-size:11.5pt !important}
  body.printing-filter-info .modal-body h4{font-size:10pt !important}
  body.printing-filter-info .modal-body .note,
  body.printing-filter-info .modal-body .formula,
  body.printing-filter-info .modal-body .term-box{font-size:9pt !important}
  body.printing-filter-info .modal-head h2{font-size:13pt !important}
}

.mkt-status{display:flex;gap:18px;flex-wrap:wrap;align-items:center;
  background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:10px 14px;margin-bottom:14px;font-size:12.5px;color:var(--ink-2)}
.mkt-status .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.mkt-status .dot.open{background:#2f8f4e}
.mkt-status .dot.closed{background:var(--muted)}
.mkt-status b{color:var(--ink);font-weight:700}
.generated-at{margin-left:auto;font-size:12px;color:#B85C00;font-weight:700}
.generated-at b{color:#E8630C;font-weight:800}

/* 매매전략 진입 버튼 */
.strat-open{border:1px solid var(--navy,#1a2b4c);background:transparent;color:#1a2b4c;
  border-radius:6px;padding:4px 11px;font-size:12px;font-weight:700;cursor:pointer;
  font-family:inherit;margin-left:10px;vertical-align:middle}
.strat-open:hover{background:#1a2b4c;color:#fff}

/* 매매전략 모달 */
.strat-overlay{display:none;position:fixed;inset:0;background:rgba(15,20,26,.55);
  z-index:50;align-items:flex-start;justify-content:center;padding:26px 14px;overflow-y:auto}
.strat-overlay.show{display:flex}
.strat-modal{background:#fff;border-radius:12px;max-width:1080px;width:100%;
  box-shadow:0 20px 60px rgba(0,0,0,.3);position:relative}
.strat-pdf{position:absolute;top:13px;right:46px;border:1px solid #1a2b4c;
  background:#1a2b4c;color:#fff;border-radius:6px;padding:5px 12px;
  font-size:12px;font-weight:700;cursor:pointer;font-family:inherit}
.strat-pdf:hover{opacity:.88}
.strat-close{position:absolute;top:14px;right:16px;border:0;background:none;
  font-size:20px;line-height:1;cursor:pointer;color:#888}
.strat-pad{padding:30px 32px 26px}
.strat-h1{font-size:18pt;font-weight:800;color:#1a2b4c;margin:0 0 4px}
.strat-meta{font-size:9.5pt;color:#777;margin-bottom:18px}
.strat-h2{font-size:14pt;font-weight:700;color:#1a2b4c;margin:22px 0 8px;
  border-left:4px solid #d9730d;padding-left:9px}
.strat-body{font-size:10pt;line-height:1.75;color:#111111}
.strat-body ul{margin:4px 0 0;padding-left:18px}
.strat-body li{margin-bottom:5px}
.strat-pt{color:#d9730d;font-weight:700}
.strat-table{width:100%;border-collapse:collapse;margin-top:6px;font-size:10pt}
.strat-table caption{caption-side:top;text-align:left;color:#1a2b4c;
  font-weight:700;font-size:10.5pt;margin-bottom:6px}
.strat-table th{background:#1a2b4c;color:#fff;font-size:9pt;padding:7px 9px;text-align:left}
.strat-table td{padding:6px 9px;border-bottom:1px solid #eee;color:#111111}
.strat-table tr:nth-child(even) td{background:#F7F9FB}
.strat-table th.sr,.strat-table td.sr{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.strat-table th,.strat-table td{white-space:nowrap}
.strat-table td:first-child{white-space:normal;min-width:96px}
.strat-table th{font-weight:600}
.strat-table td .ext-warn,.strat-table td .ext-caut{font-size:9.5pt}
/* 열이 많아 좁은 화면에서 넘칠 수 있어 가로 스크롤을 허용한다 */
.strat-tblwrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
@media print{ .strat-tblwrap{overflow:visible} .strat-table{font-size:8pt} }
.strat-disclaimer{margin-top:20px;padding-top:12px;border-top:1px solid #e5e5e5;
  font-size:8.5pt;color:#888;line-height:1.6}
@media print{
  .strat-overlay{position:static;background:none;padding:0}
  .strat-modal{box-shadow:none;max-width:none}
  .strat-close,.strat-pdf{display:none}
  /* [2026-09-14] @page는 어느 내용을 인쇄하든 문서 전체에서 딱 하나로만
     적용된다 — "이 블록이 보일 때만" 식으로 조건부 적용되지 않는다.
     여기 따로 landscape를 또 선언하면, 문서에 마지막으로 나온 @page가
     이겨서 Watchlist 표 인쇄(위쪽 세로 설정)까지 덩달아 가로로 바뀌는
     버그가 실제로 있었다. 페이지 크기는 위에서 한 번만(세로) 정하고,
     여기서는 다시 선언하지 않는다.
  */
  /* 전략 팝업을 인쇄할 때는 뒤의 대시보드 본문을 숨겨 팝업만 나오게 한다 */
  body.printing-strat > .wrap > *:not(.strat-overlay){display:none !important}
  body.printing-strat .strat-overlay{display:block !important}
}
@media (max-width:640px){
  .wrap{padding:18px 12px 60px}
  h1{font-size:21px}
  .hide-s{display:none}
  .rail{width:52px}
  .railwrap{gap:6px}
  .railnum{font-size:11.5px;min-width:46px}
  .stats{gap:18px}
}
@media (prefers-reduced-motion:no-preference){
  tbody tr{transition:background .12s ease}
}
</style>
</head>
<body>
<div class="wrap">

<section class="macro">
  <div class="macro-idx">
    <div class="macro-h-row">
      <div class="macro-h">매크로 지표</div>
      <button id="macroCollapseBtn" class="macro-collapse-btn" aria-expanded="false">펼치기</button>
    </div>
    <div id="macroCollapseBody" class="collapsed">
      <div id="idxCards" class="idx-grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr))"></div>
      <div id="idxCards2" class="idx-grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));margin-top:8px"></div>

      <section class="macro-breadth" id="breadthSection" hidden>
        <div class="macro-h" style="margin-top:16px">시장 폭</div>
        <div id="breadthCards" class="idx-grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))"></div>
      </section>
    </div>
  </div>
</section>

<section class="macro sector-panel" id="sectorSection" hidden>
  <div class="macro-idx">
    <div class="macro-h-row">
      <div class="macro-h">업종 강도</div>
      <div style="display:flex;align-items:center;gap:8px">
        <div class="seg" role="group" aria-label="업종 강도 보기">
          <button data-sectorview="treemap" aria-pressed="true">트리맵</button>
          <button data-sectorview="table" aria-pressed="false">표</button>
        </div>
        <button id="sectorCollapseBtn" class="macro-collapse-btn" aria-expanded="true">닫기</button>
      </div>
    </div>
    <div id="sectorCollapseBody">
      <p style="font-size:11px;color:var(--muted);margin:0 0 8px">
        업종을 클릭하면 아래 Momentum Watchlist 표가 그 업종 종목만 필터링됩니다. (한국 종목만 해당)
      </p>
      <div id="sectorTreemapView">
        <div id="treemapWrap" class="treemap-view" style="width:100%"></div>
      </div>
      <div id="sectorTableView" style="display:none">
        <div class="tbl-scroll" style="max-height:none">
          <table id="sectorTable">
            <thead><tr>
              <th style="text-align:left">업종</th>
              <th>종목 수</th>
              <th>평균 RS</th>
              <th>8조건 통과율</th>
              <th>진입가능(VCP)</th>
              <th>평균 회전율</th>
            </tr></thead>
            <tbody id="sectorTbody"></tbody>
          </table>
        </div>
      </div>
      <div id="sectorActiveNote" class="tbl-note" style="text-align:left;margin-top:8px;display:none"></div>
    </div>
  </div>
</section>

<header>
  <div class="hrow">
    <div>
      <h1>Momentum Watchlist</h1>
      <div class="sub">__DATE__ 기준 · 스캔 대상 __TOTAL__종목 (한국 __KRN__ / 미국 __USN__)
        <button id="stratOpen" class="strat-open">매매전략</button>
      </div>
    </div>
    <div class="hact">
      <div class="day-wrap">
        <input type="date" id="day" aria-label="날짜 선택">
        <div class="day-msg" id="dayMsg"></div>
      </div>
      <div class="seg" id="sessSeg" role="group" aria-label="세션">
        <button data-sess="AM" aria-pressed="false">장전</button>
        <button data-sess="PM" aria-pressed="false">장마감</button>
      </div>
      <a id="manualRun" class="btn btn-outline" href="__ACTIONS_URL__" target="_blank" rel="noopener">수동 조회</a>
      <button id="pdf" class="btn">PDF</button>
      <button id="excelBtn" class="btn">Excel</button>
    </div>
  </div>
  <div class="basis-row">
    <span class="basis-item">__KR_BASIS__</span>
    <span class="basis-sep">·</span>
    <span class="basis-item">__US_BASIS__</span>
  </div>
</header>

<div class="mkt-status">
  <span><span class="dot __KR_DOT__"></span>한국 <b>__KR_LABEL__</b></span>
  <span><span class="dot __US_DOT__"></span>미국 <b>__US_LABEL__</b></span>
  <span class="generated-at">이 화면 조회 시각: <b>__GENERATED_AT__</b></span>
</div>

<div class="stats">
  <div class="stat"><div class="k">8조건 통과</div><div class="v hl num" id="statPass">0</div></div>
  <div class="stat"><div class="k">7조건 관찰</div><div class="v num" id="statNear">0</div></div>
  <div class="stat"><div class="k">관심</div><div class="v num" id="statFav">0</div></div>
  <div class="stat"><div class="k">화면 표시</div><div class="v num" id="shown">0</div></div>
</div>

<div class="strat-overlay" id="stratOverlay">
  <div class="strat-modal">
    <button class="strat-close" id="stratClose" aria-label="닫기">&times;</button>
    <button class="strat-pdf" id="stratPdf">PDF로 저장</button>
    <div class="strat-pad" id="stratBody"></div>
  </div>
</div>

<div class="controls">
  <div class="seg" role="group" aria-label="구분">
    <button data-view="pass" aria-pressed="true">통과</button>
    <button data-view="near" aria-pressed="false">관찰</button>
    <button data-view="all" aria-pressed="false">전체</button>
    <button data-view="fav" aria-pressed="false">관심</button>
  </div>
  <div class="seg" role="group" aria-label="시장">
    <button data-mkt="US" aria-pressed="true">미국</button>
    <button data-mkt="KR" aria-pressed="false">한국</button>
  </div>
  <label class="rs">RS <input type="range" id="rs" min="0" max="99" value="0">
    <span class="num" id="rsv">0</span> 이상</label>
  <select id="vcpFilter" aria-label="VCP 상태 필터" class="vcp-filter">
    <option value="">VCP 전체</option>
    <option value="entry_ready">진입가능</option>
    <option value="developing">형성중</option>
    <option value="extended">확장(과열)</option>
    <option value="no_pattern">패턴없음</option>
  </select>
  <span style="display:flex;align-items:center;gap:4px">
    <select id="devDaysFilter" aria-label="이격 지속일수 필터" class="vcp-filter">
      <option value="">이격 지속일수: 전체</option>
      <option value="1-4">1~4일 (초기)</option>
      <option value="5-15">5~15일 (재베이스 관찰)</option>
      <option value="16-9999">16일 이상 (장기 확장)</option>
    </select>
    <button id="devDaysHelpBtn" class="help-btn" aria-label="이격 지속일수 설명" title="이격 지속일수란?">?</button>
  </span>
  <label class="rs" style="gap:5px">
    <input type="checkbox" id="hideOverheated"> 수급과열 숨기기
  </label>
  <input type="search" id="q" placeholder="종목 검색" aria-label="종목 검색">
  <span id="filteredCount" class="filtered-count" aria-live="polite"></span>
</div>

<div id="favSync" class="fav-sync" hidden>
  <div class="fav-sync-h">관심종목 동기화</div>
  <p class="fav-sync-p">아래 코드를 복사해 다른 기기에서 붙여넣으면 관심종목이 옮겨집니다.
    (기기마다 따로 저장되므로 자동으로는 공유되지 않습니다)</p>
  <textarea id="favCode" rows="2" spellcheck="false"></textarea>
  <div class="fav-sync-btns">
    <button id="favCopy" class="btn-sm">복사</button>
    <button id="favApply" class="btn-sm">붙여넣은 코드 적용</button>
    <button id="favClear" class="btn-sm danger">전체 해제</button>
    <span id="favMsg" class="fav-msg"></span>
  </div>
</div>

<div id="host"></div>

<section id="chartWrap" class="chart-wrap" hidden>
  <div class="chart-head">
    <div><span id="chartTitle"></span>
      <span class="chart-hint">차트 안에서 기간·주봉 변경 가능 (미국 종목 · TradingView)</span></div>
    <button id="chartClose" class="btn-sm">닫기</button>
  </div>
  <div id="chartBox" class="chart-box"></div>
</section>

<section id="vcpChartWrap" class="chart-wrap vcp-wrap" hidden>
  <div class="chart-head">
    <div><span id="vcpChartTitle"></span>
      <span class="chart-hint">최근 약 6개월 · 초록 점선=피벗 · 빨강 점선=손절가 · 음영=조정 레그</span></div>
    <button id="vcpChartClose" class="btn-sm">닫기</button>
  </div>
  <div id="vcpChartBox" class="vcp-chart-box"></div>
</section>

<footer>
행을 누르면 8개 조건 중 무엇이 미달인지 볼 수 있습니다.
RS는 IBD 공식 지표가 아니라 3·6·9·12개월 가중수익률을 유니버스 안에서 백분위로 환산한 근사값입니다.
이 화면은 SEPA 1단계(기술적 필터)만 담고 있어, 2단계 펀더멘털과 3단계 진입 시점은 직접 확인해야 합니다.
투자 참고 자료이며 투자 권유가 아닙니다.
<div class="footer-link-row">
  __RUNLOG_BTN__
  __CHANGELOG_BTN__
  <button id="filterInfoBtn" class="filter-btn">필터링 기준 보기</button>
</div>
</footer>
</div>

<div id="devDaysInfoOverlay" class="modal-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="devDaysInfoTitle">
    <div class="modal-head">
      <h2 id="devDaysInfoTitle">이격 지속일수란?</h2>
      <div class="modal-head-btns">
        <button id="devDaysInfoClose" class="btn-sm">닫기</button>
      </div>
    </div>
    <div class="modal-body">
      <h3>이격(%)이란</h3>
      <p>종가가 50일 이동평균선(최근 50거래일 평균 가격)보다 몇 % 위에 있는지를 나타냅니다. "이격 +20%"면 최근 두 달여 평균가보다 20% 비싸게 거래되고 있다는 뜻입니다.</p>

      <h3>이격 지속일수 계산 방법</h3>
      <p>최근 <b>60거래일</b>(약 3개월) 중, 이격이 <b>±15%</b>를 넘은 날이 <b>총 몇 번</b>이었는지 셉니다.</p>
      <div class="term-box">
        <b>연속 스트릭이 아닙니다.</b> "오늘부터 며칠 연속 초과 중"이 아니라, 최근 3개월 안에서 "초과한 날을 다 더한 총 횟수"입니다. 중간에 며칠 정상 범위로 돌아왔다가 다시 벌어져도 그 날짜들이 전부 누적됩니다.
      </div>

      <h3>드롭다운 구간이 의미하는 것</h3>
      <ul>
        <li><b>전체</b> — 이격 지속일수와 무관하게 모든 종목</li>
        <li><b>1~4일 (초기)</b> — 최근 이격이 벌어지기 시작한 지 얼마 안 된 종목</li>
        <li><b>5~15일 (재베이스 관찰)</b> — 어느 정도 기간 동안 벌어져 있었던 종목. 곧 눌림목(재베이스)이 나올 수 있는지 지켜볼 구간</li>
        <li><b>16일 이상 (장기 확장)</b> — 오랫동안 평균가에서 멀리 떨어져 거래된 종목</li>
      </ul>

      <p class="note">위 세 구간은 전부 "1번 이상 초과한 적 있는 종목"만 대상입니다. 최근 60거래일 동안 <b>단 한 번도</b> ±15%를 넘은 적이 없는 종목(이격 지속일수 = 0일)은 세 구간 어디에도 해당하지 않고, "전체"를 선택했을 때만 보입니다. 오류가 아니라 그 종목이 그만큼 변동성 없이 안정적으로 거래돼왔다는 뜻입니다.</p>
    </div>
  </div>
</div>

<div id="runlogOverlay" class="modal-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="runlogTitle">
    <div class="modal-head">
      <h2 id="runlogTitle">실행 기록</h2>
      <div class="modal-head-btns">
        <button id="runlogClose" class="btn-sm">닫기</button>
      </div>
    </div>
    <div class="modal-body">
      <p style="margin-top:0;color:var(--muted);font-size:11px">
        자동(AM·PM 예약)·수동 실행이 언제 돌았는지, 성공했는지, 어느 날짜 종가 데이터를
        받았는지를 실행할 때마다 자동으로 기록합니다. 최근 50건까지 보여줍니다.
      </p>
      <div id="runlogBody">__RUNLOG_HTML__</div>
    </div>
  </div>
</div>

<div id="changelogOverlay" class="modal-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="changelogTitle">
    <div class="modal-head">
      <h2 id="changelogTitle">작업 로그</h2>
      <div class="modal-head-btns">
        <button id="changelogClose" class="btn-sm">닫기</button>
      </div>
    </div>
    <div class="modal-body" id="changelogBody">__CHANGELOG_HTML__</div>
  </div>
</div>

<div id="filterInfoOverlay" class="modal-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="filterInfoTitle">
    <div class="modal-head">
      <h2 id="filterInfoTitle">필터링 기준 · 용어 설명</h2>
      <div class="modal-head-btns">
        <button id="filterInfoPdf" class="btn-sm">PDF 저장</button>
        <button id="filterInfoClose" class="btn-sm">닫기</button>
      </div>
    </div>
    <div class="modal-body">

      <p>이 표는 "지금 사도 좋을 만큼 튼튼하고, 가격도 적당한 자리에 있는 종목"을 자동으로 걸러낸 목록입니다. 아래에서 표에 나오는 용어를 하나씩, 숫자 계산 방법까지 풀어서 설명합니다.</p>

      <h3>1. RS (상대강도) — "다른 종목들과 비교해 얼마나 잘 올랐나"</h3>
      <p>반 학생 100명을 최근 3개월·6개월·9개월·12개월 성적 순으로 줄 세운다고 생각해 보세요. RS 90은 "100명 중 상위 10등 안"이라는 뜻입니다. 스캔 대상 전체 종목(유니버스) 안에서 최근 수익률이 상위 몇 %인지를 0~99 숫자로 나타낸 겁니다.</p>
      <div class="term-box">
        <b>계산 방법</b>: 3개월·6개월·9개월·12개월 수익률에 각각 가중치를 줘서 하나의 점수로 합친 뒤, 그 점수를 전체 종목 중에서 몇 %에 해당하는지로 환산합니다. IBD(Investor's Business Daily)의 공식 RS와는 계산식이 다른 근사값입니다.
      </div>

      <h3>2. 통과 / 관찰 — "8가지 건강검진 항목"</h3>
      <p>마크 미네르비니라는 투자자가 만든 "트렌드템플릿" 8개 조건으로 판정합니다. 사람으로 치면 8개 건강검진 항목을 전부 통과해야 "건강하다(통과)"고 보고, 7개만 통과하면 "거의 건강한데 하나 아쉽다(관찰)"고 보는 것과 같습니다.</p>
      <ol class="cond-list">
        <li>종가가 150일선·200일선 위 — 최근 5개월·9개월 평균 가격보다 지금이 더 비싸다</li>
        <li>150일선이 200일선 위 — 중기 평균이 장기 평균보다 높다(교차 상승)</li>
        <li>200일선이 최근 21영업일(약 1개월) 이상 계속 오르는 중</li>
        <li>50일선이 150일선·200일선 위 (정배열) — 최근 한 달, 다섯 달, 아홉 달 평균이 순서대로 쌓여 있다</li>
        <li>종가가 50일선 위 — 최근 한 달 평균보다도 지금이 비싸다</li>
        <li>현재가가 52주 최저가보다 30% 이상 높다 — 바닥을 찍고 충분히 올라왔다</li>
        <li>현재가가 52주 최고가에서 25% 이내 — 너무 많이 빠지지 않았다</li>
        <li>RS 70 이상 — 위 1번에서 설명한 상대강도가 상위 30% 안</li>
      </ol>

      <h3>3. 전체 / 관심</h3>
      <p><b>전체</b>는 8조건과 상관없이, 최소한의 가격·거래량 기준만 넘긴 모든 종목입니다. <b>관심</b>은 종목 왼쪽 별표(★)를 눌러 내가 직접 찜한 종목이고, 이 브라우저(이 컴퓨터)에만 저장됩니다.</p>

      <h3>4. VCP(변동성 수축 패턴) — "스프링을 누를수록 더 세게 튀어 오른다"</h3>
      <p>공을 손으로 누르면 눌린 만큼 튀어 오르죠. 주가도 비슷합니다. 한 번 오른 종목이 쉬었다(조정) 다시 오르기를 반복하는데, <b>쉬는 폭이 점점 좁아질수록</b> "이제 곧 크게 튈 준비가 됐다"고 봅니다. 이 좁아지는 패턴을 VCP라고 부릅니다.</p>

      <h4>수축(레그 수)</h4>
      <p>주가가 "올랐다 → 쉬었다"를 반복한 횟수입니다. 아무 움직임이나 세는 게 아니라, 그 종목의 평소 변동폭(ATR)보다 확실히 큰 움직임만 "진짜 쉬는 구간(레그)"으로 인정합니다. 표에 "3개"라고 나오면 이런 눌림목을 3번 거쳤다는 뜻입니다.</p>

      <h4>피벗대비(%)</h4>
      <p><b>피벗</b>은 "가장 최근, 가장 좁게 쉰 구간의 꼭대기 가격"입니다. 이 가격을 힘차게 돌파하면 진짜 상승이 시작된다고 보는 기준선입니다. "피벗대비 -3.1%"는 지금 가격이 그 기준선보다 3.1% 아래에 있다는 뜻입니다. 0%에 가까울수록(혹은 살짝 위) 돌파가 임박했다고 봅니다.</p>
      <div class="term-box">
        <b>계산 방법</b>: (현재가 ÷ 피벗가격 − 1) × 100
      </div>

      <h4>손절리스크%</h4>
      <p>"만약 지금 사서 틀렸다면, 최대 몇 %까지 손해를 각오해야 하나"를 보여줍니다. 피벗을 살짝(1%) 넘긴 자리에서 샀다고 가정하고, 가장 최근 눌림목의 바닥(손절가)까지 떨어지면 몇 % 손해인지 계산합니다. 보통 7~8% 이내를 적당한 리스크로 봅니다.</p>
      <div class="term-box">
        <b>계산 방법</b>: 진입가 = 피벗가격 × 1.01 (피벗보다 1% 위에서 산다고 가정)<br>
        손절리스크% = (진입가 − 손절가) ÷ 진입가 × 100
      </div>

      <h4>VCP 뱃지</h4>
      <ul>
        <li><span style="color:#1a7f37;font-weight:700">진입가능</span> — 눌림목 폭이 확인상 좁아지고 있고(수축↓), 피벗 근처이며, 손절리스크도 적당한 경우</li>
        <li><span style="color:#6b7280;font-weight:700">형성중</span> — 아직 패턴이 다 갖춰지지 않았거나, 좁아지는지 판단할 자료(레그)가 부족한 경우</li>
        <li><span style="color:#b06a00;font-weight:700">확장(과열)</span> — 이미 피벗을 훌쩍 넘어 많이 올라간 경우 — 지금 사면 추격매수가 됨</li>
        <li>"거래량↓" 표시 — 가장 최근 눌림목 구간의 거래대금이 그 이전보다 줄었다는 뜻. 팔 사람이 줄었다는 신호로 봅니다.</li>
      </ul>

      <h4>거래량비율 — "오늘, 평소보다 얼마나 많이 거래됐나"</h4>
      <p>조용히 눌림목을 만들던 종목이 거래량 없이 슬쩍 오르면, 그건 진짜 돌파가 아니라 우연히 며칠 오른 것일 수 있습니다. 진짜 돌파는 보통 <b>거래량이 평소보다 확 늘면서</b> 일어납니다. 이 값은 오늘 거래대금이 평소(직전 20거래일 평균)보다 몇 배인지를 보여줍니다.</p>
      <div class="term-box">
        <b>계산 방법</b>: 거래량비율 = 오늘 거래대금 ÷ 직전 20거래일 평균 거래대금 (오늘 자신은 평균 계산에서 뺍니다)
      </div>
      <ul>
        <li><span class="vr-badge vr-surge">1.8배 · 터짐</span> — 1.5배 이상. 거래량을 동반한 신뢰도 높은 움직임</li>
        <li><span class="vr-badge vr-normal">1.2배 · 보통</span> — 1.0~1.5배. 평소와 크게 다르지 않음</li>
        <li><span class="vr-badge vr-low">0.7배 · 부족</span> — 1.0배 미만. 거래량 없이 오른 것이라 가짜 돌파일 가능성에 주의</li>
      </ul>
      <p class="note">피벗대비%가 진입 범위 안에 있어도, 거래량비율이 "부족"이면 아직 확신하기 이릅니다. "진입가능" 뱃지와 거래량비율을 같이 보고 판단하는 게 안전합니다.</p>

      <h4>회전율 — "실적인가, 단타 수급인가"</h4>
      <p>같은 폭으로 올라도 성격이 다를 수 있습니다. 거래대금이 시가총액 대비 비정상적으로 크면, 하루 만에 시총만큼 손바뀜이 일어났다는 뜻이라 수급(단타)으로 밀린 자리일 가능성이 높습니다. 이런 상승은 실적 상승과 달리 같은 속도로 되돌아오는 경우가 많습니다. "거래대금" 셀 아래에 회전율(당일 · 5일평균)을 같이 표시합니다.</p>
      <div class="term-box">
        <b>계산 방법</b>: 회전율(%) = 당일 거래대금 ÷ 시가총액 × 100
      </div>
      <ul>
        <li>5% 미만 — 정상 범위, 별도 표시 없음</li>
        <li><span class="ext-caut">5~20%</span> — 주의</li>
        <li><span class="ext-warn">20% 이상 · 수급과열</span> — 손바뀜이 시총만큼 벌어진 상태, 실적 근거와 별개로 되돌림 위험이 큼</li>
      </ul>
      <p class="note">시가총액은 조회 시점의 현재 값이라(과거 이력 없음), 대주주 지분이 큰 종목은 회전율이 실제보다 낮게 나올 수 있습니다.</p>

      <h4>이격 지속일수 — "며칠째 벌어져 있나"</h4>
      <p>50일선 이격이 며칠째 벌어진 상태인지를 보여줍니다. 연속으로 며칠째가 아니라 <b>최근 60거래일 중 이격 15%를 넘은 날의 총 횟수</b>로 셉니다 — 중간에 하루이틀 눌려도 카운트가 끊기지 않기 때문입니다. 급등 후 이 횟수가 줄어들기 시작하면(↓ 표시) 재베이스(눌림목으로 50일선을 따라잡는 과정)가 진행 중이라는 신호입니다.</p>
      <p>표에서는 <b>"이격일수"</b> 컬럼에서 확인할 수 있습니다(줄임말). 필터 바 드롭다운 옆 <b>물음표 버튼</b>을 누르면 같은 설명을 다시 볼 수 있습니다.</p>
      <ul>
        <li>필터에서 <b>5~15일</b> 범위로 좁히면 재베이스가 진행 중인 종목만 볼 수 있습니다</li>
        <li><b>16일 이상</b>은 장기간 확장된 상태 — 실적 근거 없이 벌어진 경우가 많습니다</li>
        <li>↓ 최근 5일간 초과일수가 줄어드는 중(좁혀지는 중) · ↑ 늘어나는 중 · → 변화 없음</li>
      </ul>

      <h4>50일선 이격% vs 이격일수 — 둘의 차이</h4>
      <p>이 둘은 "지금 순간의 스냅샷"과 "시간에 걸친 패턴"이라는, 완전히 다른 성격의 정보입니다.</p>
      <table style="width:100%;border-collapse:collapse;font-size:11.5px;margin:6px 0">
        <tr>
          <th style="text-align:left;padding:5px 8px;background:#1B2A4A;color:#fff">구분</th>
          <th style="text-align:left;padding:5px 8px;background:#1B2A4A;color:#fff">50일선 이격%</th>
          <th style="text-align:left;padding:5px 8px;background:#1B2A4A;color:#fff">이격일수</th>
        </tr>
        <tr><td style="padding:5px 8px;border-bottom:1px solid #EEF1F4">측정 대상</td>
            <td style="padding:5px 8px;border-bottom:1px solid #EEF1F4"><b>오늘 하루</b>, 현재가가 50일 평균에서 얼마나 떨어져 있는지</td>
            <td style="padding:5px 8px;border-bottom:1px solid #EEF1F4"><b>최근 60거래일</b>(3개월) 동안 그렇게 떨어진 날이 며칠이나 있었는지</td></tr>
        <tr><td style="padding:5px 8px;border-bottom:1px solid #EEF1F4">답하는 질문</td>
            <td style="padding:5px 8px;border-bottom:1px solid #EEF1F4">"지금 얼마나 비싼가?"</td>
            <td style="padding:5px 8px;border-bottom:1px solid #EEF1F4">"이 상태가 얼마나 오래됐나?"</td></tr>
        <tr><td style="padding:5px 8px">성격</td>
            <td style="padding:5px 8px">순간 값 (매일 바뀜)</td>
            <td style="padding:5px 8px">누적 값 (역사·패턴)</td></tr>
      </table>
      <div class="term-box">
        비유하자면, 이격%는 <b>체온계로 잰 지금 체온</b>이고, 이격일수는 <b>"최근 3개월 중 열이 며칠이나 났었나"</b>입니다. 지금 체온이 정상이어도(이격% 낮음), 최근 계속 열이 났다 내렸다 했다면(이격일수 높음) 완전히 다른 상황입니다.
      </div>
      <p><b>이격%만 보면 놓치는 것</b> — 오늘 이격이 마침 낮게 나온 두 종목이 있다고 합시다. A종목은 원래 조용했다가 오늘 처음 벌어졌고(이격일수 0~1일), B종목은 두 달째 벌어졌다 좁혀졌다를 반복하다 오늘 마침 잠깐 좁혀진 겁니다(이격일수 40일). 이격%만 보면 둘 다 "지금은 괜찮네"로 똑같이 보이지만, 실제로는 완전히 다른 종목입니다.</p>

      <p><b>SEPA(VCP) 판단에서 각각 쓰이는 곳</b></p>
      <ul>
        <li><b>이격%</b>는 진입 타이밍의 재료입니다 — VCP의 "피벗대비(%)" 계산에 들어가고, 숫자가 너무 크면(예: +30%) "확장(과열)"로 분류돼 진입 후보에서 빠집니다.</li>
        <li><b>이격일수 16일 이상(장기 확장)</b> — 오랫동안 과열이 안 풀린 종목입니다. 실적 없이 수급으로만 밀린 테마주일 가능성이 크고, VCP가 "진입가능"으로 떠도 한 번 더 의심해봐야 합니다.</li>
        <li><b>이격일수 5~15일(재베이스 관찰)</b> — 벌어졌다가 지금 막 좁혀지기 시작한 구간. VCP가 요구하는 "눌림목 폭이 좁아지는 패턴"과 정확히 겹치는 구간이라, 가장 눈여겨볼 만한 자리입니다.</li>
        <li><b>이격일수 0일</b> — 신한지주처럼 애초에 이격이 별로 안 벌어진 종목. 변동성 자체가 낮아서 VCP식 "폭발적 돌파"보다는 안정적인 대형주 특성에 가깝습니다.</li>
      </ul>
      <p class="note">둘 중 하나만 고르라면 <b>이격일수가 더 중요합니다.</b> 이격%는 매일 바뀌는 순간값이라 "오늘 우연히 낮게 나온 것"에 속을 수 있지만, 이격일수는 그 종목의 최근 석 달간 성격 자체를 말해줍니다. 실전에서는 ① 이격일수로 "이 종목이 원래 과열형인가, 안정형인가" 먼저 판단하고 ② 그 다음 이격%로 "지금 이 순간이 살 만한 자리인가"를 확인하는 순서로 같이 보는 것이 안전합니다.</p>

      <div class="example">
        <h4>📐 직접 검증해보기 — 신한지주(055550) 실제 사례</h4>
        <p>표에 나온 값(현재가 <b>112,900원</b>, 피벗대비 <b>-3.1%</b>, 손절리스크 <b>7.5%</b>)만으로 미니 차트의 피벗·손절 가격이 맞게 계산됐는지 직접 확인해볼 수 있습니다.</p>
        <ol>
          <li><b>피벗가격 역산</b> — 피벗대비(%) 공식을 거꾸로 풀면:
            <div class="formula">피벗가격 = 현재가 ÷ (1 + 피벗대비%÷100) = 112,900 ÷ (1 − 0.031) ≈ 116,500원</div>
          </li>
          <li><b>진입가 계산</b>:
            <div class="formula">진입가 = 피벗가격 × 1.01 = 116,500 × 1.01 ≈ 117,665원</div>
          </li>
          <li><b>손절가 역산</b> — 손절리스크% 공식을 거꾸로 풀면:
            <div class="formula">손절가 = 진입가 × (1 − 손절리스크%÷100) = 117,665 × (1 − 0.075) ≈ 108,840원</div>
          </li>
        </ol>
        <p>실제로 이 종목을 클릭해서 뜨는 미니 차트에는 <b>피벗 116,500 / 손절 108,900</b>으로 표시되는데, 위 계산과 거의 정확히 일치합니다(작은 차이는 실제 호가 단위 반올림 때문입니다). 다른 종목도 같은 방법으로 직접 검산해볼 수 있습니다.</p>
      </div>

      <h3>5. 유동성·가격 필터 (한국)</h3>
      <ul>
        <li>주가 2,000원 이상 (동전주 제외)</li>
        <li>20일 평균 거래대금 50억원 이상</li>
      </ul>

      <h3>6. 유동성·가격 필터 (미국)</h3>
      <ul>
        <li>주가 10달러 이상</li>
        <li>20일 평균 거래대금 1,000만달러 이상</li>
      </ul>

      <h3>7. 유니버스에서 제외되는 종목 (한국)</h3>
      <ul>
        <li><b>정리매매 의심 종목</b> — 15거래일 이내 32% 초과 등락이 2회 이상 겹치고, 캐시 최신일까지 거래가 이어지지 않는 종목. 상장폐지 확정 후 상하한가가 풀린 구간의 실제 거래라 가격을 신뢰할 수 없어 제외합니다.</li>
        <li><b>우선주</b> — 종목명이 "…우" 또는 "…우B"로 끝나는 종목. 보통주와 이중 계산되는 것을 막기 위해 <b>시장 폭 패널 계산에서만</b> 제외합니다. Momentum Watchlist(통과·관찰·전체 목록)에는 포함됩니다.</li>
      </ul>
      <p class="note">위 두 항목 중 우선주 제외 여부가 시장 폭과 Watchlist 사이에 다르게 적용되고 있어, 두 화면의 "8조건 통과" 숫자가 정확히 일치하지 않을 수 있습니다(보통 몇 종목 이내 차이). 시장 폭은 시장 전체의 체력을 보는 지표라 유동성 필터를 걸지 않고, Watchlist는 실제로 매매 가능한 후보만 남기기 위해 유동성 필터를 겁니다 — 두 화면의 목적이 달라 숫자도 다르게 설계돼 있습니다.</p>

      <h3>8. 데이터 기준</h3>
      <p>가격은 KRX Open API(한국)·yfinance(미국) 원자료를 씁니다. 한국은 액면분할·병합·1일 데이터 오류를 자동 탐지해 소급 보정한 값을 사용합니다(원본 자체가 수정주가가 아니기 때문입니다). 52주 신고가·신저가는 그 기간의 실제 장중 고가·저가 기준입니다(종가가 아닙니다 — 미네르비니 원 정의를 따릅니다). 고가·저가 데이터를 확보하지 못한 경우에만 예외적으로 종가로 근사하며, 그럴 땐 대시보드 상단 안내나 로그에 표시됩니다.</p>

      <p class="note">VCP 분석은 자동 근사치입니다. 판정 로직이 실제 차트의 패턴을 오인할 수 있으니, "진입가능"으로 뜬 종목도 반드시 차트로 직접 확인한 뒤 판단하세요. 이 화면은 SEPA 1단계(기술적 필터)만 담고 있어, 2단계 펀더멘털과 3단계 진입 시점은 직접 확인해야 합니다. 투자 참고 자료이며 투자 권유가 아닙니다.</p>

    </div>
  </div>
</div>

<script>
const DATA = __DATA__;
const DAYS = __DAYS__;      // 같은 폴더에 있는 다른 날짜 대시보드 목록
const CURRENT = "__CURRENT__";
const STRATEGY = __STRATEGY__;   // 매매전략 (null이면 버튼 숨김)
const MACRO_SNAPSHOT = __MACRO_SNAPSHOT__;   // market_macro.py 결과 (없으면 [])
const BREADTH_SNAPSHOT = __BREADTH_SNAPSHOT__;   // market_breadth.py 결과 (없으면 [])
const VCP_CHARTS = __VCP_CHARTS__;   // vcp.py 결과 (통과·관찰 종목만, 없으면 {})

// ── 시장 폭 패널 렌더 ─────────────────────────────────────
// [2026-09-12] tt8_count/above200_pct 등은 market_breadth.py가
// breadth/{market}_{date}.json 으로 미리 계산해둔 값을 그대로 받아 보여주기만
// 한다 — 대시보드 쪽에서 다시 계산하지 않는다.
(function(){
  const host = document.getElementById("breadthCards");
  const section = document.getElementById("breadthSection");
  if(!host || !section || !(BREADTH_SNAPSHOT||[]).length) return;
  section.hidden = false;

  const LABEL = {KOSPI:"코스피", KOSDAQ:"코스닥", KR_TOTAL:"한국 전체", SP500:"S&P 500"};
  let bh = "";
  for(const b of BREADTH_SNAPSHOT){
    const nm = LABEL[b.market] || b.market;
    if(b.status === "incomplete"){
      bh += `<div class="idx-card fail"><div class="nm">${nm}</div>
        <div class="val">데이터 부족</div></div>`;
      continue;
    }
    const cls = b.above200_pct==null ? "" : (b.above200_pct>=50?"pos":"neg");
    const deltaTxt = (b.above200_pct_delta==null) ? "" :
      (b.above200_pct_delta>0?"+":"") + b.above200_pct_delta.toFixed(1) + "p";
    bh += `<div class="idx-card compact">
      <div class="nm">${nm}</div>
      <div class="valrow"><span class="val num ${cls}">${b.above200_pct==null?"–":b.above200_pct}%</span>
      <span class="chg num ${cls}">200일선 위 ${deltaTxt?`(${deltaTxt})`:""}</span></div>
      <div class="sub">신고가 ${b.nh52} · 신저가 ${b.nl52} · 8조건 ${b.tt8_count}종목</div>
    </div>`;
  }
  host.innerHTML = bh;
})();

// ── 매크로 지표 패널 렌더 ──────────────────────────────
// [2026-09-12] 예전엔 "시황 개요"(지수)와 "리스크 신호"(VIX 등)를 따로
// 뒀었는데, 소스를 market_macro.py 하나(yfinance)로 통일하면서 화면도
// 하나로 합쳤다.
(function(){
  const idxHost = document.getElementById("idxCards");
  const idxHost2 = document.getElementById("idxCards2");
  if(!idxHost || !idxHost2) return;

  // 1~6번째(나스닥·S&P500·코스피·코스닥·원달러·VIX) 1행,
  // 7~11번째(미국채10년·30년·달러인덱스·WTI·금) 2행.
  // 순서는 market_macro.py의 TICKERS 순서를 그대로 따른다.
  const row1 = (MACRO_SNAPSHOT||[]).slice(0, 6);
  const row2 = (MACRO_SNAPSHOT||[]).slice(6);

  function renderRow(items, compact){
    let h = "";
    for(const c of items){
      if(!c.ok){
        h += `<div class="idx-card${compact?" compact":""} fail"><div class="nm">${c.name}</div><div class="val">–</div></div>`;
        continue;
      }
      const cls = c.change_pct>0?"pos":(c.change_pct<0?"neg":"");
      const sign = c.change_pct>0?"+":"";
      const warn = (c.name==="VIX" && c.value>=30);
      if(compact){
        h += `<div class="idx-card compact${warn?" warn":""}"><div class="nm">${c.name}</div>
          <div class="valrow"><span class="val num">${c.value.toLocaleString()}</span>
          <span class="chg num ${cls}">${sign}${c.change_pct}%</span></div></div>`;
      } else {
        h += `<div class="idx-card${warn?" warn":""}"><div class="nm">${c.name}</div>
          <div class="val num">${c.value.toLocaleString()}</div>
          <div class="chg num ${cls}">${sign}${c.change_pct}%</div></div>`;
      }
    }
    return h;
  }

  idxHost.innerHTML = renderRow(row1, true) || '<div class="idx-card fail">매크로 지표 없음</div>';
  idxHost2.innerHTML = renderRow(row2, true);
})();
let view="pass", mkt="US", minRS=0, q="", vcpFilter="", sortKey="cap", sortDir=-1, open=null;
let devDaysFilter="", hideOverheated=false;
let activeSector=null, sectorView="treemap";
let capInKRW=false;   // 미국 탭 시가총액을 원화(조원)로 환산해서 보여줄지

// 매크로 지표의 "원/달러" 값을 그대로 가져다 쓴다 — 별도로 환율을 다시
// 받아오지 않고, 화면 상단에 이미 있는 오늘자 환율 하나만 신뢰한다.
function usdKrwRate(){
  const row = (MACRO_SNAPSHOT||[]).find(m => m.name === "원/달러");
  return (row && row.ok) ? row.value : null;
}

const COLS=[
  ["ticker","종목",""],
  ["price","현재가",""],
  ["rs","RS",""],
  ["high","52주 고점 대비","rail"],
  ["low","저점대비%","hide-s"],
  ["ma50","50일선<br>이격%","hide-s"],
  ["devDays","이격일수","hide-s"],
  ["slope","200일선<br>기울기%","hide-s"],
  ["turnover","거래대금","hide-s"],
  ["cap","시가총액","hide-s"],
  ["pivot","피벗대비(%)","hide-s"],
  ["legs","수축","hide-s"],
  ["risk","손절리스크%","hide-s"],
  ["vcp","VCP","hide-s"],
  ["volRatio","거래량비율","hide-s"],
];

function capUnitLabel(m){ return m==="US" ? "* 시가총액($1B 기준)" : "* 시가총액(1천억원 기준)"; }

const fmtMoney=(v,m)=>{
  if(v==null) return "–";
  return m==="KR" ? (v/1e8).toLocaleString(undefined,{maximumFractionDigits:0})+"억"
                  : "$"+(v/1e6).toLocaleString(undefined,{maximumFractionDigits:0})+"M";
};
function fmtCap(v, m){
  if(v==null || isNaN(v)) return "–";
  if(m==="US"){
    // [2026-09-15] 원화 환산 토글이 켜져 있으면, d.cap(원본 달러 시총)에
    // 오늘자 원/달러 환율(매크로 지표 값)을 곱해 원화로 바꾼 뒤, 한국
    // 탭과 똑같은 "1천억원" 단위로 보여준다(단위 표기를 통일해 헷갈리지
    // 않게 하기 위함). 환율을 못 받아온 날은 달러 표시로 조용히
    // 되돌아간다(에러 대신).
    if(capInKRW){
      const rate = usdKrwRate();
      if(rate){
        const krw = v * rate;
        const u = krw / 1e11;
        return krw>=1e11 ? Math.round(u).toLocaleString() : u.toFixed(2);
      }
    }
    const b = v/1e9;
    return v>=1e12 ? Math.round(b).toLocaleString() : b.toFixed(2);
  }
  const u = v/1e11;
  return v>=1e11 ? Math.round(u).toLocaleString() : u.toFixed(2);
}
const sign=v=>v==null?"":(v>0?"pos":(v<0?"neg":""));

// 50일선 이격 경고
// 미네르비니는 50일선에서 크게 벌어진 종목의 신규 진입을 금한다.
// 이격이 크면 -7% 손절선이 기술적으로 아무 의미를 갖지 못하기 때문이다.
const EXT_WARN = 25;   // 이 이상이면 신규 진입 부적합
const EXT_CAUT = 15;   // 이 이상이면 주의
function ext(v){
  if(v==null) return "–";
  const txt=(v>0?"+":"")+v.toFixed(1);
  if(v>=EXT_WARN) return `<span class="ext-warn" title="50일선 위로 ${v}% 이격 · 과열 구간, 신규 진입 부적합">${txt} !</span>`;
  if(v>=EXT_CAUT) return `<span class="ext-caut" title="50일선 위로 ${v}% 이격 · 진입 시 손절폭 확인">${txt}</span>`;
  return `<span class="${sign(v)}">${txt}</span>`;
}
const num=v=>v==null?"–":v.toLocaleString(undefined,{maximumFractionDigits:2});

// 이격 지속일수 — "최근 60거래일 중 이격이 임계값을 넘은 날의 개수"(연속 스트릭 아님).
// 중간에 하루이틀 좁혀졌다 다시 벌어져도 카운트가 끊기지 않아, 재베이스 진행
// 상황을 range 필터("5~15일" 등)로 추적할 수 있다. 50일선 이격% 셀에 병기한다.
function ma50Cell(v){
  return ext(v);
}
// 이격일수 = 최근 60거래일 중 이격 15% 초과한 날의 총 횟수(연속 스트릭 아님).
// devTrend: 최근 5일 초과일수가 그 이전 5일보다 좁아지는지(↓)/늘어나는지(↑) 방향.
function devDaysCell(devDays, devTrend, devPeak){
  if(devDays==null) return "–";
  if(devDays===0) return `<span class="vr-normal">0일</span>`;
  const peakTxt = devPeak==null ? "" : `구간 내 최대 ${devPeak>0?"+":""}${devPeak.toFixed(1)}%`;
  return `<span title="최근 60거래일 중 이격 15% 초과 · ${peakTxt}">${devDays}일 ${devTrend||""}</span>`;
}

// 회전율(%) = 거래대금 ÷ 시가총액. 실적이 아니라 수급(단타)으로 밀린 자리는
// 회전율이 비정상적으로 높다 — 같은 속도로 되돌아오는 경우가 많다.
const TURNOVER_HOT = 20;    // 이 이상이면 수급과열
const TURNOVER_CAUT = 5;    // 이 이상이면 주의
function turnoverCell(raw, market, ratio, ratio5d){
  const base = fmtMoney(raw, market);
  if(ratio==null) return base;
  const r5 = ratio5d==null ? "" : ` · 5일평균 ${ratio5d.toFixed(1)}%`;
  const title = `당일 회전율 = 거래대금÷시가총액${r5}`;
  if(ratio>=TURNOVER_HOT){
    return `${base}<div class="vcp-sub" title="${title}"><span class="ext-warn">회전율 ${ratio.toFixed(1)}% · 수급과열</span></div>`;
  }
  if(ratio>=TURNOVER_CAUT){
    return `${base}<div class="vcp-sub" title="${title}"><span class="ext-caut">회전율 ${ratio.toFixed(1)}%</span></div>`;
  }
  return `${base}<div class="vcp-sub" title="${title}">회전율 ${ratio.toFixed(1)}%</div>`;
}

// VCP 표시용 포맷터
const VCP_LABEL = {
  entry_ready: "진입가능", developing: "형성중", extended: "확장(과열)",
  no_pattern: "패턴없음", unknown: "–",
};
function vcpBadge(status, tightening, dryup){
  if(status==null) return "–";
  const cls = status==="entry_ready" ? "vcp-ready"
            : status==="extended" ? "vcp-ext" : "vcp-dev";
  const marks = [];
  if(tightening===true) marks.push("수축↓");
  if(dryup===true) marks.push("거래량↓");
  const sub = marks.length ? `<div class="vcp-sub">${marks.join(" · ")}</div>` : "";
  return `<div class="vcp-cell"><span class="vcp-badge ${cls}">${VCP_LABEL[status]||status}</span>${sub}</div>`;
}
// 돌파일 거래량 비율 — 값과 등급을 같이 보여준다. 값만 있으면 "몇 배가
// 터짐 기준인지" 매번 외워야 해서, 등급 라벨을 항상 옆에 붙인다.
const VOL_RATIO_LABEL = { surge: "터짐", normal: "보통", low: "부족" };
function volRatioCell(ratio, label){
  if(ratio==null) return "–";
  const cls = label==="surge" ? "vr-surge" : (label==="low" ? "vr-low" : "vr-normal");
  const txt = VOL_RATIO_LABEL[label] || "";
  return `<span class="vr-badge ${cls}" title="오늘 거래대금이 직전 20일 평균의 ${ratio}배 (1.5배 이상=터짐, 1.0배 미만=부족)">${ratio}배 · ${txt}</span>`;
}
function pivotCell(v, pivotPrice){
  if(v==null) return "–";
  const txt=(v>0?"+":"")+v.toFixed(1)+"%";
  const cls = (v>=-3 && v<=2) ? "pos" : (v>2 ? "neg" : "");
  const priceTxt = pivotPrice==null ? "" : ` (피벗가 ${pivotPrice.toLocaleString()})`;
  return `<span class="${cls}" title="현재가가 피벗 대비 ${v>0?"위":"아래"} ${Math.abs(v).toFixed(1)}%${priceTxt}">${txt}</span>`;
}
function riskCell(v, stopPrice){
  if(v==null) return "–";
  const cls = v<=8 ? "pos" : (v<=10 ? "" : "neg");
  const priceTxt = stopPrice==null ? "" : `손절가 ${stopPrice.toLocaleString()}`;
  return `<span class="${cls}" title="${priceTxt}">${v.toFixed(1)}%</span>`;
}

// 고점 근접도: -25%(왼쪽) → 0%(오른쪽, 신고가)
function rail(v){
  // 막대만으로는 값을 읽을 수 없고 인쇄 시 배경이 지워져 통째로 사라진다.
  // 그래서 항상 숫자를 함께 찍는다. 막대는 보조 표현으로만 둔다.
  if(v==null) return '<div class="railwrap"><span class="railnum">–</span><div class="rail"></div></div>';
  const p=Math.max(0,Math.min(100,(v+25)/25*100));
  const out=v<-25?" out":"";
  const txt=(v>0?"+":"")+v.toFixed(1)+"%";
  const near=v>=-3?" near":"";
  return `<div class="railwrap" title="52주 고점 대비 ${v}%">
    <span class="railnum${out}${near}">${txt}</span>
    <div class="rail">
      <div class="zone"></div><div class="track"></div><div class="peak"></div>
      <div class="dot${out}" style="left:${p}%"></div>
    </div></div>`;
}

// ── 관심종목 (브라우저에 저장, 기기별로 따로 관리됨) ──────
const FAV_KEY = "sepa_favorites_v1";
function loadFavs(){
  try{
    const raw = localStorage.getItem(FAV_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  }catch(_e){ return new Set(); }   // 시크릿 모드 등에서 저장이 막힌 경우
}
function saveFavs(set){
  try{ localStorage.setItem(FAV_KEY, JSON.stringify([...set])); }
  catch(_e){ /* 저장 실패해도 화면 동작은 유지 */ }
}
let FAVS = loadFavs();
const favKey = d => `${d.market}:${d.ticker}`;   // 시장까지 포함해 섞이지 않게

// 시장별로 통계(통과/관찰 수)를 다시 센다 — 전체 DATA가 아니라 선택된 시장 안에서만
function updateStats(){
  const inMkt = DATA.filter(d=>d.market===mkt);
  document.getElementById("statPass").textContent = inMkt.filter(d=>d.pass).length;
  document.getElementById("statNear").textContent = inMkt.filter(d=>!d.pass && d.met>=7).length;
  document.getElementById("statFav").textContent = inMkt.filter(d=>FAVS.has(favKey(d))).length;
}

function filtered(){
  return DATA.filter(d=>{
    if(view==="pass" && !d.pass) return false;
    if(view==="near" && (d.pass || d.met<7)) return false;
    if(view==="fav" && !FAVS.has(favKey(d))) return false;   // 관심: 조건과 무관하게 찜한 것 전부
    if(d.market!==mkt) return false;
    if(d.rs!=null && d.rs<minRS) return false;
    if(vcpFilter && d.vcp!==vcpFilter) return false;
    if(devDaysFilter){
      const [lo,hi]=devDaysFilter.split("-").map(Number);
      const dd = d.devDays ?? 0;
      if(dd<lo || dd>hi) return false;
    }
    if(hideOverheated && d.turnoverRatio!=null && d.turnoverRatio>=TURNOVER_HOT) return false;
    // [2026-09-16] 문자열이 화면엔 똑같이 "금융"으로 보여도, 한쪽은 완성형
    // (NFC)·다른 쪽은 자모분리형(NFD)일 수 있다(맥OS에서 이 프로젝트가 전에
    // 파일명으로 겪었던 것과 같은 부류의 문제). normalize("NFC")로 강제
    // 통일해서 비교해야 "선택은 됐는데 필터링은 하나도 안 되는" 증상을 막는다.
    if(activeSector && (d.sector||"").normalize("NFC")!==activeSector.normalize("NFC")) return false;
    if(q){
      const s=(d.ticker+" "+d.name).toLowerCase();
      if(!s.includes(q.toLowerCase())) return false;
    }
    return true;
  }).sort((a,b)=>{
    const x=a[sortKey], y=b[sortKey];
    if(x==null) return 1;
    if(y==null) return -1;
    if(typeof x==="string") return x.localeCompare(y)*sortDir;
    return (x-y)*sortDir;
  });
}

function esc(t){ return String(t).replace(/"/g,"&quot;").replace(/</g,"&lt;"); }

function render(){
  updateStats();
  const rows=filtered();
  document.getElementById("shown").textContent=rows.length;
  // 필터 줄 오른쪽 끝 — 지금 걸린 조건(구분·시장·RS·VCP·검색어) 전부를
  // 반영한 결과 개수를 바로 옆에서 보여준다. 위 통계 요약(shown)과 같은
  // 값이지만, 필터를 만지는 그 자리에서 바로 확인되게 하기 위함이다.
  const cntEl = document.getElementById("filteredCount");
  if(cntEl) cntEl.textContent = `${rows.length.toLocaleString()}개 종목`;
  const host=document.getElementById("host");
  document.getElementById("favSync").hidden = (view!=="fav");

  if(!rows.length){
    host.innerHTML='<div class="empty">'
      + (view==="fav"
          ? "관심종목이 없습니다. 종목 왼쪽의 별을 눌러 추가하세요."
          : "조건에 맞는 종목이 없습니다. RS 기준을 낮추거나 구분을 바꿔 보세요.")
      + '</div>';
    return;
  }

  const capNote = mkt==="US"
    ? (capInKRW
        ? `<span>* 시가총액(1천억원 기준)</span> <button id="capToggleBtn" class="help-btn cap-toggle" type="button">달러로</button>`
        : `<span>* 시가총액($1B 기준)</span> <button id="capToggleBtn" class="help-btn cap-toggle" type="button">원화 환산</button>`)
    : capUnitLabel(mkt);
  let h=`<div class="tbl-note">${capNote}</div>
    <div class="tbl-scroll"><table><thead><tr>`;
  for(const [k,label,cls] of COLS){
    const on = sortKey===k ? ` aria-sort="${sortDir===1?"ascending":"descending"}"` : "";
    // 시가총액 헤더만 예외적으로 단위를 동적으로 바꿔 보여준다(달러 ↔ 원화 환산).
    const headLabel = (k==="cap")
      ? (mkt==="US" ? (capInKRW ? "시가총액<br>(1천억원)" : "시가총액<br>($1B)") : label)
      : label;
    h+=`<th class="${cls==="hide-s"?"hide-s":""}" data-k="${k}"${on}>${headLabel}</th>`;
  }
  h+='</tr></thead><tbody>';

  for(const d of rows){
    const same=d.name===d.ticker;
    const nm = same ? "" : (d.name.length>15 ? d.name.slice(0,15)+"…" : d.name);
    const on = FAVS.has(favKey(d));
    // 미달 조건은 클릭이 아니라 마우스오버(모바일은 배지 탭)로 보여준다
    const tipBody = d.fails.length
      ? `<b>미달 ${d.fails.length}개</b>${d.fails.map(f=>"· "+f).join("<br>")}`
      : `<b>8개 조건 모두 충족</b>`;
    const badge = d.pass
      ? '<span class="badge p">통과</span>'
      : `<span class="badge" data-tip="1">${d.met}/8</span>`;
    // [2026-09-15] 수급과열(회전율 TURNOVER_HOT 이상) 경고를 항상 보이는
    // 종목명 칸에도 표시한다 — 거래대금 칸은 hide-s라 좁은 화면(모바일)에서
    // 숨겨지는데, 그러면 수급과열 경고 자체를 놓치게 되기 때문이다.
    const hotWarn = (d.turnoverRatio!=null && d.turnoverRatio>=TURNOVER_HOT)
      ? `<span class="hot-warn" title="회전율 ${d.turnoverRatio.toFixed(1)}% · 수급과열">!</span>`
      : "";

    h+=`<tr data-t="${d.ticker}" data-m="${d.market}" tabindex="0"${open===d.ticker?' class="sel"':''}>
      <td><span class="star${on?" on":""}" data-fav="${esc(favKey(d))}"
            title="${on?"관심종목에서 제거":"관심종목에 추가"}">${on?"★":"☆"}</span
        ><span class="cellwrap"><span class="tk">${d.ticker}</span>${badge}${hotWarn}
          ${same?"":`<span class="nm" title="${esc(d.name)}">${nm}</span>`}
          <span class="tip">${tipBody}</span></span></td>
      <td class="num">${num(d.price)}</td>
      <td class="num"><strong>${d.rs==null?"–":d.rs}</strong></td>
      <td>${rail(d.high)}</td>
      <td class="num hide-s ${sign(d.low)}">${num(d.low)}</td>
      <td class="num hide-s">${ma50Cell(d.ma50)}</td>
      <td class="num hide-s">${devDaysCell(d.devDays, d.devTrend, d.devPeak)}</td>
      <td class="num hide-s ${sign(d.slope)}">${num(d.slope)}</td>
      <td class="num hide-s">${turnoverCell(d.turnover, d.market, d.turnoverRatio, d.turnoverRatio5d)}</td>
      <td class="num hide-s">${fmtCap(d.cap,d.market)}</td>
      <td class="num hide-s">${pivotCell(d.pivot, d.pivotPrice)}</td>
      <td class="num hide-s">${d.legs==null?"–":d.legs+"개"}</td>
      <td class="num hide-s">${riskCell(d.risk, d.stopPrice)}</td>
      <td class="hide-s">${vcpBadge(d.vcp, d.tight, d.dryup)}</td>
      <td class="num hide-s">${volRatioCell(d.volRatio, d.volRatioLabel)}</td></tr>`;
  }
  host.innerHTML=h+"</tbody></table></div>";
}

document.addEventListener("click",e=>{
  const seg=e.target.closest(".seg button");
  if(seg){
    const grp=seg.parentElement;
    [...grp.children].forEach(b=>b.setAttribute("aria-pressed","false"));
    seg.setAttribute("aria-pressed","true");
    if(seg.dataset.view) view=seg.dataset.view;
    if(seg.dataset.mkt) mkt=seg.dataset.mkt;
    open=null; render(); return;
  }
  const th=e.target.closest("th[data-k]");
  if(th){
    const k=th.dataset.k;
    // 새 컬럼을 클릭하면 오름차순부터 시작, 같은 컬럼을 다시 누르면 방향 반전
    if(sortKey===k) sortDir*=-1; else {sortKey=k; sortDir=1;}
    render(); return;
  }
  // 별: 관심종목 토글. 행 클릭(차트 열기)과 겹치지 않게 여기서 처리를 끝낸다.
  const star=e.target.closest(".star[data-fav]");
  if(star){
    e.stopPropagation();
    const k=star.dataset.fav;
    if(FAVS.has(k)) FAVS.delete(k); else FAVS.add(k);
    saveFavs(FAVS);
    render();
    return;
  }

  // 배지 탭: 모바일에선 마우스오버가 없으므로 탭으로 툴팁을 띄운다
  const badge=e.target.closest('.badge[data-tip]');
  if(badge){
    e.stopPropagation();
    const tip=badge.closest(".cellwrap")?.querySelector(".tip");
    document.querySelectorAll(".tip.pin").forEach(t=>{ if(t!==tip) t.classList.remove("pin"); });
    tip?.classList.toggle("pin");
    return;
  }

  const tr=e.target.closest("tbody tr[data-t]");
  if(tr){
    const t=tr.dataset.t, m=tr.dataset.m;
    if(m==="KR"){
      openNaverChart(t);
      // [2026-09-13] 한국 종목은 TradingView가 아니라 네이버 탭을 쓰므로,
      // 이전에 미국 종목을 봤을 때 열려 있던 TradingView 패널이 그대로
      // 남아 있으면 "지금 보는 게 어느 종목이지?" 헷갈린다. 클릭 시 정리한다.
      open=null; hideChart();
    } else if(open===t){ open=null; hideChart(); } else { open=t; showChart(t, m); }
    toggleVcpChart(t, m);
    render();
    return;
  }

  // 표 바깥을 누르면 열려 있던 툴팁을 닫는다
  document.querySelectorAll(".tip.pin").forEach(t=>t.classList.remove("pin"));
});

document.addEventListener("keydown",e=>{
  if(e.key!=="Enter" && e.key!==" ") return;
  const tr=e.target.closest && e.target.closest("tbody tr[data-t]");
  if(tr){
    e.preventDefault();
    const t=tr.dataset.t, m=tr.dataset.m;
    if(m==="KR"){
      openNaverChart(t);
      open=null; hideChart();
    } else if(open===t){ open=null; hideChart(); } else { open=t; showChart(t, m); }
    toggleVcpChart(t, m);
    render();
  }
});

// ── 종목 차트 ────────────────────────────────────────────
// 미국은 TradingView 위젯을 화면 안에 그대로 띄운다.
// [2026-09-12] 한국은 TradingView 무료 위젯이 코스닥 중소형주 상당수를
// 커버하지 못하고(차트 공백), 네이버 이미지 임베드도 화질·기간전환이
// 제대로 안 돼(당일 분봉 썸네일만 제공하는 것으로 확인) 둘 다 포기했다.
// 대신 네이버 금융 종목 페이지를 "같은 이름의 탭"으로 열어, 클릭할
// 때마다 새 탭이 계속 쌓이지 않고 기존 탭 내용만 바뀌게 한다.
function tvSymbol(ticker, market){
  return market==="KR" ? `KRX:${ticker}` : ticker;
}
function openNaverChart(ticker){
  window.open(`https://finance.naver.com/item/main.naver?code=${ticker}`, "naverChartTab");
}
function showChart(ticker, market){
  const wrap=document.getElementById("chartWrap");
  const box=document.getElementById("chartBox");
  const row=DATA.find(d=>d.ticker===ticker && d.market===market);
  document.getElementById("chartTitle").textContent =
    (row && row.name!==row.ticker) ? `${row.name} (${ticker})` : ticker;

  const sym=tvSymbol(ticker, market);
  const q=new URLSearchParams({
    symbol:sym, interval:"D", range:"12M", theme:"light", style:"1",
    locale:"kr", hide_side_toolbar:"0", allow_symbol_change:"0",
    withdateranges:"1", save_image:"0", timezone:"Asia/Seoul",
  });
  box.innerHTML=`<iframe loading="lazy" title="${ticker} 차트"
    src="https://s.tradingview.com/widgetembed/?${q.toString()}"></iframe>`;
  wrap.hidden=false;
  wrap.scrollIntoView({behavior:"smooth", block:"nearest"});
}
function hideChart(){
  const wrap=document.getElementById("chartWrap");
  wrap.hidden=true;
  document.getElementById("chartBox").innerHTML="";   // 정지시켜 리소스 낭비 방지
}

// ── VCP 미니 차트 (피벗선·손절선·레그 음영) ──────────────
// [2026-09-13] 통과·관찰(7조건 이상) 종목만 vcp.py가 미리 계산해 넘겨준
// 130일치 가격을 그대로 그린다 — 여기서 다시 계산하지 않는다. TradingView/
// 네이버 차트와 달리 "레그를 왜 저렇게 잘랐는지"를 검증하기 위한 용도라,
// 분석에 쓴 것과 정확히 같은 구간·같은 좌표를 그려야 의미가 있다.
let vcpOpen = null;
function toggleVcpChart(ticker, market){
  const chart = VCP_CHARTS[ticker];
  if(!chart){ hideVcpChart(); return; }   // 관찰 미만 종목 등, 분석 대상이 아니었던 경우
  if(vcpOpen===ticker){ hideVcpChart(); return; }
  vcpOpen = ticker;
  const row = DATA.find(d=>d.ticker===ticker && d.market===market);
  document.getElementById("vcpChartTitle").textContent =
    (row && row.name!==row.ticker) ? `${row.name} (${ticker}) · VCP 분석` : `${ticker} · VCP 분석`;
  document.getElementById("vcpChartBox").innerHTML = buildVcpSvg(chart);
  const wrap = document.getElementById("vcpChartWrap");
  wrap.hidden = false;
}
function hideVcpChart(){
  vcpOpen = null;
  const wrap = document.getElementById("vcpChartWrap");
  wrap.hidden = true;
  document.getElementById("vcpChartBox").innerHTML = "";
}
document.getElementById("vcpChartClose").addEventListener("click", hideVcpChart);

function buildVcpSvg(chart){
  const W = 720, H = 260, padL = 44, padR = 12, padT = 26, padB = 22;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const closes = chart.close, highs = chart.high, lows = chart.low;
  const n = closes.length;
  const lo = Math.min(...lows, chart.stop_price);
  const hi = Math.max(...highs, chart.pivot_price);
  const span = (hi - lo) || 1;
  const x = i => padL + (i/(n-1)) * innerW;
  const y = v => padT + (1 - (v - lo)/span) * innerH;

  // 종가 라인
  let linePts = closes.map((c,i)=>`${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(" ");

  // 레그 음영 (조정 구간을 옅은 회색으로)
  let legRects = "";
  (chart.legs||[]).forEach((leg,i)=>{
    const x1 = x(leg.high_idx), x2 = x(leg.low_idx);
    const shade = i===(chart.legs.length-1) ? "rgba(37,99,235,.10)" : "rgba(120,120,120,.07)";
    legRects += `<rect x="${Math.min(x1,x2).toFixed(1)}" y="${padT}" width="${Math.abs(x2-x1).toFixed(1)}" height="${innerH}" fill="${shade}"/>`;
  });

  // 피벗선(초록 점선) · 손절선(빨강 점선)
  const pivotY = y(chart.pivot_price), stopY = y(chart.stop_price);
  const pivotLine = `<line x1="${padL}" y1="${pivotY.toFixed(1)}" x2="${W-padR}" y2="${pivotY.toFixed(1)}" stroke="#1a7f37" stroke-width="1.3" stroke-dasharray="4 3"/>
    <text x="${W-padR}" y="${Math.max(pivotY-4, 11).toFixed(1)}" font-size="10" fill="#1a7f37" text-anchor="end">피벗 ${chart.pivot_price}</text>`;
  const stopLine = `<line x1="${padL}" y1="${stopY.toFixed(1)}" x2="${W-padR}" y2="${stopY.toFixed(1)}" stroke="#c0343b" stroke-width="1.3" stroke-dasharray="4 3"/>
    <text x="${W-padR}" y="${Math.max(stopY-4, 11).toFixed(1)}" font-size="10" fill="#c0343b" text-anchor="end">손절 ${chart.stop_price}</text>`;

  // x축 날짜 라벨 (처음·중간·마지막만)
  const labelIdx = [0, Math.floor(n/2), n-1];
  let xLabels = labelIdx.map(i=>`<text x="${x(i).toFixed(1)}" y="${H-6}" font-size="9.5" fill="#8a90a0"
    text-anchor="${i===0?"start":(i===n-1?"end":"middle")}">${chart.dates[i]}</text>`).join("");

  return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
    ${legRects}
    ${pivotLine}
    ${stopLine}
    <polyline points="${linePts}" fill="none" stroke="#1B2A4A" stroke-width="1.6"/>
    ${xLabels}
  </svg>`;
}
document.getElementById("chartClose").addEventListener("click",()=>{
  open=null; hideChart(); render();
});

// ── 매크로 지표·시장 폭 접기 ──────────────────────────────
// [2026-09-14] 둘 다 "하루에 한 번 훑어보면 그만"인 정보라, 확인 후엔
// 닫기 버튼 하나로 두 섹션(매크로 지표 + 시장 폭)을 한꺼번에 접는다.
// 시장 폭은 매크로 지표 안에 중첩된 구조라, 부모(macroCollapseBody)만
// 접으면 자동으로 같이 접힌다 — 시장 폭 자체의 표시 여부(hidden)는
// 건드리지 않으므로 다시 펼쳤을 때 원래 상태 그대로 돌아온다.
(function(){
  const btn = document.getElementById("macroCollapseBtn");
  const body = document.getElementById("macroCollapseBody");
  if(!btn || !body) return;
  btn.addEventListener("click", () => {
    const collapsed = body.classList.toggle("collapsed");
    btn.textContent = collapsed ? "펼치기" : "닫기";
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  });
})();

// ── 미국 시가총액 원화 환산 토글 ──────────────────────────
// 표가 render()마다 통째로 다시 그려지므로, 버튼도 매번 새로 만들어진다.
// 직접 바인딩하면 다음 렌더에서 리스너가 날아가므로 document에 위임한다.
document.addEventListener("click", e=>{
  if(e.target && e.target.id==="capToggleBtn"){
    capInKRW = !capInKRW;
    render();
  }
});

// ── 이격 지속일수 설명 팝업 ────────────────────────────────
(function(){
  const btn = document.getElementById("devDaysHelpBtn");
  const overlay = document.getElementById("devDaysInfoOverlay");
  const closeBtn = document.getElementById("devDaysInfoClose");
  if(!btn || !overlay) return;
  const open = () => { overlay.hidden = false; };
  const close = () => { overlay.hidden = true; };
  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", e => { if(e.target === overlay) close(); });
  document.addEventListener("keydown", e => { if(e.key === "Escape" && !overlay.hidden) close(); });
})();

// ── 업종 강도 패널 (트리맵 / 표) ────────────────────────────
// [2026-09-15] sepa_scanner.py가 붙인 실제 sector 필드를 그대로 쓴다.
// 업종 분류가 하나도 없으면(pykrx 미설치·네트워크 실패 등) 패널 자체를
// 숨긴다 — 빈 트리맵을 보여주는 것보다 아예 안 보이는 게 덜 헷갈린다.
(function(){
  const section = document.getElementById("sectorSection");
  const hasSectorData = DATA.some(d => d.market==="KR" && d.sector);
  if(!hasSectorData) return;   // 섹션이 hidden인 채로 남는다
  section.hidden = false;

  const MAX_SECTORS_SHOWN = 14;   // 업종이 너무 많으면 박스가 잘게 쪼개져 글씨가 잘린다

  function buildSectors(){
    const kr = DATA.filter(d => d.market === "KR" && d.sector);
    const groups = {};
    for(const d of kr){
      const s = d.sector.normalize("NFC");   // 그룹 나뉨 방지 — 위 필터와 같은 이유
      (groups[s] = groups[s] || []).push(d);
    }
    let sectors = Object.entries(groups).map(([name, items])=>{
      const n = items.length;
      const passN = items.filter(d=>d.pass).length;
      const avgRS = items.reduce((s,d)=>s+(d.rs||0),0) / n;
      const readyN = items.filter(d=>d.vcp==="entry_ready").length;
      const ratios = items.filter(d=>d.turnoverRatio!=null).map(d=>d.turnoverRatio);
      const avgTurnover = ratios.length ? ratios.reduce((a,b)=>a+b,0)/ratios.length : null;
      return {name, n, passN, passRate: passN/n*100, avgRS, readyN, avgTurnover};
    }).sort((a,b)=>b.n-a.n);

    if(sectors.length > MAX_SECTORS_SHOWN){
      const kept = sectors.slice(0, MAX_SECTORS_SHOWN - 1);
      const rest = sectors.slice(MAX_SECTORS_SHOWN - 1);
      const n = rest.reduce((s,x)=>s+x.n,0);
      const passN = rest.reduce((s,x)=>s+x.passN,0);
      const avgRS = rest.reduce((s,x)=>s+x.avgRS*x.n,0) / n;
      const readyN = rest.reduce((s,x)=>s+x.readyN,0);
      const turnoverVals = rest.filter(x=>x.avgTurnover!=null);
      const avgTurnover = turnoverVals.length
        ? turnoverVals.reduce((s,x)=>s+x.avgTurnover,0)/turnoverVals.length : null;
      kept.push({name:"기타", n, passN, passRate: passN/n*100, avgRS, readyN, avgTurnover});
      sectors = kept;
    }
    return sectors;
  }

  // 단순 이등분 슬라이스 트리맵 — 업종 5~10개 수준에서는 이걸로 충분하다.
  function flattenLayout(items, x, y, w, h){
    function rec(arr, x, y, w, h, horiz){
      if(arr.length === 1) return [{...arr[0], x, y, w, h}];
      const mid = Math.ceil(arr.length/2);
      const left = arr.slice(0,mid), right = arr.slice(mid);
      const total = arr.reduce((s,it)=>s+it.n,0);
      const leftSum = left.reduce((s,it)=>s+it.n,0);
      const ratio = total>0 ? leftSum/total : 0.5;
      if(horiz){
        const w1 = w*ratio;
        return [...rec(left, x, y, w1, h, false), ...rec(right, x+w1, y, w-w1, h, false)];
      } else {
        const h1 = h*ratio;
        return [...rec(left, x, y, w, h1, true), ...rec(right, x, y+h1, w, h-h1, true)];
      }
    }
    return rec(items, x, y, w, h, true);
  }

  // [2026-09-16] 한국 증시 관행(상승=빨강·하락=파랑)에 맞춘다. 이전엔 초록~
  // 주황 계열이라 "익숙한 관례와 다르다"는 지적을 받았다 — 값 자체(평균 RS)는
  // 그때도 의미가 있었지만, 색의 방향이 관례와 어긋나 헷갈릴 수 있었다.
  function rsColor(rs){
    if(rs>=60) return "#C0343B";   // 강세 — var(--up)과 동일한 빨강
    if(rs>=40) return "#B4B2A9";   // 변화 없음 — 회색
    return "#6EC1E4";              // 약세 — 하늘색
  }

  function renderTreemap(){
    const sectors = buildSectors();
    const W = 960, H = 200;
    const boxes = flattenLayout(sectors, 0, 0, W, H);
    let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="업종 강도 트리맵">`;
    for(const b of boxes){
      const isActive = activeSector === b.name;
      // 박스가 너무 좁거나 낮으면 글씨가 테두리 밖으로 삐져나가 잘려 보인다.
      // 그런 박스는 업종명만 남기고 나머지 줄은 아예 안 그린다.
      const showSub = b.w >= 70 && b.h >= 36;
      const showThird = b.w >= 90 && b.h >= 54;
      svg += `<g data-sector="${esc(b.name)}">
        <rect x="${b.x.toFixed(1)}" y="${b.y.toFixed(1)}" width="${b.w.toFixed(1)}" height="${b.h.toFixed(1)}"
          fill="${rsColor(b.avgRS)}" ${isActive ? 'stroke="#7ED321" stroke-width="4"' : ''}/>
        <text x="${(b.x+6).toFixed(1)}" y="${(b.y+16).toFixed(1)}" fill="${isActive?'#FFE24A':'#fff'}" font-size="12" font-weight="700">${esc(b.name)}</text>
        ${showSub ? `<text x="${(b.x+6).toFixed(1)}" y="${(b.y+31).toFixed(1)}" fill="#fff" font-size="9.5" opacity="0.9">RS ${b.avgRS.toFixed(0)} · 통과 ${b.passN}/${b.n}</text>` : ""}
        ${(showThird && b.readyN>0) ? `<text x="${(b.x+6).toFixed(1)}" y="${(b.y+45).toFixed(1)}" fill="#fff" font-size="8.5" opacity="0.9">진입가능 ${b.readyN}개</text>` : ""}
      </g>`;
    }
    svg += "</svg>";
    document.getElementById("treemapWrap").innerHTML = svg;
    document.querySelectorAll("#treemapWrap g[data-sector]").forEach(g=>{
      g.addEventListener("click", ()=> toggleSector(g.dataset.sector));
    });
  }

  let sectorSortKey = "n", sectorSortDir = -1;
  const SECTOR_SORT_KEYS = { "업종": "name", "종목 수": "n", "평균 RS": "avgRS",
    "8조건 통과율": "passRate", "진입가능(VCP)": "readyN", "평균 회전율": "avgTurnover" };

  function renderSectorTable(){
    const sectors = buildSectors().slice().sort((a,b)=>{
      const x = a[sectorSortKey], y = b[sectorSortKey];
      if(x==null) return 1;
      if(y==null) return -1;
      if(typeof x === "string") return x.localeCompare(y) * sectorSortDir;
      return (x-y) * sectorSortDir;
    });
    const tbody = document.getElementById("sectorTbody");
    tbody.innerHTML = sectors.map(s=>{
      const heat = s.avgTurnover!=null && s.avgTurnover>=TURNOVER_HOT
        ? `<span class="ext-warn" style="font-size:10px">${s.avgTurnover.toFixed(1)}%</span>`
        : (s.avgTurnover!=null ? s.avgTurnover.toFixed(1)+"%" : "–");
      const rowBg = activeSector===s.name ? ' style="background:#FFF8D6"' : "";
      return `<tr data-sector="${esc(s.name)}"${rowBg}>
        <td style="text-align:left;font-weight:700">${esc(s.name)}</td>
        <td class="num">${s.n}개</td>
        <td class="num"><strong>${s.avgRS.toFixed(0)}</strong></td>
        <td class="num">${s.passN}/${s.n} (${s.passRate.toFixed(0)}%)</td>
        <td class="num">${s.readyN>0?`<span style="color:#1a7f37;font-weight:700">${s.readyN}개</span>`:"–"}</td>
        <td class="num">${heat}</td>
      </tr>`;
    }).join("");
    tbody.querySelectorAll("tr[data-sector]").forEach(tr=>{
      tr.addEventListener("click", ()=> toggleSector(tr.dataset.sector));
    });
  }

  document.querySelectorAll("#sectorTable th").forEach(th=>{
    th.style.cursor = "pointer";
    th.addEventListener("click", ()=>{
      const key = SECTOR_SORT_KEYS[th.textContent.trim()];
      if(!key) return;
      if(sectorSortKey === key){ sectorSortDir *= -1; }
      else { sectorSortKey = key; sectorSortDir = -1; }
      renderSectorTable();
    });
  });

  function toggleSector(name){
    activeSector = (activeSector === name) ? null : name;
    const note = document.getElementById("sectorActiveNote");
    if(activeSector){
      note.style.display = "block";
      note.innerHTML = `<b>"${esc(activeSector)}"</b> 업종만 표시 중 — 다시 클릭하면 해제됩니다.`;
    } else {
      note.style.display = "none";
    }
    // 업종 필터는 한국 종목 기준이라, 시장 탭도 자연스럽게 한국으로 맞춘다.
    view = "all"; mkt = "KR";
    document.querySelectorAll('.seg[aria-label="구분"] button').forEach(b=>b.setAttribute("aria-pressed", b.dataset.view==="all"?"true":"false"));
    document.querySelectorAll('.seg[aria-label="시장"] button').forEach(b=>b.setAttribute("aria-pressed", b.dataset.mkt==="KR"?"true":"false"));
    renderTreemap();
    renderSectorTable();
    render();
  }

  document.querySelectorAll('.seg[aria-label="업종 강도 보기"] button').forEach(btn=>{
    btn.addEventListener("click", ()=>{
      sectorView = btn.dataset.sectorview;
      document.querySelectorAll('.seg[aria-label="업종 강도 보기"] button')
        .forEach(b=>b.setAttribute("aria-pressed", b===btn ? "true" : "false"));
      document.getElementById("sectorTreemapView").style.display = sectorView==="treemap" ? "" : "none";
      document.getElementById("sectorTableView").style.display = sectorView==="table" ? "" : "none";
    });
  });

  document.getElementById("sectorCollapseBtn").addEventListener("click", (e)=>{
    const body = document.getElementById("sectorCollapseBody");
    const collapsed = body.classList.toggle("collapsed");
    e.target.textContent = collapsed ? "펼치기" : "닫기";
  });

  renderTreemap();
  renderSectorTable();
})();

// ── 실행 기록 팝업 ────────────────────────────────────────
(function(){
  const btn = document.getElementById("runlogBtn");
  const overlay = document.getElementById("runlogOverlay");
  const closeBtn = document.getElementById("runlogClose");
  if(!btn || !overlay) return;
  const open = () => { overlay.hidden = false; };
  const close = () => { overlay.hidden = true; };
  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", e => { if(e.target === overlay) close(); });
  document.addEventListener("keydown", e => { if(e.key === "Escape" && !overlay.hidden) close(); });
})();

// ── 작업 로그 팝업 ────────────────────────────────────────
(function(){
  const btn = document.getElementById("changelogBtn");
  const overlay = document.getElementById("changelogOverlay");
  const closeBtn = document.getElementById("changelogClose");
  if(!btn || !overlay) return;
  const open = () => { overlay.hidden = false; };
  const close = () => { overlay.hidden = true; };
  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", e => { if(e.target === overlay) close(); });
  document.addEventListener("keydown", e => { if(e.key === "Escape" && !overlay.hidden) close(); });
})();

// ── 필터링 기준 팝업 ──────────────────────────────────────
(function(){
  const btn = document.getElementById("filterInfoBtn");
  const overlay = document.getElementById("filterInfoOverlay");
  const closeBtn = document.getElementById("filterInfoClose");
  const pdfBtn = document.getElementById("filterInfoPdf");
  if(!btn || !overlay) return;
  const open = () => { overlay.hidden = false; };
  const close = () => { overlay.hidden = true; };
  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", e => { if(e.target === overlay) close(); });
  document.addEventListener("keydown", e => { if(e.key === "Escape" && !overlay.hidden) close(); });

  // PDF 저장: 별도 라이브러리 없이 브라우저 인쇄 기능을 이용한다.
  // (이 대시보드는 인터넷 없이도 열리는 로컬 파일이라, 외부 PDF
  // 라이브러리를 CDN에서 받아오는 방식은 오프라인일 때 깨질 수 있다.)
  // 인쇄 시 body에 클래스를 달아, @media print 규칙이 팝업 내용만
  // 남기고 나머지 화면 전체를 숨기게 한다. 인쇄창에서 "PDF로 저장"을
  // 고르면 그대로 PDF 파일이 된다.
  //
  // [2026-09-14] 용지 방향(@page)은 CSS에서 body 클래스로 조건부 지정이
  // 안 된다 — 문서 전체에서 마지막에 선언된 @page 하나로 통일되기 때문에,
  // 다른 인쇄 대상(Watchlist 표 등)과 설정이 꼬일 수 있다. 인쇄하는
  // 순간에만 <style> 태그를 추가해 세로(A4 portrait)로 확실히 덮어쓰고,
  // 끝나면 없앤다.
  if(pdfBtn){
    pdfBtn.addEventListener("click", () => {
      const portraitStyle = document.createElement("style");
      portraitStyle.id = "pdfPortraitOverride";
      portraitStyle.textContent = "@media print{ @page{ size:A4 portrait; margin:18mm 15mm; } }";
      document.head.appendChild(portraitStyle);
      document.body.classList.add("printing-filter-info");
      window.print();
      document.body.classList.remove("printing-filter-info");
      portraitStyle.remove();
    });
  }
})();

// ── 관심종목 동기화 (복사 / 붙여넣기) ────────────────────
(function(){
  const codeEl=document.getElementById("favCode");
  const msgEl=document.getElementById("favMsg");
  let timer=null;
  function flash(t){
    msgEl.textContent=t;
    clearTimeout(timer);
    timer=setTimeout(()=>msgEl.textContent="", 2600);
  }
  function refreshCode(){ codeEl.value=[...FAVS].join(","); }

  document.getElementById("favCopy").addEventListener("click",async()=>{
    refreshCode();
    if(!codeEl.value){ flash("관심종목이 없습니다."); return; }
    try{
      await navigator.clipboard.writeText(codeEl.value);
      flash("복사했습니다.");
    }catch(_e){
      codeEl.select();          // 클립보드 접근이 막힌 환경 대비
      flash("직접 복사해 주세요 (Cmd/Ctrl+C).");
    }
  });

  document.getElementById("favApply").addEventListener("click",()=>{
    const parts=codeEl.value.split(",").map(x=>x.trim()).filter(Boolean);
    const valid=parts.filter(x=>/^(KR|US):.+$/.test(x));
    if(!valid.length){ flash("올바른 코드가 아닙니다."); return; }
    FAVS=new Set(valid);
    saveFavs(FAVS);
    render();
    flash(`${valid.length}개 적용했습니다.`);
  });

  document.getElementById("favClear").addEventListener("click",()=>{
    if(!FAVS.size){ flash("이미 비어 있습니다."); return; }
    FAVS=new Set(); saveFavs(FAVS); refreshCode(); render();
    flash("전체 해제했습니다.");
  });

  const _r=render;
  render=function(){ _r(); if(!document.getElementById("favSync").hidden) refreshCode(); };
})();

document.getElementById("rs").addEventListener("input",e=>{
  minRS=+e.target.value;
  document.getElementById("rsv").textContent=minRS;
  render();
});
document.getElementById("q").addEventListener("input",e=>{q=e.target.value;render();});
document.getElementById("vcpFilter").addEventListener("change",e=>{vcpFilter=e.target.value;render();});
document.getElementById("devDaysFilter").addEventListener("change",e=>{devDaysFilter=e.target.value;render();});
document.getElementById("hideOverheated").addEventListener("change",e=>{hideOverheated=e.target.checked;render();});

// PDF 저장: 브라우저 인쇄 대화상자에서 "PDF로 저장" 선택
document.getElementById("pdf").addEventListener("click",()=>window.print());

// ── Excel 내보내기 ────────────────────────────────────────
// [2026-09-15] SheetJS 등 외부 라이브러리를 CDN에서 받아오지 않는다
// (PDF 버튼과 같은 이유 — 오프라인이면 깨진다). 대신 Excel이 예전부터
// 인식해온 "HTML 표를 .xls 확장자로 저장" 방식을 쓴다. 실제로는 HTML
// 파일이지만 Excel이 열 때 표로 인식해서 정상적인 스프레드시트가 된다.
//
// 화면에 보이는 배지·툴팁·막대그래프 HTML을 그대로 뽑지 않고, 각 컬럼의
// 실제 값만 깔끔한 텍스트로 다시 구성한다 — 화면용 마크업은 Excel에서
// 안 열리거나 지저분하게 들어간다.
function excelRowValues(d){
  const vcpText = d.vcp==null ? "" : (VCP_LABEL[d.vcp] || d.vcp);
  const volRatioText = d.volRatio==null ? "" :
    `${d.volRatio}배 (${VOL_RATIO_LABEL[d.volRatioLabel]||""})`;
  const devDaysText = d.devDays==null ? "" : `${d.devDays}일 ${d.devTrend||""}`;
  const turnoverRatioText = d.turnoverRatio==null ? "" : ` (회전율 ${d.turnoverRatio.toFixed(1)}%)`;
  return [
    d.name && d.name!==d.ticker ? `${d.name}(${d.ticker})` : d.ticker,
    d.price ?? "",
    d.rs ?? "",
    d.high==null ? "" : `${d.high}%`,
    d.low==null ? "" : d.low,
    d.ma50==null ? "" : `${d.ma50}%`,
    devDaysText,
    d.slope ?? "",
    fmtMoney(d.turnover, d.market) + turnoverRatioText,
    fmtCap(d.cap, d.market),
    d.pivot==null ? "" : `${d.pivot}%`,
    d.legs==null ? "" : `${d.legs}개`,
    d.risk==null ? "" : `${d.risk}%`,
    vcpText,
    volRatioText,
  ];
}
function exportExcel(){
  const data = filtered();
  const headers = ["종목", "현재가", "RS", "52주 고점 대비", "저점대비%", "50일선 이격%",
    "이격일수", "200일선 기울기%", "거래대금", "시가총액", "피벗대비(%)", "수축",
    "손절리스크%", "VCP", "거래량비율"];

  let html = "\ufeff";  // UTF-8 BOM — 없으면 Excel이 한글을 깨서 연다
  html += '<html xmlns:o="urn:schemas-microsoft-com:office:office" '
        + 'xmlns:x="urn:schemas-microsoft-com:office:excel" '
        + 'xmlns="http://www.w3.org/TR/REC-html40">';
  html += '<head><meta charset="UTF-8"></head><body><table border="1">';
  html += "<tr>" + headers.map(h=>`<th>${h}</th>`).join("") + "</tr>";
  for(const d of data){
    const cells = excelRowValues(d);
    html += "<tr>" + cells.map(c=>`<td>${esc(String(c))}</td>`).join("") + "</tr>";
  }
  html += "</table></body></html>";

  const blob = new Blob([html], {type: "application/vnd.ms-excel;charset=utf-8"});
  const viewLabel = {pass:"통과", near:"관찰", all:"전체", fav:"관심"}[view] || view;
  const dateTag = (CURRENT.match(/(\\d{8}(_[A-Z]+)?)/) || [""])[0];
  const fname = `SEPA_${mkt}_${viewLabel}_${dateTag}.xls`.replace(/\\s+/g, "");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}
document.getElementById("excelBtn").addEventListener("click", exportExcel);

// 날짜·세션 선택: 실제 데이터가 있는 조합만 이동, 없으면 안내만 하고 되돌림
(async function(){
  const input = document.getElementById("day");
  const msg = document.getElementById("dayMsg");
  const seg = document.getElementById("sessSeg");

  // 페이지에 박아넣은 DAYS는 '이 페이지를 만든 순간'의 목록이라 오래된 페이지일수록
  // 낡아 있다. manifest.json은 만들 때마다 매번 새로 덮어써지는 별도 파일이라
  // 항상 최신이므로, 열릴 때 다시 읽어와서 있으면 그걸 우선 쓴다.
  // file:// 로 로컬에서 더블클릭해 열면 브라우저 보안 정책상 fetch가 막히는 게
  // 정상이라, 그런 경우엔 조용히 내장된 DAYS로 대체한다.
  let days = DAYS;
  try {
    const res = await fetch("manifest.json", {cache: "no-store"});
    if(res.ok){
      const fresh = await res.json();
      if(Array.isArray(fresh) && fresh.length) days = fresh;
    }
  } catch(_e) { /* 로컬 file:// 등 — 내장 DAYS로 대체 */ }

  if(!days.length){ input.parentElement.style.display="none"; seg.style.display="none"; return; }

  // key(YYYYMMDD) + session -> file 맵
  const byKeySess = {};      // "20260828_PM" -> file
  const sessionsByKey = {};  // "20260828" -> Set(["AM","PM"])
  let minKey = days[0].key, maxKey = days[0].key;
  let curKey = null, curSess = "PM";

  for(const d of days){
    byKeySess[`${d.key}_${d.session}`] = d.file;
    (sessionsByKey[d.key] ??= new Set()).add(d.session);
    if(d.key < minKey) minKey = d.key;
    if(d.key > maxKey) maxKey = d.key;
    if(d.file === CURRENT){ curKey = d.key; curSess = d.session; }
  }
  if(!curKey) curKey = maxKey;

  const toISO = k => `${k.slice(0,4)}-${k.slice(4,6)}-${k.slice(6,8)}`;
  const toKey = iso => iso.replaceAll("-", "");

  input.value = toISO(curKey);
  input.min = toISO(minKey);
  input.max = toISO(maxKey);

  let hideTimer = null;
  function flash(text){
    msg.textContent = text;
    msg.classList.add("show");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(()=>msg.classList.remove("show"), 3400);
  }

  function refreshSegState(key){
    const has = sessionsByKey[key] || new Set();
    for(const b of seg.querySelectorAll("button")){
      const s = b.dataset.sess;
      const available = has.has(s);
      b.disabled = !available;
      b.setAttribute("aria-pressed", String(s === curSess && available));
      b.title = available ? "" : "이 날짜엔 데이터가 없습니다";
    }
  }
  refreshSegState(curKey);

  function goto(key, sess){
    const file = byKeySess[`${key}_${sess}`];
    if(file){ location.href = file; return true; }
    return false;
  }

  input.addEventListener("change", e=>{
    const key = toKey(e.target.value);
    const has = sessionsByKey[key];
    if(!has || has.size===0){
      flash("해당 날짜는 데이터가 없습니다. 사용 가능 기간: " + toISO(minKey) + " ~ " + toISO(maxKey));
      input.value = toISO(curKey);
      return;
    }
    // 같은 세션(장전/장마감)이 그 날짜에도 있으면 유지, 없으면 있는 쪽으로
    if(goto(key, curSess)) return;
    const fallback = has.has("PM") ? "PM" : (has.has("AM") ? "AM" : [...has][0]);
    goto(key, fallback);
  });

  seg.addEventListener("click", e=>{
    const btn = e.target.closest("button[data-sess]");
    if(!btn || btn.disabled) return;
    goto(curKey, btn.dataset.sess);
  });
})();

// ── 매매전략 패널 ────────────────────────────────────────
function renderStrategy(){
  const openBtn = document.getElementById("stratOpen");
  if(!STRATEGY){ openBtn.style.display="none"; return; }

  const body = document.getElementById("stratBody");
  let h = `<div class="strat-h1">SEPA 매매전략 — ${STRATEGY.date}</div>
    <div class="strat-meta">생성 시각 ${STRATEGY.generated_at} · ${STRATEGY.session.label}</div>`;

  for(const sec of STRATEGY.sections){
    h += `<div class="strat-h2">${sec.title}</div><div class="strat-body"><ul>`;
    for(const line of sec.body){
      h += `<li>${highlightPt(line)}</li>`;
    }
    h += `</ul></div>`;
  }

  if(STRATEGY.table && STRATEGY.table.length){
    // 첫 화면과 같은 지표를 모두 싣는다. 이 표만 보고도 진입 판단이 서야 하고,
    // PDF로 인쇄했을 때 판단 근거가 전부 남아야 하기 때문이다.
    h += `<div class="strat-tblwrap"><table class="strat-table"><caption>통과 종목 — RS 90 이상 진입 경과 및 주요 지표</caption>
      <thead><tr>
        <th>종목</th><th style="text-align:center">시장</th>
        <th class="sr">현재가</th><th class="sr">RS</th>
        <th class="sr">52주 고점대비</th><th class="sr">저점대비%</th>
        <th class="sr">50일선 이격%</th><th class="sr">200일선 기울기%</th>
        <th class="sr">거래대금</th><th class="sr">시가총액</th>
        <th>경과</th>
      </tr></thead><tbody>`;
    const ordered = [...STRATEGY.table].sort((a,b)=>{
      // 미국 먼저, 한국 나중. 같은 시장 안에서는 RS 높은 순.
      if(a.market!==b.market) return a.market==="US" ? -1 : 1;
      return (b.rs??-1) - (a.rs??-1);
    });
    for(const r of ordered){
      const capUnit = r.market==="US" ? "B$" : "천억";
      h += `<tr><td><strong>${r.name}</strong> <span style="color:#999;font-size:8.5pt">${r.ticker}</span></td>
        <td style="text-align:center">${r.market}</td>
        <td class="sr">${num(r.price)}</td>
        <td class="sr"><strong>${r.rs==null?'–':r.rs}</strong></td>
        <td class="sr ${sign(r.high)}">${r.high==null?'–':(r.high>0?'+':'')+r.high.toFixed(1)+'%'}</td>
        <td class="sr">${r.low==null?'–':r.low.toFixed(1)}</td>
        <td class="sr">${ext(r.ma50)}</td>
        <td class="sr">${r.slope==null?'–':r.slope.toFixed(2)}</td>
        <td class="sr">${fmtMoney(r.turnover, r.market)}</td>
        <td class="sr">${fmtCap(r.cap, r.market)}<span style="color:#aaa;font-size:8pt"> ${capUnit}</span></td>
        <td class="strat-pt">${r.elapsed}</td></tr>`;
    }
    h += `</tbody></table></div>`;
  }

  h += `<div class="strat-disclaimer">${STRATEGY.disclaimer}</div>`;
  body.innerHTML = h;
}

function highlightPt(text){
  // "-7~8%", "20~25%" 같은 핵심 수치를 오렌지로 강조 (백슬래시 없는 문자 클래스 사용)
  const re = new RegExp("(-?[0-9]+(?:[.][0-9]+)?%(?:~-?[0-9]+(?:[.][0-9]+)?%)?|[0-9]+~[0-9]+%)", "g");
  return text.replace(re, m => `<span class="strat-pt">${m}</span>`);
}

renderStrategy();
document.getElementById("stratOpen")?.addEventListener("click", ()=>{
  document.getElementById("stratOverlay").classList.add("show");
});
// 전략 팝업 PDF: 인쇄 중에는 본문을 숨기는 클래스를 붙였다가 끝나면 되돌린다
document.getElementById("stratPdf")?.addEventListener("click", ()=>{
  document.body.classList.add("printing-strat");
  const cleanup = ()=>document.body.classList.remove("printing-strat");
  window.addEventListener("afterprint", cleanup, {once:true});
  setTimeout(cleanup, 3000);   // afterprint를 안 주는 브라우저 대비
  window.print();
});

document.getElementById("stratClose")?.addEventListener("click", ()=>{
  document.getElementById("stratOverlay").classList.remove("show");
});
document.getElementById("stratOverlay")?.addEventListener("click", e=>{
  if(e.target.id==="stratOverlay") e.currentTarget.classList.remove("show");
});
document.addEventListener("keydown", e=>{
  if(e.key==="Escape") document.getElementById("stratOverlay")?.classList.remove("show");
});

render();
</script>
</body>
</html>
"""


def _actions_url() -> str:
    """
    '수동 조회' 버튼이 이동할 GitHub Actions 실행 화면 주소.
    GITHUB_REPOSITORY 는 Actions 실행 중 자동으로 채워지는 환경변수(owner/repo)다.
    로컬(맥)에서 만들 때는 이 값이 없어 버튼이 일반 안내 링크로 대체된다.
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return f"https://github.com/{repo}/actions/workflows/daily.yml"
    return "https://github.com"


def _day_list(out_dir: str, current_file: str) -> list:
    """
    같은 폴더에 있는 대시보드 파일들을 찾아 날짜+세션 목록을 만든다.
    파일명 규칙: SEPA대시보드_YYYYMMDD_SESSION.html (SESSION: AM/PM/MANUAL/HIST)
    세션 접미사가 없는 예전 파일은 MANUAL로 취급해 하위호환한다.

    한글 파일명 정규화(NFC/NFD) 문제 대응:
    맥에서 만든 파일은 자모 분리형(NFD)으로 저장되는데, 이건 리눅스가 쓰는
    결합형(NFC)과 바이트가 달라 'SEPA대시보드_*' 패턴으로 찾으면 안 걸린다.
    그래서 (1) 모든 .html을 훑고 (2) 이름을 NFC로 정규화해 비교하며
    (3) 실제 파일명이 NFD면 NFC로 바꿔놓아, 다음부터는 웹에서도 열리게 한다.
    """
    import glob
    import re

    days = []
    seen = set()

    def _entry(fname):
        m = re.search(r"(\d{8})(?:_(AM|PM|MANUAL|HIST))?\.html$", fname)
        if not m:
            return None
        key, session = m.group(1), (m.group(2) or "MANUAL")
        try:
            base_label = dt.datetime.strptime(key, "%Y%m%d").strftime("%Y-%m-%d (%a)")
        except ValueError:
            base_label = key
        sess_label = {"AM": "장전", "PM": "장마감", "MANUAL": "수동", "HIST": "소급"}[session]
        return {"file": fname, "key": key, "session": session,
               "label": f"{base_label} · {sess_label}"}

    for path in glob.glob(os.path.join(out_dir, "*.html")):
        raw = os.path.basename(path)
        nfc = unicodedata.normalize("NFC", raw)
        if not nfc.startswith("SEPA대시보드_"):
            continue

        # 실제 파일명이 NFD면 NFC로 바꿔 저장한다(웹에서 열리도록).
        if raw != nfc:
            target = os.path.join(out_dir, nfc)
            try:
                if os.path.exists(target):
                    os.remove(path)          # 이미 올바른 이름이 있으면 깨진 쪽을 버린다
                else:
                    os.rename(path, target)
                    print(f"  [정규화] 파일명 수정: {raw} → {nfc}")
            except OSError as e:
                print(f"  [경고] 파일명 정규화 실패({raw}): {e}")

        if nfc in seen:
            continue
        e = _entry(nfc)
        if e:
            seen.add(nfc)
            days.append(e)

    cur_nfc = unicodedata.normalize("NFC", current_file) if current_file else ""
    if cur_nfc and cur_nfc not in seen:
        e = _entry(cur_nfc)
        if e:
            days.append(e)

    # 최신이 위로: 날짜 내림차순, 같은 날짜면 장마감(PM)이 장전(AM)보다 위
    rank = {"PM": 2, "MANUAL": 1, "AM": 0}
    days.sort(key=lambda d: (d["key"], rank.get(d["session"], 0)), reverse=True)
    return days


def build(csv_path: str, out_path: str = None, open_browser: bool = True,
         hist_dir: str = None, generate_strategy: bool = True,
         session: str = "MANUAL", macro_snapshot: list = None,
         data_as_of: str = None, breadth_snapshot: list = None) -> str:
    """
    data_as_of: 'YYYYMMDD'. 실제 가격 데이터의 기준일(장전 스캔이면 --data-date
    로 고정한 직전 영업일). run_daily.py가 CSV 파일명을 오늘 날짜로 맞추기
    위해 사본을 만들 때, 원래 데이터 기준일이 파일명에서는 사라진다.
    이 값을 명시적으로 넘기지 않으면 파일명에서 유추한 날짜를 쓰는데,
    장전 스캔에서는 그 날짜가 실제 데이터 기준일과 하루 이상 어긋날 수 있다.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"스캔 결과 파일이 없습니다: {csv_path}\n"
            f"  먼저 python3 sepa_scanner.py --market US 를 실행하세요."
        )
    session = (session or "MANUAL").upper()
    if session not in ("AM", "PM", "MANUAL", "HIST"):
        session = "MANUAL"

    hist_dir = hist_dir or HIST_DIR
    os.makedirs(hist_dir, exist_ok=True)

    df = pd.read_csv(csv_path, index_col=0, encoding="utf-8-sig")
    df.index = [str(i).zfill(6) if str(i).isdigit() else str(i) for i in df.index]

    stamp = os.path.basename(csv_path).replace("sepa_scan_", "").replace(".csv", "")

    # VCP 미니 차트 데이터 — CSV와 같은 stamp의 짝 파일. 없으면(구버전 CSV,
    # VCP 분석 실패 등) 빈 딕셔너리로 두고 화면에서는 해당 종목 클릭 시
    # 차트 패널이 그냥 안 뜨도록 처리한다(에러 아님).
    vcp_charts = {}
    chart_json_path = os.path.join(os.path.dirname(csv_path), f"sepa_vcp_charts_{stamp}.json")
    if os.path.exists(chart_json_path):
        try:
            with open(chart_json_path, encoding="utf-8") as f:
                vcp_charts = json.load(f)
        except Exception as e:
            print(f"[대시보드] VCP 차트 데이터 로드 실패(표는 정상 표시됩니다): {e}")

    # 작업 로그 — CHANGELOG.md를 직접 관리하면(가장 최근 항목을 맨 위에
    # 추가) 대시보드의 "작업 로그" 버튼이 그 내용을 그대로 보여준다.
    # 아주 단순한 마크다운만 지원한다: '## '는 소제목, '- '는 목록,
    # 나머지 줄은 문단. 파일이 없으면 버튼 자체를 숨긴다.
    changelog_html = _render_changelog_md(
        os.path.join(os.path.dirname(csv_path), "..", "CHANGELOG.md")
    )

    # 실행 로그 — run_daily.py가 실행될 때마다(자동·수동, 성공·실패 무관)
    # run_log.jsonl에 남긴 기록을 최근 것부터 보여준다. 작업 로그(코드
    # 변경 이력)와는 다르게 이건 사람이 쓰지 않고 자동으로 쌓인다.
    runlog_html = _render_run_log(
        os.path.join(os.path.dirname(csv_path), "..", "run_log.jsonl")
    )

    try:
        scan_date = dt.datetime.strptime(stamp, "%Y%m%d").date()
    except ValueError:
        scan_date = dt.date.today()
        stamp = scan_date.strftime("%Y%m%d")
    date_str = scan_date.strftime("%Y-%m-%d")
    sess_kr = {"AM": "장전", "PM": "장마감", "MANUAL": "수동 조회", "HIST": "소급 조회"}[session]
    shown_date = scan_date.strftime("%Y년 %m월 %d일") + f" · {sess_kr}"

    # ── 실제 데이터 기준일 (표시용 날짜와는 별개) ──────────────
    # data_as_of가 주어지면 그걸 신뢰하고, 없으면 파일명에서 유추한
    # scan_date를 그대로 쓴다(기존 동작과 동일, 하위 호환).
    if data_as_of:
        try:
            basis_ref = dt.datetime.strptime(data_as_of, "%Y%m%d").date()
        except ValueError:
            basis_ref = scan_date
    else:
        basis_ref = scan_date

    # ── 상단 상태 점(●) 용 — "오늘(표시일)" 기준 실제 개장 여부 ──
    # 이건 데이터를 숨기는 데 쓰지 않는다. 장전(AM) 스캔은 표시일(오늘)과
    # 실제 데이터 기준일(직전 영업일)이 다른데, 예전엔 이 값으로 데이터를
    # 통째로 숨겨서 "토요일에 열어보면 금요일 데이터인데도 0종목"이 되는
    # 버그가 있었다. 이제 아래에서 실제 데이터 기준일을 계산해 정확한
    # 날짜를 라벨로 보여주므로, 데이터 자체를 숨길 이유가 없다.
    kr_stat = mc.kr_status(date_str)
    us_stat = mc.us_status(date_str)

    # 화면 상단에 "이 데이터가 정확히 어느 시장의 언제 종가인지"를 표시한다.
    #
    # [2026-09-14] 예전엔 "달력상 오늘이 개장일인가"만 보고 날짜를 정했다
    # (_last_open_date). 그런데 이건 실제로 그 날짜 데이터를 받았는지와는
    # 무관한 계산이다 — 오늘(9/14)은 달력상 분명한 개장일이지만, KRX가
    # 밤늦게까지 당일 종가를 확정 발행하지 않아 실제 캐시에는 9/11 데이터가
    # 마지막으로 들어있던 사례가 실제로 있었다. 그때도 화면엔 "9/14 종가"
    # 라고 잘못 표시됐다.
    #
    # market_breadth.py가 이미 "실제로 어느 날짜 가격 데이터를 썼는지"를
    # data_date로 정직하게 남겨두므로(그 모듈은 캐시의 진짜 마지막 날짜를
    # 쓰지, 달력을 보고 추측하지 않는다), 있으면 그걸 우선 신뢰한다.
    # breadth 계산 자체가 실패했을 때만(부가 정보라 실패 허용) 예전
    # 달력 기반 추정으로 폴백한다.
    def _breadth_actual_date(markets):
        for b in (breadth_snapshot or []):
            if b.get("market") in markets and b.get("data_date"):
                try:
                    return dt.datetime.strptime(str(b["data_date"]), "%Y%m%d").date()
                except ValueError:
                    continue
        return None

    kr_basis_date = _breadth_actual_date(("KR_TOTAL",)) or _last_open_date(mc.kr_status, basis_ref)
    us_basis_date = _breadth_actual_date(("SP500",)) or _last_open_date(mc.us_status, basis_ref)
    kr_basis_label, us_basis_label = _market_basis_labels(kr_basis_date, us_basis_date)

    # ── 초저가 종목 제외 (미국 $1 미만, 한국 1,000원 미만) ──────
    # 참고: sepa_scanner.py의 스캔 단계에도 더 엄격한 하한(MIN_PRICE_US=10,
    # MIN_PRICE_KR=2000)이 이미 걸려 있어 보통은 이 필터가 새로 걸러내는
    # 종목은 없다. 나중에 그 값을 낮추더라도 결과 화면만큼은 이 기준
    # 아래로는 절대 보이지 않도록 이중으로 막아두는 것이다.
    if "price" in df.columns and "market" in df.columns:
        price = pd.to_numeric(df["price"], errors="coerce")
        too_cheap = ((df["market"] == "US") & (price < 1)) | \
                   ((df["market"] == "KR") & (price < 1000))
        df = df[~too_cheap]

    n_pass = int(df["PASS"].sum()) if "PASS" in df.columns else 0
    n_near = int(((df.get("PASS") != True) & (df.get("conditions_met", 0) >= 7)).sum())

    # ── 매매전략 생성 (RS90 레지스트리는 history/ 에 누적) ──────
    # 주의: 장전(AM) 스캔의 한국 데이터는 개장 전이라 사실상 전날 종가와 같다.
    # 그날 새로 확정되는 것은 미국 종가 쪽이다. 레지스트리는 날짜 단위로만
    # 갱신하므로, 같은 날 AM/PM 두 번 갱신되어도 최초 진입일이 덮어써지지 않는다.
    strategy_json = "null"
    if generate_strategy and "PASS" in df.columns:
        try:
            registry = strat.load_registry(hist_dir)
            registry = strat.update_registry(registry, df, date_str)
            strat.save_registry(hist_dir, registry)
            result = strat.build_strategy(df, date_str, registry)
            result["session"]["run_session"] = session
            strategy_json = json.dumps(result, ensure_ascii=False)
        except Exception as e:
            print(f"[경고] 매매전략 생성 실패(대시보드는 정상 생성됨): {e}")

    def _dot(open_): return "open" if open_ else "closed"

    # [2026-09-14] "9월 14일 장마감"이라는 표지 날짜와, 실제로 이 화면이
    # 몇 시에 조회·생성됐는지는 다를 수 있다(예: 새벽 1시에 수동 실행하면
    # 그날 장이 열리기도 전인데 표지엔 "9월 14일 장마감"으로 찍힌다).
    # 이 둘을 헷갈리지 않도록, 실제 생성 시각을 화면에 따로 표시한다.
    _WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
    _now = dt.datetime.now()
    generated_at_label = f"{_now:%Y-%m-%d}({_WEEKDAY_KR[_now.weekday()]}) {_now:%H:%M} KST"

    html = (HTML
            .replace("__DATA__", json.dumps(_rows(df), ensure_ascii=False))
            .replace("__STRATEGY__", strategy_json)
            .replace("__DATE__", shown_date)
            .replace("__GENERATED_AT__", generated_at_label)
            .replace("__TOTAL__", f"{len(df):,}")
            .replace("__KRN__", f"{int((df.get('market') == 'KR').sum()):,}")
            .replace("__USN__", f"{int((df.get('market') == 'US').sum()):,}")
            .replace("__KR_BASIS__", kr_basis_label)
            .replace("__US_BASIS__", us_basis_label)
            .replace("__KR_DOT__", _dot(kr_stat["open"]))
            .replace("__US_DOT__", _dot(us_stat["open"]))
            .replace("__KR_LABEL__", kr_stat["label"])
            .replace("__US_LABEL__", us_stat["label"])
            .replace("__ACTIONS_URL__", _actions_url())
            .replace("__MACRO_SNAPSHOT__", json.dumps(macro_snapshot or [], ensure_ascii=False))
            .replace("__BREADTH_SNAPSHOT__", json.dumps(breadth_snapshot or [], ensure_ascii=False))
            .replace("__VCP_CHARTS__", json.dumps(vcp_charts, ensure_ascii=False))
            .replace("__CHANGELOG_BTN__",
                     '<button id="changelogBtn" class="log-btn">작업 로그</button>' if changelog_html else "")
            .replace("__CHANGELOG_HTML__", changelog_html or "<p>작업 로그가 없습니다.</p>")
            .replace("__RUNLOG_BTN__",
                     '<button id="runlogBtn" class="runlog-btn">실행 기록</button>' if runlog_html else "")
            .replace("__RUNLOG_HTML__", runlog_html or "<p>아직 기록된 실행이 없습니다.</p>"))

    # 최종 파일은 history/ (영구 보관) 와 output/ (당일 산출물) 양쪽에 둔다.
    # 파일명을 NFC로 강제 통일한다.
    # 맥은 한글 파일명을 자모 분리형(NFD)으로 저장하는데, 깃허브 서버(리눅스)는
    # 결합형(NFC)을 쓴다. 같은 '대시보드'라는 글자라도 두 형태는 바이트 단위로
    # 다른 문자열이라, 한쪽에서 만든 파일을 다른 쪽 방식으로 링크하면 404가 난다.
    # 어느 컴퓨터에서 실행하든 항상 NFC로 저장해 이 문제 자체가 생기지 않게 한다.
    fname = unicodedata.normalize("NFC", f"SEPA대시보드_{stamp}_{session}.html")
    hist_path = os.path.join(hist_dir, fname)
    out_path = out_path or os.path.join(OUT_DIR, fname)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    days = _day_list(hist_dir, fname)
    final_html = (html
                 .replace("__DAYS__", json.dumps(days, ensure_ascii=False))
                 .replace("__CURRENT__", fname))

    with open(hist_path, "w", encoding="utf-8") as f:
        f.write(final_html)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(final_html)

    # 날짜 목록을 별도 공유 파일에도 저장한다.
    # HTML 안에 박아넣은 __DAYS__는 '그 페이지를 만든 순간'에 고정되어 버려서,
    # 나중에 다른 날짜를 백필해도 이미 배포된 페이지는 그 사실을 모른다.
    # 화면이 열릴 때 이 파일을 다시 읽어오면 그 문제가 없어진다.
    manifest_path = os.path.join(hist_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(days, f, ensure_ascii=False)

    print(f"대시보드 생성: {out_path}")
    print(f"히스토리 저장: {hist_path}")
    print(f"매니페스트 갱신: {manifest_path}")
    if open_browser:
        webbrowser.open("file://" + os.path.abspath(out_path))
        print("브라우저에서 열었습니다.")
    return out_path


def regen_manifest(hist_dir: str = None) -> str:
    """
    스캔을 다시 하지 않고, history/ 폴더를 지금 상태 그대로 다시 훑어
    manifest.json만 새로 쓴다.

    왜 필요한가: 예전 방식은 스캔이 '시작될 때' 체크아웃해온 파일 목록만
    보고 manifest.json을 만들었다. 스캔이 오래 걸리는 동안(한국 전종목이면
    수십 분) 다른 곳(예: 로컬 맥)에서 history/ 에 파일을 올려도, 그 새 파일이
    실제로는 나중에 git이 합쳐줘서 존재하게 되지만 manifest.json 내용은
    스캔 시작 시점 기준으로 이미 굳어버려 그 존재를 모르는 문제가 있었다.
    커밋 직전에 이 함수로 한 번 더 훑으면, 그 시점에 실제로 폴더에 있는
    모든 파일을 정확히 반영한다.
    """
    hist_dir = hist_dir or HIST_DIR
    if not os.path.isdir(hist_dir):
        print(f"[안내] {hist_dir} 폴더가 없어 매니페스트를 만들지 않습니다.")
        return ""
    days = _day_list(hist_dir, current_file="")
    manifest_path = os.path.join(hist_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(days, f, ensure_ascii=False)
    print(f"매니페스트 재생성: {manifest_path} ({len(days)}개 항목)")
    return manifest_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--hist-dir", default=None)
    ap.add_argument("--session", default="MANUAL", choices=["AM", "PM", "MANUAL", "HIST"])
    ap.add_argument("--regen-manifest", action="store_true",
                    help="스캔 없이 history/ 폴더를 다시 훑어 manifest.json만 갱신")
    a = ap.parse_args()
    if a.regen_manifest:
        regen_manifest(a.hist_dir)
        raise SystemExit(0)
    csv_path = a.csv or os.path.join(OUT_DIR, f"sepa_scan_{dt.date.today():%Y%m%d}.csv")
    build(csv_path, open_browser=not a.no_open, hist_dir=a.hist_dir, session=a.session)
