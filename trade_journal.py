# -*- coding: utf-8 -*-
"""
trade_journal.py — 백테스트 거래를 "과거 시점부터 순서대로" 매매일지 형태로
정리해서, 매수·매도 타이밍을 연습해볼 수 있는 HTML로 만든다.

[2026-09-19] 설계 근거
- 한 건의 거래(진입 1번+청산 1번)를 "매수 진입" 행과 "매도(청산)" 행,
  두 개의 별도 사건으로 쪼갠 뒤 날짜순으로 전부 섞어서 정렬한다.
  이래야 "이 날짜에 어떤 종목이 매수 신호가 떴고, 다른 종목은 어떻게
  청산됐는지"를 실제 타임라인처럼 볼 수 있다.
- 지금 백테스트 모델은 진입 1번·청산 1번 구조라 "1차/2차/3차 매수"
  같은 분할매수 기록은 없다. 전부 "매수 진입" 한 줄로만 나온다.
- 코멘트는 실제 조건(8조건 통과+VCP entry_ready, 손절가/50일선/만기)을
  근거로 자동 생성한다 — 추측이 아니라 백테스트가 실제로 판정에 쓴
  조건 그대로다.
- 손익률 색상은 한국 증시 관행(상승=빨강·하락=파랑)을 따른다.
"""
import os
import sys
import glob
import argparse

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "output")

EXIT_COMMENTS = {
    "손절": "종가가 손절가 아래로 하회 → 손실 제한을 위해 매도",
    "추세이탈": "종가가 50일 이동평균선 아래로 하회 → 상승 추세 종료로 판단, 매도",
    "만기청산": "정해둔 최대 보유기간에 도달 → 기한 만료로 매도",
    "데이터끝(미청산)": "데이터 구간이 여기서 끝남 — 실제로 청산된 게 아니라, 이 시점 "
                        "가격 기준 참고용 평가액입니다",
}


def _latest_trades_csvs(market: str = None, hold: str = None) -> list:
    """
    [2026-09-19] 예전엔 이름과 다르게 backtest_trades_*.csv 를 매칭되는
    대로 전부 반환해서, 여러 번 실행한 결과(60일·90일·120일, 여러 날짜)가
    죄다 섞여 같은 과거 신호가 중복으로 찍히는 버그가 있었다. 시장별로
    "실제로 가장 최근에 만들어진 파일 딱 하나씩"만 고른다 — 파일명
    문자열 정렬이 아니라 수정 시각(mtime) 기준이다.

    [2026-09-19] hold(예: "h60", "notimeout") — 60일 결과와 무제한 결과를
    나란히 비교하고 싶을 때, "가장 최근 것"만으로는 둘 중 하나가 다른
    하나를 밀어내 버린다(나중에 돌린 쪽이 항상 "최근"이 되므로). hold를
    지정하면 그 만기 태그가 파일명에 정확히 들어간 것만 대상으로 삼아,
    두 조건의 결과를 각각 별도 HTML로 동시에 갖고 있을 수 있다.
    """
    markets = [market] if market else ["KR", "US"]
    chosen = []
    for mkt in markets:
        pattern = f"backtest_trades_*_{mkt}_{hold}.csv" if hold else f"backtest_trades_*_{mkt}_*.csv"
        candidates = glob.glob(os.path.join(OUT_DIR, pattern))
        if candidates:
            latest = max(candidates, key=os.path.getmtime)
            chosen.append(latest)
        else:
            print(f"[매매일지] {mkt}"
                  f"{f' (만기={hold})' if hold else ''} 결과 파일을 못 찾아 건너뜁니다.")
    if not chosen:
        raise FileNotFoundError(
            "backtest_trades_*.csv 를 output/ 에서 못 찾았습니다. backtest.py를 먼저 실행해 주세요."
        )
    return chosen


def _load_name_maps():
    """
    한국은 로컬 캐시(kr_listing.parquet)에서, 미국은 sepa_scanner의
    us_names()로 이름을 가져온다. 둘 다 실패해도(캐시 없음, 네트워크
    문제 등) 종목코드를 이름 대신 쓰는 것으로 안전하게 넘어간다.
    """
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
            print(f"[매매일지] 한국 종목명 캐시 읽기 실패(코드로 대체): {e}")
    return kr_map


def _us_name_map(tickers):
    try:
        from sepa_scanner import us_names
        return dict(us_names(tickers))
    except Exception as e:
        print(f"[매매일지] 미국 종목명 조회 실패(코드로 대체): {str(e)[:150]}")
        return {}


def build_journal(market: str = None, hold: str = None) -> pd.DataFrame:
    """
    거래 CSV(들)을 읽어 진입·청산을 각각 한 행씩으로 분리한 뒤 날짜순으로
    합친 DataFrame을 반환한다.
    """
    files = _latest_trades_csvs(market, hold=hold)
    print(f"[매매일지] 원본 파일 {len(files)}개: {[os.path.basename(f) for f in files]}")

    frames = []
    for f in files:
        # 파일명(backtest_trades_YYYYMMDD_KR_h60.csv)에서 시장 태그를 뽑는다.
        # split("_") -> [backtest, trades, YYYYMMDD, KR, h60] 이므로 인덱스 3.
        parts = os.path.basename(f).replace(".csv", "").split("_")
        mkt = parts[3] if len(parts) > 3 and parts[3] in ("KR", "US") else "?"
        d = pd.read_csv(f, dtype={"ticker": str})
        d["market"] = mkt
        frames.append(d)
    trades = pd.concat(frames, ignore_index=True)

    kr_map = _load_name_maps()
    us_tickers = trades.loc[trades["market"] == "US", "ticker"].unique().tolist()
    us_map = _us_name_map(us_tickers) if us_tickers else {}

    def name_of(row):
        if row["market"] == "KR":
            return kr_map.get(str(row["ticker"]).zfill(6), row["ticker"])
        return us_map.get(row["ticker"], row["ticker"])

    trades["name"] = trades.apply(name_of, axis=1)
    trades["ticker_disp"] = trades.apply(
        lambda r: str(r["ticker"]).zfill(6) if r["market"] == "KR" else r["ticker"], axis=1)

    events = []
    for _, r in trades.iterrows():
        events.append({
            "date": r["entry_date"][:10], "market": r["market"], "name": r["name"],
            "ticker": r["ticker_disp"], "action": "매수 진입", "price": r["entry_price"],
            "comment": (f"8조건(트렌드템플릿) 통과 + VCP 패턴 완성(entry_ready)으로 매수. "
                        f"진입 시점 RS {r['rs_at_entry']:.0f}" if pd.notna(r.get("rs_at_entry"))
                        else "8조건(트렌드템플릿) 통과 + VCP 패턴 완성(entry_ready)으로 매수"),
            "return_pct": None,
        })
        events.append({
            "date": r["exit_date"][:10], "market": r["market"], "name": r["name"],
            "ticker": r["ticker_disp"], "action": "매도(청산)", "price": r["exit_price"],
            "comment": EXIT_COMMENTS.get(r["exit_reason"], r["exit_reason"]),
            "return_pct": r["return_pct"],
        })

    journal = pd.DataFrame(events)
    journal = journal.sort_values(["date", "ticker", "action"]).reset_index(drop=True)
    return journal


def render_html(journal: pd.DataFrame, title: str) -> str:
    rows_html = []
    for _, r in journal.iterrows():
        if pd.notna(r["return_pct"]):
            color = "#C0343B" if r["return_pct"] >= 0 else "#1B5FA6"   # 빨강=수익, 파랑=손실
            sign = "+" if r["return_pct"] >= 0 else ""
            ret_cell = f'<td data-sort="{r["return_pct"]}" style="color:{color};font-weight:700">{sign}{r["return_pct"]:.2f}%</td>'
        else:
            ret_cell = '<td data-sort="">–</td>'
        mkt_badge = "🇰🇷" if r["market"] == "KR" else "🇺🇸"
        action_color = "#1a7f37" if r["action"] == "매수 진입" else "#4A525C"
        # [2026-09-19] 한국은 원화라 소수점이 의미 없어 정수로, 미국은
        # 달러 소수점 단위 등락이 실제로 의미가 있어 그대로 둔다.
        price_str = f"{r['price']:,.0f}" if r["market"] == "KR" else f"{r['price']:,.2f}"
        rows_html.append(f"""<tr>
            <td data-sort="{r['date']}">{r['date']}</td>
            <td data-sort="{r['market']}">{mkt_badge}</td>
            <td data-sort="{r['name']}">{r['name']}</td>
            <td data-sort="{r['ticker']}" style="color:#8B939D">{r['ticker']}</td>
            <td data-sort="{r['action']}" style="color:{action_color};font-weight:700">{r['action']}</td>
            <td data-sort="{r['price']}" style="text-align:right">{price_str}</td>
            <td data-sort="{r['comment']}" style="font-size:12px;color:#4A525C">{r['comment']}</td>
            {ret_cell}
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
  <p class="sub">과거 시점부터 순서대로 정렬 — 실제로 그 시점에 있었다면 어떻게 판단했을지 연습해보세요.
  매수 진입만 보고 먼저 스스로 판단해본 뒤, 실제 매도 결과와 코멘트를 확인하는 방식을 권합니다.</p>
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
        <th data-col="0">날짜</th><th data-col="1">시장</th><th data-col="2">종목명</th><th data-col="3">코드</th><th data-col="4">구분</th>
        <th data-col="5" style="text-align:right">가격</th><th data-col="6">코멘트</th><th data-col="7">손익률</th>
      </tr></thead>
      <tbody id="tbody">
        {''.join(rows_html)}
      </tbody>
    </table>
  </div>
  <p class="note">
    * "매수 진입"만 있고 "1차/2차/3차"가 없는 이유: 이 백테스트는 진입 1번·청산 1번 구조라 분할매수 기록이 없습니다.<br>
    * 코멘트는 실제 8조건·VCP·손절가·50일선 판정 결과를 그대로 반영한 것입니다(추측 아님).<br>
    * 빨강 = 수익 매도, 파랑 = 손실 매도 (한국 증시 관행).
  </p>
<script>
let currentMarketFilter = 'all';
let currentSearch = '';

function applyFilters(){{
  const q = currentSearch.trim().toLowerCase();
  document.querySelectorAll('#tbody tr').forEach(tr=>{{
    const mkt = tr.children[1].textContent.includes('🇰🇷') ? 'KR' : 'US';
    const name = tr.children[2].textContent.toLowerCase();
    const code = tr.children[3].textContent.toLowerCase();
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

// [2026-09-19] 헤더 더블클릭 정렬. 화면에 보이는 텍스트가 아니라 각 셀의
// data-sort 값(원본 값)으로 정렬해야 날짜·가격·손익률이 숫자/날짜답게
// 정렬된다 — 화면 표시 문자열(₩ 표시, % 기호 등) 그대로 정렬하면 순서가
// 틀어진다.
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
                     help="생략하면 한국+미국 결과를 합쳐서 하나의 타임라인으로 만든다")
    ap.add_argument("--hold", default=None,
                     help="특정 만기 조건만 지정(예: h60, h90, h120, notimeout). "
                          "생략하면 시장별로 가장 최근에 실행한 결과를 쓴다. "
                          "60일과 무제한을 나란히 비교하고 싶을 때, 이 옵션으로 "
                          "각각 --hold h60 / --hold notimeout 을 지정해 둘 다 만들어두면 "
                          "서로 덮어쓰지 않고 별도 파일로 남는다.")
    args = ap.parse_args()

    journal = build_journal(market=args.market, hold=args.hold)
    hold_label = f" · 만기 {args.hold}" if args.hold else ""
    title = f"SEPA 매매일지 연습 · {'한국' if args.market=='KR' else '미국' if args.market=='US' else '한국+미국'}{hold_label}"
    html = render_html(journal, title)

    os.makedirs(OUT_DIR, exist_ok=True)
    mkt_tag = args.market or "ALL"
    hold_tag = f"_{args.hold}" if args.hold else ""
    out_path = os.path.join(OUT_DIR, f"trade_journal_{mkt_tag}{hold_tag}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[매매일지] 총 {len(journal)}개 사건(매수+매도) 정리 완료")
    print(f"[매매일지] 저장: {out_path}")
    print(f"[매매일지] 브라우저로 열어서 확인하세요: open '{out_path}' (맥에서)")


if __name__ == "__main__":
    main()
