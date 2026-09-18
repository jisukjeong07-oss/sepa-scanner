# -*- coding: utf-8 -*-
"""
send_telegram.py — 완성된 SEPA PDF 리포트를 텔레그램으로 전송한다.

[2026-09-18] 설계 근거
- 매일 장전·장마감 스캔이 끝난 뒤, run_daily.py가 만든 PDF를 그대로
  텔레그램 봇 API(sendDocument)로 전송한다. 이미 쓰고 계신 텔레그램
  봇을 그대로 재사용한다 — 새 봇을 만들 필요 없이, .env에 그 봇의
  토큰·chat_id만 추가하면 된다.
- 다른 API 키들과 동일한 방식으로 .env에서 읽는다:
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...
- 전송 실패해도(토큰 미설정, 네트워크 문제 등) run_daily.py 전체가
  죽으면 안 된다 — sector_data.py처럼 실패를 조용히 로그로만 남기고
  넘어간다. 리포트·대시보드 생성 자체는 텔레그램과 무관하게 이미
  끝난 뒤에 호출되므로, 이 단계가 실패해도 잃는 건 "알림 하나"뿐이다.
"""
import os
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendDocument"


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


def send_pdf_report(pdf_path: str, caption: str) -> bool:
    """
    PDF 한 개를 텔레그램으로 보낸다. 성공하면 True, 실패해도 예외를
    던지지 않고 False를 반환한다(호출부에서 흐름을 막지 않기 위함).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[텔레그램] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 "
              ".env에 없어 전송을 건너뜁니다.")
        return False
    if not os.path.exists(pdf_path):
        print(f"[텔레그램] 파일을 찾을 수 없습니다: {pdf_path}")
        return False

    url = TELEGRAM_API.format(token=token)
    try:
        with open(pdf_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1024]},  # 캡션 길이 제한
                files={"document": (os.path.basename(pdf_path), f, "application/pdf")},
                timeout=30,
            )
        if resp.status_code == 200 and resp.json().get("ok"):
            print(f"[텔레그램] 전송 완료: {os.path.basename(pdf_path)}")
            return True
        else:
            print(f"[텔레그램] 전송 실패(HTTP {resp.status_code}): {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"[텔레그램] 전송 중 오류(리포트 생성 자체는 정상입니다): {str(e)[:200]}")
        return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("사용법: python3 send_telegram.py <PDF경로> [캡션]")
        sys.exit(1)
    path = sys.argv[1]
    cap = sys.argv[2] if len(sys.argv) > 2 else "SEPA 리포트 테스트 전송"
    ok = send_pdf_report(path, cap)
    sys.exit(0 if ok else 1)
