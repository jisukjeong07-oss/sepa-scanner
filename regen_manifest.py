# -*- coding: utf-8 -*-
"""
history/ 폴더의 날짜 목록(manifest.json)만 다시 만드는 독립 스크립트.

make_dashboard.py 에도 같은 기능(--regen-manifest)이 있지만, 그 파일은 맨 위에서
pandas 등을 불러오기 때문에 실행하려면 무거운 라이브러리 설치가 필요하다.
배포 전용 워크플로는 "몇 초 만에 끝나는" 것이 목적이라, 표준 라이브러리만
쓰는 이 파일을 따로 둔다. 설치 단계 없이 바로 실행된다.

하는 일 두 가지:
  1) 맥에서 만들어져 이름이 깨진(NFD) 한글 파일명을 NFC로 바로잡는다.
     - 리눅스/웹은 NFC를 쓰므로, NFD 파일명은 웹에서 404가 난다.
  2) 폴더에 실제로 존재하는 파일 기준으로 manifest.json 을 새로 쓴다.

사용법:
    python3 regen_manifest.py            # 기본: ./history
    python3 regen_manifest.py --dir 경로
"""

import os
import re
import glob
import json
import argparse
import datetime as dt
import unicodedata

BASE = os.path.dirname(os.path.abspath(__file__))
SESS_LABEL = {"AM": "장전", "PM": "장마감", "MANUAL": "수동", "HIST": "소급"}
RANK = {"PM": 2, "MANUAL": 1, "HIST": 1, "AM": 0}


def _entry(fname: str):
    m = re.search(r"(\d{8})(?:_(AM|PM|MANUAL|HIST))?\.html$", fname)
    if not m:
        return None
    key, session = m.group(1), (m.group(2) or "MANUAL")
    try:
        base_label = dt.datetime.strptime(key, "%Y%m%d").strftime("%Y-%m-%d (%a)")
    except ValueError:
        base_label = key
    return {"file": fname, "key": key, "session": session,
           "label": f"{base_label} · {SESS_LABEL.get(session, session)}"}


def regen(hist_dir: str) -> int:
    if not os.path.isdir(hist_dir):
        print(f"[안내] 폴더가 없습니다: {hist_dir}")
        return 0

    days, seen, fixed = [], set(), 0

    for path in sorted(glob.glob(os.path.join(hist_dir, "*.html"))):
        raw = os.path.basename(path)
        nfc = unicodedata.normalize("NFC", raw)
        if not nfc.startswith("SEPA대시보드_"):
            continue

        if raw != nfc:
            target = os.path.join(hist_dir, nfc)
            try:
                if os.path.exists(target):
                    os.remove(path)   # 올바른 이름이 이미 있으면 깨진 쪽을 버린다
                    print(f"  [정규화] 중복 제거: {raw}")
                else:
                    os.rename(path, target)
                    print(f"  [정규화] 파일명 수정: {raw} → {nfc}")
                fixed += 1
            except OSError as e:
                print(f"  [경고] 정규화 실패({raw}): {e}")

        if nfc in seen:
            continue
        e = _entry(nfc)
        if e:
            seen.add(nfc)
            days.append(e)

    days.sort(key=lambda d: (d["key"], RANK.get(d["session"], 0)), reverse=True)

    out = os.path.join(hist_dir, "manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(days, f, ensure_ascii=False)

    print(f"매니페스트 재생성: {out} ({len(days)}개 항목, 파일명 수정 {fixed}건)")
    for d in days:
        print(f"   - {d['label']}  ({d['file']})")
    return len(days)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(BASE, "history"))
    a = ap.parse_args()
    regen(a.dir)
