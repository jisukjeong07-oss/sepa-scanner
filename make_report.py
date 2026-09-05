# -*- coding: utf-8 -*-
"""
스캔 결과(CSV) -> 일일 SEPA 리포트 PDF 생성

사용법:
  python make_report.py                       # 오늘자 스캔 결과로 생성
  python make_report.py --csv output/sepa_scan_20260820.csv
"""

import os
import argparse
import datetime as dt

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from reportlab.pdfbase.ttfonts import TTFont

# ── 한글 폰트 등록 ────────────────────────────────────────────
# CID 폰트(HYGothic 등)는 글꼴을 PDF에 넣지 않고 '뷰어가 가진 한글 폰트'를
# 참조한다. 그래서 한글 폰트팩이 없는 뷰어(모바일 앱, 브라우저 내장 뷰어 등)
# 에서는 글자가 통째로 안 보인다.
# 실제 TTF를 임베딩하면 어떤 기기에서 열어도 동일하게 보인다.
_FONT_CANDIDATES = [
    # (일반, 굵게) — 앞에서부터 시도
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", None),          # macOS
    ("/Library/Fonts/NanumGothic.ttf", "/Library/Fonts/NanumGothicBold.ttf"),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),               # Linux
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),      # Windows
]


def _register_korean_font():
    for regular, bold in _FONT_CANDIDATES:
        if not os.path.exists(regular):
            continue
        try:
            pdfmetrics.registerFont(TTFont("KRSans", regular))
            if bold and os.path.exists(bold):
                pdfmetrics.registerFont(TTFont("KRSans-Bold", bold))
                bold_name = "KRSans-Bold"
            else:
                bold_name = "KRSans"      # 굵은 폰트가 없으면 같은 글꼴 사용
            pdfmetrics.registerFontFamily("KRSans", normal="KRSans", bold=bold_name,
                                          italic="KRSans", boldItalic=bold_name)
            return bold_name, "KRSans"
        except Exception:
            continue

    # 임베딩 가능한 폰트를 못 찾으면 CID 폰트로 후퇴한다.
    # 이 경우 일부 뷰어에서 한글이 보이지 않을 수 있음을 알린다.
    print("[경고] 임베딩 가능한 한글 폰트를 찾지 못했습니다. "
          "일부 PDF 뷰어(특히 모바일)에서 한글이 보이지 않을 수 있습니다.")
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    return "HYGothic-Medium", "HYSMyeongJo-Medium"


FB, FR = _register_korean_font()

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "output")

_s = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=_s["Title"], fontName=FB, fontSize=19,
                            leading=24, textColor=colors.HexColor("#1a2b4c")),
    "sub": ParagraphStyle("s", parent=_s["Normal"], fontName=FR, fontSize=9.5,
                          leading=13, textColor=colors.HexColor("#555555")),
    "h1": ParagraphStyle("h1", parent=_s["Heading1"], fontName=FB, fontSize=13,
                         leading=17, spaceBefore=14, spaceAfter=7,
                         textColor=colors.HexColor("#1a2b4c")),
    "body": ParagraphStyle("b", parent=_s["Normal"], fontName=FR, fontSize=9.2,
                           leading=13.5, spaceAfter=5),
    "small": ParagraphStyle("sm", parent=_s["Normal"], fontName=FR, fontSize=7.8,
                            leading=11, textColor=colors.HexColor("#666666")),
    "cell": ParagraphStyle("c", parent=_s["Normal"], fontName=FR, fontSize=7.5, leading=10),
    "cellb": ParagraphStyle("cb", parent=_s["Normal"], fontName=FB, fontSize=7.5, leading=10),
    "head": ParagraphStyle("hd", parent=_s["Normal"], fontName=FB, fontSize=7.6,
                           leading=10, textColor=colors.white),
}

# 50일선 이격 경고 임계값 (대시보드와 동일 기준)
# 미네르비니는 50일선에서 크게 벌어진 종목의 신규 진입을 금한다.
EXT_WARN = 25.0   # 이 이상이면 과열 — 신규 진입 부적합
EXT_CAUT = 15.0   # 이 이상이면 주의

COLS = [
    ("name", "종목명", 30),
    ("price", "현재가", 20),
    ("RS", "RS", 12),
    ("vs_52w_high_%", "52W고점\n대비%", 20),
    ("vs_52w_low_%", "52W저점\n대비%", 20),
    ("vs_MA50_%", "50일선\n이격%", 20),
    ("MA200_slope_%", "200일선\n1개월기울기%", 26),
    ("avg_turnover_20d", "20일평균\n거래대금", 26),
]


def _table(df: pd.DataFrame) -> Table:
    header = [Paragraph(h, S["head"]) for _, h, _ in COLS]
    rows = [header]
    for idx, r in df.iterrows():
        row = []
        for key, _, _ in COLS:
            v = r.get(key, "")
            if key == "name":
                # 미국 종목은 name과 티커가 같아 그대로 두면 두 번 표시된다.
                if str(v).strip() == str(idx).strip():
                    v = str(v)
                else:
                    v = f"{v}<br/><font size=6 color='#888888'>{idx}</font>"
                row.append(Paragraph(str(v), S["cellb"]))
            elif key == "avg_turnover_20d":
                try:
                    v = f"{float(v)/1e8:,.0f}억" if r["market"] == "KR" else f"${float(v)/1e6:,.0f}M"
                except Exception:
                    v = "-"
                row.append(Paragraph(str(v), S["cell"]))
            elif key == "vs_MA50_%":
                # 50일선에서 크게 벌어진 종목은 손절선이 기술적 의미를 잃는다.
                # 표에서 한눈에 걸러낼 수 있도록 색과 기호로 표시한다.
                try:
                    f = float(v)
                    if f >= EXT_WARN:
                        v = f"<font color='#C0343B'><b>{f:,.1f} !</b></font>"
                    elif f >= EXT_CAUT:
                        v = f"<font color='#A96A0B'>{f:,.1f}</font>"
                    else:
                        v = f"{f:,.1f}"
                except (TypeError, ValueError):
                    v = "-"
                row.append(Paragraph(str(v), S["cell"]))
            else:
                row.append(Paragraph(str(v), S["cell"]))
        rows.append(row)

    # splitByRow: 행 단위로 쪼개고, repeatRows=1 로 다음 장에도 헤더를 반복한다.
    t = Table(rows, colWidths=[w * mm for _, _, w in COLS],
              repeatRows=1, splitByRow=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2b4c")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5fa")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return t


F_COLS = [
    ("name", "종목명", 28),
    ("RS", "RS", 12),
    ("최근분기", "최근\n분기", 16),
    ("순이익증가율_YoY_%", "순이익\nYoY%", 22),
    ("매출증가율_YoY_%", "매출\nYoY%", 22),
    ("직전분기_순이익증가율_%", "직전분기\n순이익YoY%", 26),
    ("이익가속", "이익\n가속", 16),
]


def _ftable(df: pd.DataFrame) -> Table:
    rows = [[Paragraph(h, S["head"]) for _, h, _ in F_COLS]]
    for idx, r in df.iterrows():
        row = []
        for key, _, _ in F_COLS:
            v = r.get(key, "")
            if key == "name":
                # 미국 종목은 name과 티커가 같아 그대로 두면 두 번 표시된다.
                if str(v).strip() == str(idx).strip():
                    v = str(v)
                else:
                    v = f"{v}<br/><font size=6 color='#888888'>{idx}</font>"
                row.append(Paragraph(str(v), S["cellb"]))
            elif key == "이익가속":
                row.append(Paragraph("O" if v is True or str(v) == "True" else "-", S["cell"]))
            else:
                row.append(Paragraph("-" if pd.isna(v) else str(v), S["cell"]))
        rows.append(row)
    t = Table(rows, colWidths=[w * mm for _, _, w in F_COLS],
              repeatRows=1, splitByRow=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14532d")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f7f2")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return t


def _fit(header, table, n_rows: int):
    """
    표가 한 페이지에 들어갈 만한 크기(대략 30행 이하)면 제목과 함께 묶어
    통째로 다음 장으로 넘긴다. 페이지 끝에 2~3행만 남고 헤더가 반복되는
    어색한 분할을 막기 위함이다. 그보다 크면 자연스럽게 쪼개지도록 둔다.
    """
    if n_rows <= 30:
        return KeepTogether([header, table])
    return [header, table]


SESSION_LABEL = {"AM": "장전 (한국 개장 전)", "PM": "장마감 후", "MANUAL": "수동 조회",
                 "HIST": "소급 조회 (과거 시점)"}


def build(csv_path: str, out_path: str = None, stage2_csv: str = None,
         session: str = None) -> str:
    df = pd.read_csv(csv_path, index_col=0, encoding="utf-8-sig")
    # 한국 종목코드는 앞자리 0이 CSV에서 유실되므로 6자리로 복원 (숫자형 코드만)
    df.index = [str(i).zfill(6) if str(i).isdigit() else str(i) for i in df.index]

    # 파일명(예: sepa_scan_20260824.csv)에서 날짜를 뽑는다.
    # 예전엔 여기서 무조건 dt.date.today()를 썼는데, 소급 스캔한 CSV를 넣으면
    # 표지에는 그 과거 날짜가 찍히면서 파일명은 '오늘' 날짜로 저장되는
    # 불일치가 있었다. 파일명도 항상 CSV의 실제 날짜를 따르도록 통일한다.
    stamp = os.path.basename(csv_path).replace("sepa_scan_", "").replace(".csv", "")
    try:
        scan_date = dt.datetime.strptime(stamp, "%Y%m%d").date()
    except ValueError:
        scan_date = dt.date.today()
        stamp = scan_date.strftime("%Y%m%d")

    today = scan_date.strftime("%Y년 %m월 %d일")
    if session:
        today += f" · {SESSION_LABEL.get(session, session)}"

    passed = df[df["PASS"] == True].sort_values("RS", ascending=False)
    near = df[(df["PASS"] != True) & (df["conditions_met"] >= 7)].sort_values("RS", ascending=False)

    story = []
    story.append(Paragraph("SEPA 트렌드템플릿 일일 스캔 리포트", S["title"]))
    story.append(Paragraph(f"Mark Minervini Trend Template 8조건 정량 스캔 | {today}", S["sub"]))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a2b4c")))
    story.append(Spacer(1, 10))

    kr_n = (df["market"] == "KR").sum()
    us_n = (df["market"] == "US").sum()
    story.append(Paragraph(
        f"스캔 대상 {len(df):,}종목 (한국 {kr_n:,} / 미국 {us_n:,}) 중 "
        f"<font name='{FB}'>{len(passed)}종목</font>이 8조건을 모두 충족했습니다. "
        f"8개 중 7개를 충족한 관찰 종목은 {len(near)}종목입니다.", S["body"]))
    story.append(Spacer(1, 6))

    for tag, label in [("KR", "한국 (KOSPI/KOSDAQ)"), ("US", "미국")]:
        scanned = int((df["market"] == tag).sum())
        sub = passed[passed["market"] == tag]

        # 스캔 자체를 안 한 것과, 스캔했는데 통과가 0인 것은 전혀 다른 상황이다.
        # 둘을 같은 문구로 표시하면 "시장이 약하다"는 잘못된 결론을 부른다.
        if scanned == 0:
            story.append(Paragraph(f"{label} — 스캔 안 함", S["h1"]))
            story.append(Paragraph(
                "이번 실행에는 이 시장이 포함되지 않았습니다. "
                "데이터를 수집하지 않았을 뿐이며, 통과 종목이 없다는 뜻이 아닙니다. "
                "스캔하려면 해당 시장을 포함해 다시 실행하세요.", S["body"]))
            story.append(Spacer(1, 6))
            continue

        _hdr = Paragraph(f"통과 종목 — {label} ({len(sub)}종목 / 스캔 {scanned:,}종목)", S["h1"])
        if sub.empty:
            story.append(_hdr)
            story.append(Paragraph(
                f"{scanned:,}종목을 스캔했으나 8조건을 모두 충족한 종목이 없습니다. "
                "시장 전반의 추세가 약화된 구간일 수 있으며, "
                "미네르비니 방식에서는 이 경우 현금 비중 확대가 정석입니다.", S["body"]))
        else:
            _sub = sub.head(40)
            _fitted = _fit(_hdr, _table(_sub), len(_sub))
            story.extend(_fitted) if isinstance(_fitted, list) else story.append(_fitted)
            if len(sub) > len(_sub):
                story.append(Paragraph(
                    f"RS 상위 {len(_sub)}종목만 표시했습니다. "
                    f"전체 {len(sub)}종목은 CSV 파일에서 확인하세요.", S["small"]))
        story.append(Spacer(1, 6))

    # ── 2단계: DART 펀더멘털 ──────────────────────────────
    if stage2_csv and os.path.exists(stage2_csv):
        s2 = pd.read_csv(stage2_csv, index_col=0, encoding="utf-8-sig")
        s2.index = [str(i).zfill(6) if str(i).isdigit() else str(i) for i in s2.index]
        final = s2[s2.get("SEPA12_PASS") == True].sort_values("RS", ascending=False)

        story.append(Paragraph(
            f"★ 1+2단계 동시 통과 — 한국 ({len(final)}종목)", S["h1"]))
        story.append(Paragraph(
            "트렌드템플릿 8조건에 더해 DART 재무데이터 기준 "
            f"최근 분기 순이익 YoY +{25}% 이상, 매출 YoY +{20}% 이상을 충족한 종목입니다. "
            "SEPA에서 가장 우선순위가 높은 후보군입니다.", S["body"]))
        if final.empty:
            story.append(Paragraph("해당 종목이 없습니다.", S["body"]))
        else:
            story.append(_ftable(final.head(25)))
        story.append(Spacer(1, 6))

    if not near.empty:
        _n = near.head(20)
        _nhdr = Paragraph(
            f"관찰 리스트 — 8개 중 7개 조건 충족 ({len(near)}종목)", S["h1"])
        story.append(_nhdr)
        story.append(Paragraph("한 가지 조건만 미달한 종목입니다. 곧 진입 가능 구간에 들어올 수 있어 "
                               "워치리스트로 관리하세요.", S["body"]))
        _nfit = _fit(Paragraph("", S["small"]), _table(_n), len(_n))
        story.extend(_nfit) if isinstance(_nfit, list) else story.append(_nfit)
        if len(near) > len(_n):
            story.append(Paragraph(
                f"RS 상위 {len(_n)}종목만 표시했습니다. "
                f"전체 {len(near)}종목은 CSV 파일에서 확인하세요.", S["small"]))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#999999")))
    story.append(Spacer(1, 5))
    story.append(Paragraph(
        "RS는 IBD 공식 지표가 아닌 근사식(3/6/9/12개월 가중수익률의 유니버스 내 백분위)입니다. "
        "본 스캔은 SEPA 1단계(기술적 필터)만 자동화한 것으로, 2단계 펀더멘털(EPS·매출 성장, 기관 수급)과 "
        "3단계 정밀 진입(VCP·피벗 돌파·거래량)은 개별 확인이 필요합니다. "
        "투자 참고 자료이며 투자 권유가 아닙니다.", S["small"]))

    if out_path is None:
        suffix = f"_{session}" if session else ""
        out_path = os.path.join(OUT_DIR, f"SEPA리포트_{stamp}{suffix}.pdf")
    SimpleDocTemplate(out_path, pagesize=A4, topMargin=16 * mm, bottomMargin=14 * mm,
                      leftMargin=12 * mm, rightMargin=12 * mm,
                      title="SEPA 일일 스캔 리포트").build(story)
    print(f"리포트 생성: {out_path}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    csv_path = a.csv or os.path.join(OUT_DIR, f"sepa_scan_{dt.date.today():%Y%m%d}.csv")
    build(csv_path)
