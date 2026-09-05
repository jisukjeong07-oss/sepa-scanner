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

import pandas as pd

import unicodedata

import market_calendar as mc
import strategy as strat

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "output")
HIST_DIR = os.path.join(BASE, "history")   # 저장소에 커밋되어 영구 보관되는 폴더

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
            "met": int(r.get("conditions_met", 0) or 0),
            "pass": bool(r.get("PASS", False)),
            "fails": fails,
        })
    return out


def _f(v):
    try:
        f = float(v)
        return None if pd.isna(f) else round(f, 2)
    except (TypeError, ValueError):
        return None


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
.btn{border:1px solid var(--ink);background:var(--ink);color:#fff;border-radius:7px;
     padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{opacity:.88}
.btn-outline{border:1px solid var(--line);background:var(--surface);color:var(--ink-2);
     border-radius:7px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;
     font-family:inherit;text-decoration:none;display:inline-flex;align-items:center}
.btn-outline:hover{background:#F2F5F8;color:var(--ink)}
#sessSeg button:disabled{opacity:.35;cursor:not-allowed}

/* 시황 · 리스크 패널 (화면 최상단) */
.macro{display:grid;grid-template-columns:1.4fr 1fr;gap:14px;margin-bottom:20px}
@media (max-width:820px){.macro{grid-template-columns:1fr}}
.macro-h{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-bottom:8px}
.idx-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.idx-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.idx-card .nm{font-size:11.5px;color:var(--ink-2)}
.idx-card .val{font-size:19px;font-weight:800;letter-spacing:-.01em;margin-top:2px}
.idx-card .chg{font-size:12px;font-weight:700;margin-top:1px}
.idx-card .chg.pos{color:var(--up)} .idx-card .chg.neg{color:var(--down)}
.idx-card.fail{color:var(--muted)}
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
  @page{size:A4 landscape;margin:12mm}
  body{background:#fff}
  .controls,.hact,footer .noprint{display:none !important}
  .wrap{max-width:none;padding:0}
  table{border:1px solid #999}
  tbody tr{page-break-inside:avoid}
  thead{display:table-header-group}
  .hide-s{display:table-cell !important}
  .ext-warn{-webkit-print-color-adjust:exact;print-color-adjust:exact;
            background:#FBEAEA !important;color:#C0343B !important}
  .ext-caut{color:#A96A0B !important}
  /* 인쇄 시 배경색이 지워지면 막대가 통째로 사라진다 */
  .rail,.rail *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .rail .zone{background:#eee}
  .rail .dot{background:#C0343B !important;border-color:#fff !important}
  .rail .dot.out{background:#8A8F98 !important}
  .rail .track{background:#D6DAE0 !important}
  .rail .peak{background:#1A1D23 !important}
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
label.rs{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--ink-2)}
input[type=range]{width:120px;accent-color:var(--up)}
button:focus-visible,input:focus-visible,tr:focus-visible{outline:2px solid var(--up);outline-offset:2px}

/* 표 — 높이를 제한하고 내부에서만 스크롤, 헤더는 위에 고정 */
.tbl-scroll{max-height:52vh;overflow-y:auto;background:var(--surface);
  border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--surface)}
th{font-size:11px;color:var(--muted);text-align:right;padding:11px 10px;
   border-bottom:1px solid var(--line);white-space:nowrap;cursor:pointer;
   font-weight:600;letter-spacing:.04em;user-select:none;
   position:sticky;top:0;background:var(--surface);z-index:2}
th:first-child,td:first-child{text-align:left;padding-left:12px}
th[aria-sort]{color:var(--ink)}
td{padding:10px;text-align:right;border-bottom:1px solid #EEF1F4;white-space:nowrap}
tbody tr{cursor:pointer}
tbody tr:hover{background:#F6F8FA}
tbody tr.sel{background:#EEF3F8}
.tk{font-weight:700;letter-spacing:-.01em}
.nm{font-size:11px;color:var(--muted);display:block;font-weight:400}
.pos{color:var(--up)} .neg{color:var(--down)}
.badge{display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px;
       background:#F0F2F5;color:var(--ink-2);margin-left:6px;font-weight:600;cursor:help}
.badge.p{background:var(--up);color:#fff;cursor:default}

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
.railwrap{display:flex;align-items:center;justify-content:flex-end;gap:9px}
.railnum{font-variant-numeric:tabular-nums;font-size:12.5px;font-weight:600;
         white-space:nowrap;min-width:52px;text-align:right}
.railnum.near{color:var(--up)}          /* 고점 3% 이내 = 돌파 임박 */
.railnum.out{color:var(--muted)}        /* 조건7 미달 */

/* 시그니처: 52주 고점 근접도 트랙 (조건7 = 고점 -25% 이내) */
.rail{position:relative;width:96px;height:16px;margin-left:auto}
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

/* 시장 상태 배너 */
.mkt-status{display:flex;gap:18px;flex-wrap:wrap;align-items:center;
  background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:10px 14px;margin-bottom:14px;font-size:12.5px;color:var(--ink-2)}
.mkt-status .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.mkt-status .dot.open{background:#2f8f4e}
.mkt-status .dot.closed{background:var(--muted)}
.mkt-status b{color:var(--ink);font-weight:700}

/* 매매전략 진입 버튼 */
.strat-open{border:1px solid var(--navy,#1a2b4c);background:transparent;color:#1a2b4c;
  border-radius:6px;padding:4px 11px;font-size:12px;font-weight:700;cursor:pointer;
  font-family:inherit;margin-left:10px;vertical-align:middle}
.strat-open:hover{background:#1a2b4c;color:#fff}

/* 매매전략 모달 */
.strat-overlay{display:none;position:fixed;inset:0;background:rgba(15,20,26,.55);
  z-index:50;align-items:flex-start;justify-content:center;padding:26px 14px;overflow-y:auto}
.strat-overlay.show{display:flex}
.strat-modal{background:#fff;border-radius:12px;max-width:760px;width:100%;
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
.strat-disclaimer{margin-top:20px;padding-top:12px;border-top:1px solid #e5e5e5;
  font-size:8.5pt;color:#888;line-height:1.6}
@media print{
  .strat-overlay{position:static;background:none;padding:0}
  .strat-modal{box-shadow:none;max-width:none}
  .strat-close,.strat-pdf{display:none}
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
    <div class="macro-h">시황 개요</div>
    <div id="idxCards" class="idx-grid"></div>
  </div>
  <div class="macro-risk">
    <div class="macro-h">리스크 신호</div>
    <div id="riskCards" class="risk-grid"></div>
  </div>
</section>

<header>
  <div class="hrow">
    <div>
      <h1>SEPA 종목 후보</h1>
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
      <button id="pdf" class="btn">PDF로 저장</button>
    </div>
  </div>
</header>

<div class="mkt-status">
  <span><span class="dot __KR_DOT__"></span>한국 <b>__KR_LABEL__</b></span>
  <span><span class="dot __US_DOT__"></span>미국 <b>__US_LABEL__</b></span>
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
  <input type="search" id="q" placeholder="종목 검색" aria-label="종목 검색">
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
      <span class="chart-hint">차트 안에서 기간·주봉 변경 가능</span></div>
    <button id="chartClose" class="btn-sm">닫기</button>
  </div>
  <div id="chartBox" class="chart-box"></div>
</section>

<footer>
행을 누르면 8개 조건 중 무엇이 미달인지 볼 수 있습니다.
RS는 IBD 공식 지표가 아니라 3·6·9·12개월 가중수익률을 유니버스 안에서 백분위로 환산한 근사값입니다.
이 화면은 SEPA 1단계(기술적 필터)만 담고 있어, 2단계 펀더멘털과 3단계 진입 시점은 직접 확인해야 합니다.
투자 참고 자료이며 투자 권유가 아닙니다.
</footer>
</div>

<script>
const DATA = __DATA__;
const DAYS = __DAYS__;      // 같은 폴더에 있는 다른 날짜 대시보드 목록
const CURRENT = "__CURRENT__";
const STRATEGY = __STRATEGY__;   // 매매전략 (null이면 버튼 숨김)
const INDEX_SNAPSHOT = __INDEX_SNAPSHOT__;
const RISK_SIGNALS = __RISK_SIGNALS__;

// ── 시황 · 리스크 패널 렌더 ──────────────────────────────
(function(){
  const idxHost = document.getElementById("idxCards");
  const riskHost = document.getElementById("riskCards");
  if(!idxHost || !riskHost) return;

  let ih = "";
  for(const c of (INDEX_SNAPSHOT||[])){
    if(!c.ok){
      ih += `<div class="idx-card fail"><div class="nm">${c.name}</div><div class="val">–</div></div>`;
      continue;
    }
    const cls = c.change_pct>0?"pos":(c.change_pct<0?"neg":"");
    const sign = c.change_pct>0?"+":"";
    ih += `<div class="idx-card"><div class="nm">${c.name}</div>
      <div class="val num">${c.value.toLocaleString()}</div>
      <div class="chg num ${cls}">${sign}${c.change_pct}%</div></div>`;
  }
  idxHost.innerHTML = ih || '<div class="idx-card fail">지수 정보 없음</div>';

  let rh = "";
  for(const r of (RISK_SIGNALS||[])){
    if(!r.ok){
      rh += `<div class="risk-card fail"><div class="nm">${r.name}</div>
        <div class="val">조회 실패</div>
        ${r.detail?`<div class="asof">${r.detail}</div>`:""}</div>`;
      continue;
    }
    const warn = (r.name==="VIX" && r.value>=30) ||
                (r.name.includes("Fear") && r.value<=25) ||
                (r.name.includes("BofA") && r.value<=4.0);
    rh += `<div class="risk-card${warn?" warn":""}"><div class="nm">${r.name}</div>
      <div class="val num">${r.value}${r.name.includes("BofA")?"%":""}</div>
      <div class="lbl">${r.label}</div>
      <div class="asof">기준: ${r.as_of||"–"}</div></div>`;
  }
  riskHost.innerHTML = rh || '<div class="risk-card fail">리스크 신호 없음</div>';
})();
let view="pass", mkt="US", minRS=0, q="", sortKey="cap", sortDir=-1, open=null;

const COLS=[
  ["ticker","종목",""],
  ["price","현재가",""],
  ["rs","RS",""],
  ["high","52주 고점 대비","rail"],
  ["low","저점 대비%","hide-s"],
  ["ma50","50일선 이격%","hide-s"],
  ["slope","200일선 기울기%","hide-s"],
  ["turnover","거래대금","hide-s"],
  ["cap","cap-caption","hide-s"],   // 라벨은 시장에 따라 렌더 시점에 동적으로 붙인다
];

function capUnitLabel(m){ return m==="US" ? "시가총액(1B)" : "시가총액(1천억원)"; }

const fmtMoney=(v,m)=>{
  if(v==null) return "–";
  return m==="KR" ? (v/1e8).toLocaleString(undefined,{maximumFractionDigits:0})+"억"
                  : "$"+(v/1e6).toLocaleString(undefined,{maximumFractionDigits:0})+"M";
};
function fmtCap(v, m){
  if(v==null || isNaN(v)) return "–";
  if(m==="US"){
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

  let h='<div class="tbl-scroll"><table><thead><tr>';
  for(const [k,label,cls] of COLS){
    const on = sortKey===k ? ` aria-sort="${sortDir===1?"ascending":"descending"}"` : "";
    const text = k==="cap" ? capUnitLabel(mkt) : label;
    h+=`<th class="${cls==="hide-s"?"hide-s":""}" data-k="${k}"${on}>${text}</th>`;
  }
  h+='</tr></thead><tbody>';

  for(const d of rows){
    const same=d.name===d.ticker;
    const nm = same ? "" : (d.name.length>9 ? d.name.slice(0,9)+"…" : d.name);
    const on = FAVS.has(favKey(d));
    // 미달 조건은 클릭이 아니라 마우스오버(모바일은 배지 탭)로 보여준다
    const tipBody = d.fails.length
      ? `<b>미달 ${d.fails.length}개</b>${d.fails.map(f=>"· "+f).join("<br>")}`
      : `<b>8개 조건 모두 충족</b>`;
    const badge = d.pass
      ? '<span class="badge p">통과</span>'
      : `<span class="badge" data-tip="1">${d.met}/8</span>`;

    h+=`<tr data-t="${d.ticker}" data-m="${d.market}" tabindex="0"${open===d.ticker?' class="sel"':''}>
      <td><span class="star${on?" on":""}" data-fav="${esc(favKey(d))}"
            title="${on?"관심종목에서 제거":"관심종목에 추가"}">${on?"★":"☆"}</span
        ><span class="cellwrap"><span class="tk">${d.ticker}</span>${badge}
          ${same?"":`<span class="nm" title="${esc(d.name)}">${nm}</span>`}
          <span class="tip">${tipBody}</span></span></td>
      <td class="num">${num(d.price)}</td>
      <td class="num"><strong>${d.rs==null?"–":d.rs}</strong></td>
      <td>${rail(d.high)}</td>
      <td class="num hide-s ${sign(d.low)}">${num(d.low)}</td>
      <td class="num hide-s">${ext(d.ma50)}</td>
      <td class="num hide-s ${sign(d.slope)}">${num(d.slope)}</td>
      <td class="num hide-s">${fmtMoney(d.turnover,d.market)}</td>
      <td class="num hide-s">${fmtCap(d.cap,d.market)}</td></tr>`;
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
    const t=tr.dataset.t;
    if(open===t){ open=null; hideChart(); }
    else { open=t; showChart(t, tr.dataset.m); }
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
    const t=tr.dataset.t;
    if(open===t){ open=null; hideChart(); }
    else { open=t; showChart(t, tr.dataset.m); }
    render();
  }
});

// ── TradingView 차트 ────────────────────────────────────
// 한국은 KRX:종목코드, 미국은 티커 그대로 쓴다.
// 위젯 자체에 기간·봉 전환 도구가 들어 있어 따로 만들지 않는다.
function tvSymbol(ticker, market){
  return market==="KR" ? `KRX:${ticker}` : ticker;
}
function showChart(ticker, market){
  const wrap=document.getElementById("chartWrap");
  const box=document.getElementById("chartBox");
  const sym=tvSymbol(ticker, market);
  const row=DATA.find(d=>d.ticker===ticker && d.market===market);
  document.getElementById("chartTitle").textContent =
    (row && row.name!==row.ticker) ? `${row.name} (${ticker})` : ticker;

  // range=12M(약 250거래일) + 일봉 캔들이 기본. 사용자가 차트 안에서 바꿀 수 있다.
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
document.getElementById("chartClose").addEventListener("click",()=>{
  open=null; hideChart(); render();
});

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

// PDF 저장: 브라우저 인쇄 대화상자에서 "PDF로 저장" 선택
document.getElementById("pdf").addEventListener("click",()=>window.print());

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
    h += `<table class="strat-table"><caption>RS 90 이상 진입 경과 — 통과 종목</caption>
      <thead><tr><th>종목</th><th>시장</th><th>RS</th><th>52W고점대비%</th><th>경과</th></tr></thead><tbody>`;
    const ordered = [...STRATEGY.table].sort((a,b)=>{
      // 미국 먼저, 한국 나중. 같은 시장 안에서는 RS 높은 순.
      if(a.market!==b.market) return a.market==="US" ? -1 : 1;
      return (b.rs??-1) - (a.rs??-1);
    });
    for(const r of ordered){
      h += `<tr><td><strong>${r.name}</strong> <span style="color:#999;font-size:8.5pt">${r.ticker}</span></td>
        <td>${r.market}</td><td>${r.rs==null?'–':r.rs}</td>
        <td>${r.high==null?'–':r.high}</td>
        <td class="strat-pt">${r.elapsed}</td></tr>`;
    }
    h += `</tbody></table>`;
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
         session: str = "MANUAL", index_snapshot: list = None,
         risk_signals: list = None) -> str:
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
    try:
        scan_date = dt.datetime.strptime(stamp, "%Y%m%d").date()
    except ValueError:
        scan_date = dt.date.today()
        stamp = scan_date.strftime("%Y%m%d")
    date_str = scan_date.strftime("%Y-%m-%d")
    sess_kr = {"AM": "장전", "PM": "장마감", "MANUAL": "수동 조회", "HIST": "소급 조회"}[session]
    shown_date = scan_date.strftime("%Y년 %m월 %d일") + f" · {sess_kr}"

    # ── 휴장일이면 해당 시장 데이터를 화면에서 숨긴다 ──────────
    kr_stat = mc.kr_status(date_str)
    us_stat = mc.us_status(date_str)
    if not kr_stat["open"] and "market" in df.columns:
        df = df[df["market"] != "KR"]
    if not us_stat["open"] and "market" in df.columns:
        df = df[df["market"] != "US"]

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

    html = (HTML
            .replace("__DATA__", json.dumps(_rows(df), ensure_ascii=False))
            .replace("__STRATEGY__", strategy_json)
            .replace("__DATE__", shown_date)
            .replace("__TOTAL__", f"{len(df):,}")
            .replace("__KRN__", f"{int((df.get('market') == 'KR').sum()):,}")
            .replace("__USN__", f"{int((df.get('market') == 'US').sum()):,}")
            .replace("__KR_DOT__", _dot(kr_stat["open"]))
            .replace("__US_DOT__", _dot(us_stat["open"]))
            .replace("__KR_LABEL__", kr_stat["label"])
            .replace("__US_LABEL__", us_stat["label"])
            .replace("__ACTIONS_URL__", _actions_url())
            .replace("__INDEX_SNAPSHOT__", json.dumps(index_snapshot or [], ensure_ascii=False))
            .replace("__RISK_SIGNALS__", json.dumps(risk_signals or [], ensure_ascii=False)))

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
