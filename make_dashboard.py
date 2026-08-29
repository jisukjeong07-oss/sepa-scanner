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
}
*{box-sizing:border-box}
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
  .rail .zone{background:#eee}
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

/* 표 */
table{width:100%;border-collapse:collapse;background:var(--surface);
      border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{font-size:11px;color:var(--muted);text-align:right;padding:11px 10px;
   border-bottom:1px solid var(--line);white-space:nowrap;cursor:pointer;
   font-weight:600;letter-spacing:.04em;user-select:none}
th:first-child,td:first-child{text-align:left;padding-left:14px}
th[aria-sort]{color:var(--ink)}
td{padding:10px;text-align:right;border-bottom:1px solid #EEF1F4;white-space:nowrap}
tbody tr{cursor:pointer}
tbody tr:hover{background:#F6F8FA}
.tk{font-weight:700;letter-spacing:-.01em}
.nm{font-size:11px;color:var(--muted);display:block;font-weight:400}
.pos{color:var(--up)} .neg{color:var(--down)}
.badge{display:inline-block;font-size:10px;padding:2px 6px;border-radius:4px;
       background:#F0F2F5;color:var(--ink-2);margin-left:6px;font-weight:600}
.badge.p{background:var(--up);color:#fff}

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
  .strat-close{display:none}
}
@media (max-width:640px){
  .wrap{padding:18px 12px 60px}
  h1{font-size:21px}
  .hide-s{display:none}
  .rail{width:64px}
  .stats{gap:18px}
}
@media (prefers-reduced-motion:no-preference){
  tbody tr{transition:background .12s ease}
}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="hrow">
    <div>
      <div class="eyebrow">Minervini Trend Template · 8조건 정량 스캔</div>
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
  <div class="stat"><div class="k">화면 표시</div><div class="v num" id="shown">0</div></div>
</div>

<div class="strat-overlay" id="stratOverlay">
  <div class="strat-modal">
    <button class="strat-close" id="stratClose" aria-label="닫기">&times;</button>
    <div class="strat-pad" id="stratBody"></div>
  </div>
</div>

<div class="controls">
  <div class="seg" role="group" aria-label="구분">
    <button data-view="pass" aria-pressed="true">통과</button>
    <button data-view="near" aria-pressed="false">관찰</button>
    <button data-view="all" aria-pressed="false">전체</button>
  </div>
  <div class="seg" role="group" aria-label="시장">
    <button data-mkt="US" aria-pressed="true">미국</button>
    <button data-mkt="KR" aria-pressed="false">한국</button>
  </div>
  <label class="rs">RS <input type="range" id="rs" min="0" max="99" value="0">
    <span class="num" id="rsv">0</span> 이상</label>
  <input type="search" id="q" placeholder="종목 검색" aria-label="종목 검색">
</div>

<div id="host"></div>

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
let view="pass", mkt="US", minRS=0, q="", sortKey="cap", sortDir=-1, open=null;

const COLS=[
  ["ticker","종목",""],
  ["price","현재가",""],
  ["rs","RS",""],
  ["high","52주 고점 대비","rail"],
  ["low","저점 대비%","hide-s"],
  ["ma50","50일선%","hide-s"],
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
const num=v=>v==null?"–":v.toLocaleString(undefined,{maximumFractionDigits:2});

// 고점 근접도: -25%(왼쪽) → 0%(오른쪽, 신고가)
function rail(v){
  if(v==null) return '<div class="rail"></div>';
  const p=Math.max(0,Math.min(100,(v+25)/25*100));
  const out=v<-25?" out":"";
  return `<div class="rail" title="52주 고점 대비 ${v}%">
    <div class="zone"></div><div class="track"></div><div class="peak"></div>
    <div class="dot${out}" style="left:${p}%"></div></div>`;
}

// 시장별로 통계(통과/관찰 수)를 다시 센다 — 전체 DATA가 아니라 선택된 시장 안에서만
function updateStats(){
  const inMkt = DATA.filter(d=>d.market===mkt);
  document.getElementById("statPass").textContent = inMkt.filter(d=>d.pass).length;
  document.getElementById("statNear").textContent = inMkt.filter(d=>!d.pass && d.met>=7).length;
}

function filtered(){
  return DATA.filter(d=>{
    if(view==="pass" && !d.pass) return false;
    if(view==="near" && (d.pass || d.met<7)) return false;
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

function render(){
  updateStats();
  const rows=filtered();
  document.getElementById("shown").textContent=rows.length;
  const host=document.getElementById("host");

  if(!rows.length){
    host.innerHTML='<div class="empty">조건에 맞는 종목이 없습니다. RS 기준을 낮추거나 구분을 바꿔 보세요.</div>';
    return;
  }

  let h='<table><thead><tr>';
  for(const [k,label,cls] of COLS){
    const on = sortKey===k ? ` aria-sort="${sortDir===1?"ascending":"descending"}"` : "";
    const text = k==="cap" ? capUnitLabel(mkt) : label;
    h+=`<th class="${cls==="hide-s"?"hide-s":""}" data-k="${k}"${on}>${text}</th>`;
  }
  h+='</tr></thead><tbody>';

  for(const d of rows){
    const same=d.name===d.ticker;
    const nm = same ? "" : (d.name.length>9 ? d.name.slice(0,9)+"…" : d.name);
    h+=`<tr data-t="${d.ticker}" tabindex="0">
      <td><span class="tk">${d.ticker}</span>${d.pass?'<span class="badge p">통과</span>':`<span class="badge">${d.met}/8</span>`}
          ${same?"":`<span class="nm" title="${d.name}">${nm}</span>`}</td>
      <td class="num">${num(d.price)}</td>
      <td class="num"><strong>${d.rs==null?"–":d.rs}</strong></td>
      <td>${rail(d.high)}</td>
      <td class="num hide-s ${sign(d.low)}">${num(d.low)}</td>
      <td class="num hide-s ${sign(d.ma50)}">${num(d.ma50)}</td>
      <td class="num hide-s ${sign(d.slope)}">${num(d.slope)}</td>
      <td class="num hide-s">${fmtMoney(d.turnover,d.market)}</td>
      <td class="num hide-s">${fmtCap(d.cap,d.market)}</td></tr>`;

    if(open===d.ticker){
      const chips = d.fails.length
        ? d.fails.map(f=>`<span class="chip no">미달 · ${f}</span>`).join("")
        : '<span class="chip">8개 조건 모두 충족</span>';
      h+=`<tr class="detail"><td colspan="${COLS.length}">
        <h4>${d.ticker} · 조건 점검</h4><div class="cond">${chips}</div></td></tr>`;
    }
  }
  host.innerHTML=h+"</tbody></table>";
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
  const tr=e.target.closest("tbody tr[data-t]");
  if(tr){ open = open===tr.dataset.t ? null : tr.dataset.t; render(); }
});

document.addEventListener("keydown",e=>{
  if(e.key!=="Enter" && e.key!==" ") return;
  const tr=e.target.closest && e.target.closest("tbody tr[data-t]");
  if(tr){ e.preventDefault(); open = open===tr.dataset.t ? null : tr.dataset.t; render(); }
});

document.getElementById("rs").addEventListener("input",e=>{
  minRS=+e.target.value;
  document.getElementById("rsv").textContent=minRS;
  render();
});
document.getElementById("q").addEventListener("input",e=>{q=e.target.value;render();});

// PDF 저장: 브라우저 인쇄 대화상자에서 "PDF로 저장" 선택
document.getElementById("pdf").addEventListener("click",()=>window.print());

// 날짜·세션 선택: 실제 데이터가 있는 조합만 이동, 없으면 안내만 하고 되돌림
(function(){
  const input = document.getElementById("day");
  const msg = document.getElementById("dayMsg");
  const seg = document.getElementById("sessSeg");
  if(!DAYS.length){ input.parentElement.style.display="none"; seg.style.display="none"; return; }

  // key(YYYYMMDD) + session -> file 맵
  const byKeySess = {};      // "20260828_PM" -> file
  const sessionsByKey = {};  // "20260828" -> Set(["AM","PM"])
  let minKey = DAYS[0].key, maxKey = DAYS[0].key;
  let curKey = null, curSess = "PM";

  for(const d of DAYS){
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
    for(const r of STRATEGY.table){
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
    """
    import glob
    import re

    days = []

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

    for f in glob.glob(os.path.join(out_dir, "SEPA대시보드_*.html")):
        e = _entry(os.path.basename(f))
        if e:
            days.append(e)

    if not any(d["file"] == current_file for d in days):
        e = _entry(current_file)
        if e:
            days.append(e)

    # 최신이 위로: 날짜 내림차순, 같은 날짜면 장마감(PM)이 장전(AM)보다 위
    rank = {"PM": 2, "MANUAL": 1, "AM": 0}
    days.sort(key=lambda d: (d["key"], rank.get(d["session"], 0)), reverse=True)
    return days


def build(csv_path: str, out_path: str = None, open_browser: bool = True,
         hist_dir: str = None, generate_strategy: bool = True,
         session: str = "MANUAL") -> str:
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
            .replace("__ACTIONS_URL__", _actions_url()))

    # 최종 파일은 history/ (영구 보관) 와 output/ (당일 산출물) 양쪽에 둔다.
    fname = f"SEPA대시보드_{stamp}_{session}.html"
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

    print(f"대시보드 생성: {out_path}")
    print(f"히스토리 저장: {hist_path}")
    if open_browser:
        webbrowser.open("file://" + os.path.abspath(out_path))
        print("브라우저에서 열었습니다.")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--hist-dir", default=None)
    ap.add_argument("--session", default="MANUAL", choices=["AM", "PM", "MANUAL", "HIST"])
    a = ap.parse_args()
    csv_path = a.csv or os.path.join(OUT_DIR, f"sepa_scan_{dt.date.today():%Y%m%d}.csv")
    build(csv_path, open_browser=not a.no_open, hist_dir=a.hist_dir, session=a.session)
