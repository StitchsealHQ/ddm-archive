# -*- coding: utf-8 -*-
"""네이버 데이터랩 검색 트렌드 수집.

최근 6개월 주간 검색량(상대지수)을 4개 키워드 그룹으로 조회해 data.json의 trend 필드에 저장.
네이버 애플리케이션에 '데이터랩(검색어트렌드)' API가 등록되어 있어야 한다 (검색 API와 같은 키).

NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수 필요 (.env 자동 로드).
"""
import json
import os
import ssl
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data.json"

_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

GROUPS = [
    {"groupName": "동대문", "keywords": ["동대문", "동대문시장"]},
    {"groupName": "DDP", "keywords": ["DDP", "동대문디자인플라자"]},
    {"groupName": "동대문 맛집", "keywords": ["동대문 맛집", "동대문 닭한마리"]},
    {"groupName": "동대문 도매", "keywords": ["동대문 도매", "동대문 사입"]},
    {"groupName": "말랑이·왁뿌볼", "keywords": ["말랑이", "왁뿌볼"]},
]

CTX = ssl.create_default_context()


def main():
    cid = os.environ.get("NAVER_CLIENT_ID")
    sec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        print("no NAVER key — skipped")
        return

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=182)
    body = json.dumps({
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "timeUnit": "week", "keywordGroups": GROUPS,
    }).encode()
    req = urllib.request.Request("https://openapi.naver.com/v1/datalab/search",
        data=body, headers={
            "X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec,
            "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            result = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"datalab HTTP {e.code} — skipped (데이터랩 API 미등록이면 네이버 콘솔에서 추가)")
        return

    groups = [{"name": g["title"], "data": [{"p": d["period"], "v": d["ratio"]}
                                            for d in g["data"]]}
              for g in result.get("results", [])]
    if not groups:
        print("empty result — skipped")
        return

    data = json.loads(DATA.read_text(encoding="utf-8"))
    data["trend"] = {
        "start": start.isoformat(), "end": end.isoformat(),
        "unit": "week", "groups": groups,
    }
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"trend saved: {len(groups)} groups, {len(groups[0]['data'])} weeks")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
