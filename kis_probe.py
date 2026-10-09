# -*- coding: utf-8 -*-
"""
KIS 시세 필드 시험 (일회성 진단용 — 스캐너 본체와 무관)

목적: KRX 애프터마켓(16:00~20:00) 체결이 KIS의 어느 필드에 섞이는지 확인한다.
  - 주식현재가 stck_prpr (KRX J / NXT NX / 통합 UN)
  - 일자별 시세 stck_clpr (10/7, 10/8 등 최근 거래일)
  - 시간외 일자별 시세 ovtm_untp_* (기존 시간외 단일가 필드에 애프터마켓이 들어가는지)
  - 과거 분봉: 15:30 봉, 그리고 16:00~20:00 구간에 봉이 존재하는지
  - KRX Open API 캐시(cache/kr_krx_daily.parquet)의 공식 종가·고가·저가·거래량과 비교

결과: probe/kis_probe_<시각>.csv (요약), probe/kis_raw_<시각>.json (원본 응답 전체)
키는 환경변수 KIS_APP_KEY / KIS_APP_SECRET (GitHub Secrets)로만 받는다. 토큰은 출력하지 않는다.
"""
import os, sys, json, time, datetime as dt
import requests
import pandas as pd

BASE = "https://openapi.koreainvestment.com:9443"   # 실전투자 도메인
TICKERS = [
    "005930", "000660", "373220", "207940", "005380", "000270", "068270", "035420",
    "035720", "105560", "055550", "012330", "006400", "051910", "009150", "222800",
    "317400", "096530", "096770", "028670", "010950", "192820", "247540", "086520",
    "036930", "240810", "039030", "042700", "329180", "012450",
]

KEY = os.environ.get("KIS_APP_KEY", "").strip()
SEC = os.environ.get("KIS_APP_SECRET", "").strip()
if not KEY or not SEC:
    sys.exit("KIS_APP_KEY / KIS_APP_SECRET 환경변수가 없습니다 (GitHub Secrets 확인).")


def get_token():
    r = requests.post(f"{BASE}/oauth2/tokenP", timeout=15,
                      json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SEC})
    j = r.json()
    if "access_token" not in j:
        sys.exit(f"토큰 발급 실패: HTTP {r.status_code} {j.get('error_description') or j.get('msg1') or j}")
    print(f"[토큰] 발급 성공 (만료 {j.get('access_token_token_expired', '?')})")
    return j["access_token"]


TOKEN = get_token()
CALLS = {"n": 0}


def call(path, tr_id, params):
    h = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {TOKEN}",
         "appkey": KEY, "appsecret": SEC, "tr_id": tr_id, "custtype": "P"}
    for attempt in range(4):
        time.sleep(0.07)                     # 초당 약 14건 — 실전 한도(초당 20건) 아래
        CALLS["n"] += 1
        try:
            r = requests.get(BASE + path, headers=h, params=params, timeout=15)
            j = r.json()
        except Exception as e:
            j = {"rt_cd": "X", "msg1": str(e)[:120]}
        if j.get("msg_cd") == "EGW00201":    # 초당 호출 초과 → 잠시 쉬고 재시도
            time.sleep(1.0)
            continue
        return j
    return j


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def by_date(rows, key="stck_bsop_date"):
    return {r.get(key): r for r in (rows or []) if isinstance(r, dict) and r.get(key)}


# ── KRX 공식 값(캐시) ──
krx = None
try:
    krx = pd.read_parquet("cache/kr_krx_daily.parquet")
    krx["date"] = pd.to_datetime(krx["date"])
    krx = krx[krx["ticker"].isin(TICKERS)]
    print(f"[KRX 캐시] 마지막 날짜 {krx['date'].max().date()}")
except Exception as e:
    print(f"[KRX 캐시] 읽기 실패: {e}")

now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
stamp = now.strftime("%Y%m%d_%H%M")
raw, rows = {}, []

for t in TICKERS:
    R = raw[t] = {}
    for mk in ("J", "NX", "UN"):
        R[f"price_{mk}"] = call("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                               {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": t})
    R["daily"] = call("/uapi/domestic-stock/v1/quotations/inquire-daily-price", "FHKST01010400",
                      {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t,
                       "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    R["ovtm_daily"] = call("/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice", "FHPST02320000",
                           {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t})
    R["ovtm_now"] = call("/uapi/domestic-stock/v1/quotations/inquire-overtime-price", "FHPST02300000",
                         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t})

    daily = by_date(R["daily"].get("output"))
    ovd = by_date(R["ovtm_daily"].get("output2"))
    dates = sorted(daily.keys())[-2:]            # 최근 거래일 2개 (예: 20261007, 20261008)
    last_day = dates[-1] if dates else None

    # 마지막 거래일 분봉: 15:30 봉 / 20:00 기준(애프터마켓 봉 존재 여부)
    for hh in ("153000", "200000"):
        R[f"min_{hh}"] = call("/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", "FHKST03010230",
                              {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t, "FID_INPUT_HOUR_1": hh,
                               "FID_INPUT_DATE_1": last_day or "", "FID_PW_DATA_INCU_YN": "N",
                               "FID_FAKE_TICK_INCU_YN": ""})
    mins = [m for m in (R["min_153000"].get("output2") or []) + (R["min_200000"].get("output2") or [])
            if isinstance(m, dict) and m.get("stck_bsop_date") == last_day]
    bar1530 = next((m for m in mins if m.get("stck_cntg_hour", "").startswith("1530")), None)
    after = [m for m in mins if m.get("stck_cntg_hour", "") >= "160000"]
    after_last = max(after, key=lambda m: m["stck_cntg_hour"]) if after else None

    p = {mk: (R[f"price_{mk}"].get("output") or {}) for mk in ("J", "NX", "UN")}
    for d in dates:
        k = krx[(krx["ticker"] == t) & (krx["date"] == pd.Timestamp(d))] if krx is not None else pd.DataFrame()
        k = k.iloc[0] if len(k) else None
        o = ovd.get(d, {})
        row = {
            "ticker": t, "date": d,
            "KRX_close": None if k is None else num(k["close"]),
            "KRX_high": None if k is None else num(k["high"]),
            "KRX_low": None if k is None else num(k["low"]),
            "KRX_vol": None if k is None else num(k["volume"]),
            "daily_clpr": num(daily[d].get("stck_clpr")),
            "daily_high": num(daily[d].get("stck_hgpr")),
            "daily_low": num(daily[d].get("stck_lwpr")),
            "daily_vol": num(daily[d].get("acml_vol")),
            "ovtm_clpr": num(o.get("stck_clpr")),
            "ovtm_prpr": num(o.get("ovtm_untp_prpr")),
            "ovtm_vol": num(o.get("ovtm_untp_vol")),
        }
        if d == last_day:
            row.update({
                "prpr_J": num(p["J"].get("stck_prpr")), "prpr_NX": num(p["NX"].get("stck_prpr")),
                "prpr_UN": num(p["UN"].get("stck_prpr")),
                "high_J": num(p["J"].get("stck_hgpr")), "low_J": num(p["J"].get("stck_lwpr")),
                "vol_J": num(p["J"].get("acml_vol")),
                "min1530": num(bar1530.get("stck_prpr")) if bar1530 else None,
                "after_bars": len(after),
                "after_last_time": after_last.get("stck_cntg_hour") if after_last else None,
                "after_last_px": num(after_last.get("stck_prpr")) if after_last else None,
                "ovtm_now_prpr": num((R["ovtm_now"].get("output") or {}).get("ovtm_untp_prpr")),
            })
        rows.append(row)
    print(f"  {t} 완료 (누적 호출 {CALLS['n']})")

df = pd.DataFrame(rows)
os.makedirs("probe", exist_ok=True)
df.to_csv(f"probe/kis_probe_{stamp}.csv", index=False, encoding="utf-8-sig")
with open(f"probe/kis_raw_{stamp}.json", "w", encoding="utf-8") as f:
    json.dump({"run_kst": now.isoformat(), "raw": raw}, f, ensure_ascii=False)

# ── 판정 요약 ──
def same(a, b):
    return a is not None and b is not None and abs(a - b) < 0.5

last = df[df["prpr_J"].notna()]
print("\n================ 판정 요약 ================")
print(f"실행 시각 {now:%Y-%m-%d %H:%M} KST, 종목 {len(TICKERS)}개, 호출 {CALLS['n']}회")
print(f"마지막 거래일: {', '.join(sorted(df['date'].unique()))}")
checks = [
    ("일봉 stck_clpr == KRX 공식 종가 (전체 날짜)", df, "daily_clpr", "KRX_close"),
    ("일봉 고가 == KRX 공식 고가", df, "daily_high", "KRX_high"),
    ("일봉 저가 == KRX 공식 저가", df, "daily_low", "KRX_low"),
    ("현재가 stck_prpr(J) == 마지막 날 KRX 종가", last, "prpr_J", "KRX_close"),
    ("현재가 stck_prpr(J) == 마지막 날 일봉 종가", last, "prpr_J", "daily_clpr"),
    ("분봉 15:30 == 마지막 날 KRX 종가", last, "min1530", "KRX_close"),
    ("현재가(J) 고가 == KRX 공식 고가", last, "high_J", "KRX_high"),
]
for name, d, a, b in checks:
    ok = sum(same(x, y) for x, y in zip(d[a], d[b]))
    print(f"  {name}: {ok}/{len(d)} 일치")
vr = (df["daily_vol"] / df["KRX_vol"]).dropna()
if len(vr):
    print(f"  일봉 거래량 / KRX 공식 거래량: 평균 {vr.mean():.3f} (최소 {vr.min():.3f}, 최대 {vr.max():.3f})")
print(f"  16:00 이후 분봉이 있는 종목: {(last['after_bars'] > 0).sum()}/{len(last)}")
diff_after = last[last["after_last_px"].notna() & (last["after_last_px"] != last["KRX_close"])]
print(f"  16:00 이후 마지막 분봉 가격이 정규장 종가와 다른 종목: {len(diff_after)}개")
print("\n[마지막 거래일 상세]")
cols = ["ticker", "KRX_close", "daily_clpr", "prpr_J", "prpr_NX", "prpr_UN", "min1530",
        "after_last_time", "after_last_px", "ovtm_prpr", "ovtm_now_prpr", "KRX_vol", "vol_J"]
print(last[cols].to_string(index=False))
print(f"\n저장: probe/kis_probe_{stamp}.csv, probe/kis_raw_{stamp}.json")
