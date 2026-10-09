# -*- coding: utf-8 -*-
"""
KIS 시세 2차 측정 (일회성 진단용 — 스캐너 본체와 무관)

사용법: python kis_probe2.py --mode kr|us|usverify

kr       : 한국 장 마감 전후(15:25~16:30) 여러 번 기록
           - 현재가 stck_prpr·고가·저가·거래량 (KRX J)
           - 일자별 시세의 오늘 행 stck_clpr, 지난 거래일(10/8) 행 → 공식 값 정정 여부
           - 당일 분봉(FHKST03010200)의 15:30 봉 존재 여부·가격
us       : 미국 장 마감 전후(KST 04:50~09:00) 여러 번 기록
           - KIS 해외 현재가 상세(last·시가·고가·저가·거래량), 기간별시세 최신 행(clos·tvol)
           - 야후(yfinance) 최신 봉이 언제 채워지는지
           - 한국 일자별 시세: 지난 거래일 행이 언제 공식 값으로 정정되는지
usverify : 미국 최종값 대조(야후가 다 채워진 뒤, 한국 시간 오후)
           - 야후 최종 종가·거래량 vs KIS 기간별시세

결과는 probe/ 아래 CSV·JSON으로 저장한다. 키는 GitHub Secrets 환경변수로만 받는다.
"""
import os, sys, json, time, argparse, datetime as dt
import requests
import pandas as pd

KST = dt.timezone(dt.timedelta(hours=9))
BASE = "https://openapi.koreainvestment.com:9443"
KR = ["005930", "000660", "373220", "207940", "005380", "000270", "068270", "035420",
      "035720", "105560", "055550", "012330", "006400", "051910", "009150", "222800",
      "317400", "096530", "096770", "028670", "010950", "192820", "247540", "086520",
      "036930", "240810", "039030", "042700", "329180", "012450"]
US = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "MU", "ANET",
      "NTAP", "MRVL", "CRL", "MPC", "FFIV", "DDOG", "HUM", "JPM", "XOM", "LLY",
      "UNH", "WMT", "COST", "NFLX", "AMD", "ORCL", "CAT", "GE", "KO", "BAC"]

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["kr", "us", "usverify"], required=True)
ap.add_argument("--schedule", default="")      # 어떤 예약이 이 실행을 시작했는지(지연 측정용)
A = ap.parse_args()

KEY = os.environ.get("KIS_APP_KEY", "").strip()
SEC = os.environ.get("KIS_APP_SECRET", "").strip()
if not KEY or not SEC:
    sys.exit("KIS_APP_KEY / KIS_APP_SECRET 환경변수가 없습니다.")

START = dt.datetime.now(KST)
TAG = f"{A.mode}_{START:%Y%m%d_%H%M}"
os.makedirs("probe", exist_ok=True)
with open(f"probe/meta_{TAG}.json", "w", encoding="utf-8") as f:
    json.dump({"mode": A.mode, "job_start_kst": START.isoformat(), "schedule": A.schedule}, f, ensure_ascii=False)
print(f"[시작] {START:%Y-%m-%d %H:%M:%S} KST  mode={A.mode}  schedule='{A.schedule}'")


def get_token():
    r = requests.post(f"{BASE}/oauth2/tokenP", timeout=15,
                      json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SEC})
    j = r.json()
    if "access_token" not in j:
        sys.exit(f"토큰 발급 실패: HTTP {r.status_code} {j.get('error_description') or j.get('msg1') or j}")
    return j["access_token"]


TOKEN = get_token()


def call(path, tr_id, params):
    h = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {TOKEN}",
         "appkey": KEY, "appsecret": SEC, "tr_id": tr_id, "custtype": "P"}
    j = {}
    for _ in range(4):
        time.sleep(0.07)
        try:
            j = requests.get(BASE + path, headers=h, params=params, timeout=15).json()
        except Exception as e:
            j = {"rt_cd": "X", "msg1": str(e)[:120]}
        if j.get("msg_cd") == "EGW00201":
            time.sleep(1.0)
            continue
        return j
    return j


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def wait_until(hhmm):
    """오늘 KST hh:mm까지 대기. 이미 지났으면 바로 반환(False)."""
    tgt = START.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]), second=0, microsecond=0)
    sec = (tgt - dt.datetime.now(KST)).total_seconds()
    if sec <= 0:
        return False
    time.sleep(sec)
    return True


def append(rows, path):
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv(path, mode="a", header=not os.path.exists(path), index=False, encoding="utf-8")


def kr_daily_rows(t):
    j = call("/uapi/domestic-stock/v1/quotations/inquire-daily-price", "FHKST01010400",
             {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    return {r.get("stck_bsop_date"): r for r in (j.get("output") or []) if isinstance(r, dict)}


def kr_sample(label, with_minute):
    now = dt.datetime.now(KST)
    today = now.strftime("%Y%m%d")
    rows = []
    for t in KR:
        p = (call("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                  {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t}).get("output") or {})
        dly = kr_daily_rows(t)
        days = sorted(d for d in dly if d)
        prev = [d for d in days if d < today]
        prev_day = prev[-1] if prev else None
        row = {"sample": label, "time": now.strftime("%H:%M:%S"), "ticker": t,
               "prpr": num(p.get("stck_prpr")), "high": num(p.get("stck_hgpr")),
               "low": num(p.get("stck_lwpr")), "vol": num(p.get("acml_vol")),
               "today_clpr": num((dly.get(today) or {}).get("stck_clpr")),
               "today_high": num((dly.get(today) or {}).get("stck_hgpr")),
               "today_low": num((dly.get(today) or {}).get("stck_lwpr")),
               "prev_day": prev_day,
               "prev_clpr": num((dly.get(prev_day) or {}).get("stck_clpr")),
               "prev_low": num((dly.get(prev_day) or {}).get("stck_lwpr")),
               "min_bars": None, "min_first": None, "min1530": None}
        if with_minute:
            m = call("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t, "FID_INPUT_HOUR_1": "153000",
                      "FID_PW_DATA_INCU_YN": "N", "FID_ETC_CLS_CODE": ""})
            bars = [b for b in (m.get("output2") or []) if isinstance(b, dict)]
            b1530 = next((b for b in bars if b.get("stck_cntg_hour", "").startswith("1530")), None)
            row.update({"min_bars": len(bars),
                        "min_first": bars[0].get("stck_cntg_hour") if bars else None,
                        "min1530": num(b1530.get("stck_prpr")) if b1530 else None})
        rows.append(row)
    append(rows, f"probe/kr_samples_{START:%Y%m%d}.csv")
    print(f"  [{label}] {now:%H:%M:%S} 기록 {len(rows)}종목")


def run_kr():
    plan = [("1525", False), ("1531", True), ("1535", False), ("1545", False), ("1555", False),
            ("1559", False), ("1602", False), ("1606", True), ("1612", False), ("1620", False),
            ("1630", True)]
    for hhmm, mn in plan:
        on_time = wait_until(hhmm)
        if not on_time and (dt.datetime.now(KST) - START.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))).total_seconds() > 240:
            print(f"  [{hhmm}] 이미 지난 시각 — 건너뜀")
            continue
        kr_sample(hhmm, mn)


# ── 미국 ──
EXCD = {}


def us_detail(sym):
    order = [EXCD[sym]] if sym in EXCD else ["NAS", "NYS", "AMS"]
    for ex in order:
        j = call("/uapi/overseas-price/v1/quotations/price-detail", "HHDFS76200200",
                 {"AUTH": "", "EXCD": ex, "SYMB": sym})
        o = j.get("output") or {}
        if num(o.get("last")):
            EXCD[sym] = ex
            return ex, o
    return None, {}


def us_daily(sym, ex):
    j = call("/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000",
             {"AUTH": "", "EXCD": ex, "SYMB": sym, "GUBN": "0", "BYMD": "", "MODP": "0"})
    return [r for r in (j.get("output2") or []) if isinstance(r, dict)]


def yahoo_last():
    try:
        import yfinance as yf
        df = yf.download(US, period="7d", interval="1d", auto_adjust=False, progress=False,
                         threads=False, group_by="column")
        out = {}
        for s in US:
            c = df["Close"][s].dropna()
            v = df["Volume"][s].dropna()
            out[s] = (str(c.index[-1].date()) if len(c) else None,
                      float(c.iloc[-1]) if len(c) else None,
                      float(v.iloc[-1]) if len(v) else None)
        return out
    except Exception as e:
        print(f"  [야후] 실패: {str(e)[:100]}")
        return {}


def us_sample(label):
    now = dt.datetime.now(KST)
    yh = yahoo_last()
    rows = []
    for s in US:
        ex, o = us_detail(s)
        d = us_daily(s, ex) if ex else []
        top = d[0] if d else {}
        y = yh.get(s, (None, None, None))
        rows.append({"sample": label, "time": now.strftime("%H:%M:%S"), "symbol": s, "excd": ex,
                     "last": num(o.get("last")), "open": num(o.get("open")), "high": num(o.get("high")),
                     "low": num(o.get("low")), "tvol": num(o.get("tvol")), "base": num(o.get("base")),
                     "daily_date": top.get("xymd"), "daily_clos": num(top.get("clos")),
                     "daily_tvol": num(top.get("tvol")),
                     "yahoo_date": y[0], "yahoo_close": y[1], "yahoo_vol": y[2]})
    append(rows, f"probe/us_samples_{START:%Y%m%d}.csv")
    print(f"  [{label}] {now:%H:%M:%S} 미국 {len(rows)}종목")


def kr_correction(label):
    now = dt.datetime.now(KST)
    rows = []
    for t in KR:
        dly = kr_daily_rows(t)
        for d in sorted(dly)[-3:]:
            r = dly[d]
            rows.append({"sample": label, "time": now.strftime("%H:%M:%S"), "ticker": t, "date": d,
                         "clpr": num(r.get("stck_clpr")), "high": num(r.get("stck_hgpr")),
                         "low": num(r.get("stck_lwpr")), "vol": num(r.get("acml_vol"))})
    append(rows, f"probe/kr_correction_{START:%Y%m%d}.csv")
    print(f"  [{label}] {now:%H:%M:%S} 한국 일봉 정정 확인")


def run_us():
    deadline = START + dt.timedelta(hours=5, minutes=45)       # Actions 작업 최대 6시간
    plan = ["0450", "0500", "0503", "0506", "0510", "0515", "0520", "0530", "0545",
            "0600", "0630", "0700", "0730", "0800", "0830", "0900"]
    kr_check = {"0450", "0600", "0700", "0800", "0830", "0900"}
    for hhmm in plan:
        wait_until(hhmm)
        if dt.datetime.now(KST) > deadline:
            print("  작업 시간 한도 근접 — 종료")
            break
        tgt = START.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))
        if (dt.datetime.now(KST) - tgt).total_seconds() > 240:
            print(f"  [{hhmm}] 이미 지난 시각 — 건너뜀")
            continue
        us_sample(hhmm)
        if hhmm in kr_check:
            kr_correction(hhmm)


def run_usverify():
    us_sample("verify")
    kr_correction("verify")


{"kr": run_kr, "us": run_us, "usverify": run_usverify}[A.mode]()
print(f"[끝] {dt.datetime.now(KST):%H:%M:%S} KST")
