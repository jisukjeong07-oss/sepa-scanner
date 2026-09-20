# -*- coding: utf-8 -*-
"""
missed_patterns_report.py — find_missed_patterns.py가 이미 만들어둔
missed_patterns_*.csv를 읽어, 정렬·검색 가능한 HTML로 보여준다.

[2026-09-19] 설계 근거
- 무거운 전체 유니버스 재계산(find_missed_patterns.py)은 다시 안 돌린다.
  이미 저장된 CSV를 읽기만 하므로 몇 초 안에 끝난다.
- trade_journal.py에서 만든 기능(헤더 더블클릭 정렬, 종목명·코드 검색,
  한국 정수·미국 소수점 가격 포맷, 시장 필터, 이름 조회)을 그대로
  재사용한다 — 새로 만들면 서로 다르게 동작해서 헷갈릴 수 있어서다.
"""
import os
import sys
import glob
import argparse

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "output")


def _latest_missed_csvs(market: str = None) -> list:
    """시장별로 실제 가장 최근에 만들어진 missed_patterns 파일만 고른다
    (trade_journal.py의 _latest_trades_csvs와 같은 방식 — mtime 기준)."""
    markets = [market] if market else ["KR", "US"]
    chosen = []
    for mkt in markets:
        candidates = glob.glob(os.path.join(OUT_DIR, f"missed_patterns_{mkt}_*.csv"))
        if candidates:
            chosen.append(max(candidates, key=os.path.getmtime))
        else:
            print(f"[놓친패턴 리포트] {mkt} 결과 파일을 못 찾아 건너뜁니다.")
    if not chosen:
        raise FileNotFoundError(
            "missed_patterns_*.csv 를 output/ 에서 못 찾았습니다. "
            "find_missed_patterns.py를 먼저 실행해 주세요."
        )
    return chosen


def _load_name_maps():
    kr_map = {}
    path = os.path.join(BASE_DIR, "cache", "kr_listing.parquet")
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
            name_col = next((c for c in ("Name", "name", "종목명") if c in df.columns), None)
            if name_col:
                idx = df.index.astype(str).str.zfill(6)
                kr_map = dict(zip(idx, df[name_col]))
        except Exception as e:
            print(f"[놓친패턴 리포트] 한국 종목명 캐시 읽기 실패(코드로 대체): {e}")
    return kr_map


def _us_name_map(tickers):
    try:
        from sepa_scanner import us_names
        return dict(us_names(tickers))
    except Exception as e:
        print(f"[놓친패턴 리포트] 미국 종목명 조회 실패(코드로 대체): {str(e)[:150]}")
        return {}


def build_table(market: str = None) -> pd.DataFrame:
    files = _latest_missed_csvs(market)
    print(f"[놓친패턴 리포트] 원본 파일 {len(files)}개: {[os.path.basename(f) for f in files]}")

    frames = []
    for f in files:
        # 파일명: missed_patterns_KR_YYYYMMDD.csv -> 인덱스 2가 시장
        parts = os.path.basename(f).replace(".csv", "").split("_")
        mkt = parts[2] if len(parts) > 2 and parts[2] in ("KR", "US") else "?"
        d = pd.read_csv(f, dtype={"ticker": str})
        d["market"] = mkt
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)

    kr_map = _load_name_maps()
    us_tickers = df.loc[df["market"] == "US", "ticker"].unique().tolist()
    us_map = _us_name_map(us_tickers) if us_tickers else {}

    def name_of(row):
        if row["market"] == "KR":
            return kr_map.get(str(row["ticker"]).zfill(6), row["ticker"])
        return us_map.get(row["ticker"], row["ticker"])

    df["name"] = df.apply(name_of, axis=1)
    df["ticker_disp"] = df.apply(
        lambda r: str(r["ticker"]).zfill(6) if r["market"] == "KR" else r["ticker"], axis=1)

    df = df.sort_values("missed_return_pct", ascending=False).reset_index(drop=True)
    return df


def render_html(df: pd.DataFrame, title: str) -> str:
    rows_html = []
    for _, r in df.iterrows():
        color = "#C0343B" if r["missed_return_pct"] >= 0 else "#1B5FA6"   # 빨강=수익, 파랑=손실
        sign = "+" if r["missed_return_pct"] >= 0 else ""
        # [2026-09-19] trade_journal.py와 동일 — 한국은 원화라 정수로,
        # 미국은 달러 소수점 단위 등락도 의미가 있어 그대로 둔다.
        price_fmt = (lambda v: f"{v:,.0f}") if r["market"] == "KR" else (lambda v: f"{v:,.2f}")
        recov_badge = ('<span style="color:#1a7f37;font-weight:700">회복함</span>'
                       if r["recovered_later"] else
                       '<span style="color:#8B939D">회복 안 함</span>')
        recov_date = r["recovered_date"] if pd.notna(r.get("recovered_date")) else "–"
        mkt_badge = "🇰🇷" if r["market"] == "KR" else "🇺🇸"

        rows_html.append(f"""<tr>
            <td data-sort="{r['name']}">{r['name']}</td>
            <td data-sort="{r['ticker_disp']}" style="color:#8B939D">{r['ticker_disp']}</td>
            <td data-sort="{r['market']}">{mkt_badge}</td>
            <td data-sort="{r['developing_date']}">{r['developing_date']}</td>
            <td data-sort="{r['developing_price']}" style="text-align:right">{price_fmt(r['developing_price'])}</td>
            <td data-sort="{r['extended_date']}">{r['extended_date']}</td>
            <td data-sort="{r['extended_price']}" style="text-align:right">{price_fmt(r['extended_price'])}</td>
            <td data-sort="{r['missed_return_pct']}" style="color:{color};font-weight:700">{sign}{r['missed_return_pct']:.2f}%</td>
            <td data-sort="{r['days_span']}" style="text-align:right">{int(r['days_span'])}</td>
            <td data-sort="{1 if r['recovered_later'] else 0}">{recov_badge}</td>
            <td data-sort="{recov_date}">{recov_date}</td>
        </tr>""")

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>{title}</title>
<style>
  body{{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;
       background:#EDF0F3;margin:0;padding:20px;color:#12161C;font-size:13px}}
  h1{{font-size:18px;color:#1B2A4A;margin:0 0 4px}}
  .sub{{font-size:12px;color:#4A525C;margin:0 0 14px}}
  .filters{{margin-bottom:10px}}
  .filters button{{border:1px solid #D5DBE1;background:#fff;border-radius:6px;
       padding:5px 12px;margin-right:6px;cursor:pointer;font-size:12px}}
  .filters button[aria-pressed="true"]{{background:#1B2A4A;color:#fff;border-color:#1B2A4A}}
  .wrap{{background:#fff;border:1px solid #D5DBE1;border-radius:10px;overflow:auto;max-height:80vh}}
  table{{width:100%;border-collapse:collapse}}
  th{{position:sticky;top:0;background:#1B2A4A;color:#fff;text-align:left;
      padding:8px 10px;font-size:12px;white-space:nowrap;cursor:pointer;user-select:none}}
  th:hover{{background:#26385c}}
  th.sorted-asc::after{{content:" ▲"}}
  th.sorted-desc::after{{content:" ▼"}}
  td{{padding:7px 10px;border-bottom:1px solid #EEF1F4;white-space:nowrap}}
  tr:hover td{{background:#F7F9FB}}
  .note{{font-size:11px;color:#8B939D;margin-top:10px;line-height:1.6}}
</style></head>
<body>
  <h1>{title}</h1>
  <p class="sub">8조건은 통과했지만, VCP가 "진입가능(entry_ready)"을 한 번도 안 낸 채
  형성 중(developing)에서 바로 과열(extended)로 넘어가버린 종목들입니다.
  "놓친수익률"은 형성 중이 시작된 날부터 과열로 확인된 날까지의 가격 변화입니다.</p>
  <div class="filters" role="group" aria-label="시장 필터">
    <button data-f="all" aria-pressed="true">전체</button>
    <button data-f="KR" aria-pressed="false">한국만</button>
    <button data-f="US" aria-pressed="false">미국만</button>
    <input id="searchBox" type="search" placeholder="종목명 또는 코드 검색"
           style="margin-left:10px;padding:5px 10px;border:1px solid #D5DBE1;border-radius:6px;
                  font-size:12px;width:200px">
  </div>
  <div class="wrap">
    <table>
      <thead><tr>
        <th data-col="0">종목명</th><th data-col="1">코드</th><th data-col="2">시장</th>
        <th data-col="3">형성중 시작일</th><th data-col="4" style="text-align:right">형성중 가격</th>
        <th data-col="5">과열 확인일</th><th data-col="6" style="text-align:right">과열 가격</th>
        <th data-col="7">놓친수익률</th><th data-col="8" style="text-align:right">기간(일)</th>
        <th data-col="9">이후 회복</th><th data-col="10">회복일</th>
      </tr></thead>
      <tbody id="tbody">
        {''.join(rows_html)}
      </tbody>
    </table>
  </div>
  <p class="note">
    * 빨강 = 놓친 구간이 상승(대부분 이 경우), 파랑 = 하락.<br>
    * "이후 회복"은 과열 판정 이후 언젠가 entry_ready가 다시 떴는지 여부입니다 —
    "회복 안 함"이면 그 뒤로 진입가능 시점이 한 번도 없었다는 뜻입니다.
  </p>
<script>
let currentMarketFilter = 'all';
let currentSearch = '';

function applyFilters(){{
  const q = currentSearch.trim().toLowerCase();
  document.querySelectorAll('#tbody tr').forEach(tr=>{{
    const mkt = tr.children[2].textContent.includes('🇰🇷') ? 'KR' : 'US';
    const name = tr.children[0].textContent.toLowerCase();
    const code = tr.children[1].textContent.toLowerCase();
    const mktOk = (currentMarketFilter==='all' || currentMarketFilter===mkt);
    const searchOk = (q==='' || name.includes(q) || code.includes(q));
    tr.style.display = (mktOk && searchOk) ? '' : 'none';
  }});
}}

document.querySelectorAll('.filters button').forEach(btn=>{{
  btn.addEventListener('click', ()=>{{
    document.querySelectorAll('.filters button').forEach(b=>b.setAttribute('aria-pressed', b===btn?'true':'false'));
    currentMarketFilter = btn.dataset.f;
    applyFilters();
  }});
}});

document.getElementById('searchBox').addEventListener('input', (e)=>{{
  currentSearch = e.target.value;
  applyFilters();
}});

let sortState = {{col: null, dir: 1}};
document.querySelectorAll('th[data-col]').forEach(th=>{{
  th.addEventListener('dblclick', ()=>{{
    const col = parseInt(th.dataset.col, 10);
    sortState.dir = (sortState.col === col) ? -sortState.dir : 1;
    sortState.col = col;

    document.querySelectorAll('th[data-col]').forEach(h=>h.classList.remove('sorted-asc','sorted-desc'));
    th.classList.add(sortState.dir===1 ? 'sorted-asc' : 'sorted-desc');

    const tbody = document.getElementById('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a,b)=>{{
      const av = a.children[col].dataset.sort, bv = b.children[col].dataset.sort;
      const an = parseFloat(av), bn = parseFloat(bv);
      const bothNumeric = !isNaN(an) && !isNaN(bn) && av.trim()!=='' && bv.trim()!=='';
      let cmp;
      if(bothNumeric) cmp = an - bn;
      else cmp = av.localeCompare(bv, 'ko');
      return cmp * sortState.dir;
    }});
    rows.forEach(tr => tbody.appendChild(tr));
  }});
}});
</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["KR", "US"], default=None,
                     help="생략하면 한국+미국 결과를 합쳐서 하나의 표로 만든다")
    args = ap.parse_args()

    df = build_table(market=args.market)
    title = f"SEPA 놓친 패턴 리포트 · {'한국' if args.market=='KR' else '미국' if args.market=='US' else '한국+미국'}"
    html = render_html(df, title)

    os.makedirs(OUT_DIR, exist_ok=True)
    tag = args.market or "ALL"
    out_path = os.path.join(OUT_DIR, f"missed_patterns_report_{tag}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[놓친패턴 리포트] 총 {len(df)}종목 정리 완료")
    print(f"[놓친패턴 리포트] 저장: {out_path}")
    print(f"[놓친패턴 리포트] 브라우저로 열어서 확인하세요: open '{out_path}' (맥에서)")


if __name__ == "__main__":
    main()
