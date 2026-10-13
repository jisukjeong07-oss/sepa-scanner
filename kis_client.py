# -*- coding: utf-8 -*-
"""
kis_client.py — 한국투자증권(KIS) Open API 공용 클라이언트.
kis_kr_today.py(한국 당일 봉)와 kis_us_today.py(미국 당일 봉)가 함께 쓴다.

[2026-10-13] 왜 따로 뺐나 / 병렬 호출
- 10/12·10/13 장마감 실행에서 한국 2,800종목 × 2회 호출을 한 줄로 보내니
  45~60분이 걸렸다. GitHub 러너(미국)에서 한국 서버까지 왕복이 0.3초쯤이라
  호출 간격(0.06초)보다 대기 시간이 훨씬 길었기 때문이다.
- 그래서 여러 요청을 동시에 보내되, "초당 호출 수"는 토큰 버킷으로 묶어
  실전 한도(초당 20건) 아래(기본 15건)로 지킨다. 동시 요청 수(스레드)는
  왕복 지연을 메우는 용도일 뿐이고, 실제 속도는 초당 한도가 정한다.
  → 5,600회 호출 ≈ 6~7분.
- 한도 초과(EGW00201)가 오면 그 요청만 1~4초 쉬고 재시도한다.

보안: 키는 환경변수 KIS_APP_KEY / KIS_APP_SECRET(GitHub Secrets)로만 받고,
토큰은 저장소 밖 임시 폴더에만 둔다(daily.yml이 cache/ 아래를 모두 커밋하므로).
"""
from __future__ import annotations
import os
import json
import time
import threading
import tempfile
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import requests

KST = dt.timezone(dt.timedelta(hours=9))
BASE = "https://openapi.koreainvestment.com:9443"     # 실전투자 도메인
TOKEN_CACHE = os.path.join(tempfile.gettempdir(), "sepa_kis_token.json")
RATE_PER_SEC = float(os.environ.get("KIS_RATE", "15"))   # 초당 호출 상한(실전 한도 20)
WORKERS = int(os.environ.get("KIS_WORKERS", "8"))        # 동시 요청 수


class _Bucket:
    """초당 RATE_PER_SEC 건으로 호출을 고르게 흘려보내는 토큰 버킷."""

    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def take(self):
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
            time.sleep(wait)


class KIS:
    def __init__(self, key: str | None = None, secret: str | None = None):
        self.key = (key or os.environ.get("KIS_APP_KEY", "")).strip()
        self.secret = (secret or os.environ.get("KIS_APP_SECRET", "")).strip()
        if not self.key or not self.secret:
            raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET 환경변수가 없습니다.")
        self.token = None
        self.calls = 0
        self.retries = 0
        self._bucket = _Bucket(RATE_PER_SEC)
        self._lock = threading.Lock()
        self._local = threading.local()

    # ── 인증 ──
    def auth(self):
        try:
            with open(TOKEN_CACHE, encoding="utf-8") as f:
                c = json.load(f)
            exp = dt.datetime.fromisoformat(c["expires"])
            if dt.datetime.now(KST) < exp - dt.timedelta(minutes=10):
                self.token = c["token"]
                return self
        except Exception:
            pass
        for attempt in range(3):
            r = requests.post(f"{BASE}/oauth2/tokenP", timeout=20,
                              json={"grant_type": "client_credentials",
                                    "appkey": self.key, "appsecret": self.secret})
            try:
                j = r.json()
            except Exception:
                j = {}
            if "access_token" in j:
                self.token = j["access_token"]
                try:
                    exp_dt = dt.datetime.strptime(j.get("access_token_token_expired", ""),
                                                  "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                except Exception:
                    exp_dt = dt.datetime.now(KST) + dt.timedelta(hours=23)
                try:
                    with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
                        json.dump({"token": self.token, "expires": exp_dt.isoformat()}, f)
                except Exception:
                    pass
                return self
            msg = str(j.get("error_description") or j.get("msg1") or f"HTTP {r.status_code}")
            if "EGW00133" in str(j) or "1분" in msg:
                print(f"[KIS] 토큰 발급 제한(1분 1회) — 65초 후 재시도 ({attempt + 1}/3)")
                time.sleep(65)
                continue
            raise RuntimeError(f"KIS 토큰 발급 실패: {msg[:120]}")
        raise RuntimeError("KIS 토큰 발급 실패: 재시도 초과")

    # ── 호출 ──
    def _session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            self._local.s = s
        return s

    def get(self, path: str, tr_id: str, params: dict) -> dict:
        h = {"content-type": "application/json; charset=utf-8",
             "authorization": f"Bearer {self.token}",
             "appkey": self.key, "appsecret": self.secret,
             "tr_id": tr_id, "custtype": "P"}
        j = {}
        for attempt in range(4):
            self._bucket.take()
            with self._lock:
                self.calls += 1
            try:
                r = self._session().get(BASE + path, headers=h, params=params, timeout=15)
                j = r.json()
            except Exception as e:
                j = {"rt_cd": "X", "msg1": f"요청 실패: {str(e)[:80]}"}
                time.sleep(1.0)
                continue
            if j.get("msg_cd") == "EGW00201":       # 초당 호출 한도 초과
                with self._lock:
                    self.retries += 1
                time.sleep(1.0 + attempt)
                continue
            return j
        return j

    def map(self, fn, items, workers: int | None = None, progress_every: int = 500,
            label: str = "") -> list:
        """items 각각에 fn을 병렬 적용. 순서 보존. 예외는 None으로."""
        workers = workers or WORKERS
        out = [None] * len(items)
        t0 = time.time()
        done = 0

        def run(i_item):
            i, item = i_item
            try:
                return i, fn(item)
            except Exception as e:
                return i, None

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, res in ex.map(run, enumerate(items)):
                out[i] = res
                done += 1
                if progress_every and done % progress_every == 0:
                    ok = sum(1 for x in out[:done] if x)
                    print(f"  ... {done}/{len(items)} ({ok}건 확보, {time.time() - t0:.0f}초, "
                          f"호출 {self.calls}회{', 재시도 ' + str(self.retries) + '회' if self.retries else ''}){label}")
        return out


def num(v):
    try:
        f = float(str(v).replace(",", ""))
        return f if f == f else None
    except Exception:
        return None
