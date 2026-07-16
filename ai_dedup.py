# -*- coding: utf-8 -*-
"""AI 사건 클러스터링 중복 제거 (GitHub Models, 무료).

collect.py의 단어 겹침 방식이 못 잡는 '같은 사건, 다른 문구' 헤드라인을
gpt-4.1에 제목 목록을 보내 사건별로 묶고 대표 1건만 남긴다.
최근 7일 뉴스만 대상 (제목만 전송하므로 저비용, 실행당 API 1~2회).

GITHUB_TOKEN 환경변수 필요 (GitHub Actions 내장 토큰).
"""
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data.json"
MODEL = "openai/gpt-4.1"
API = "https://models.github.ai/inference/chat/completions"
WINDOW_DAYS = 7
MAX_TITLES = 250

CTX = ssl.create_default_context()

PROMPT = """다음은 최근 수집된 동대문 관련 뉴스 제목 목록이다.
'같은 사건·발표·행사'를 다룬 제목들을 묶어라. 표현이 달라도 실질적으로 같은 소식이면 같은 그룹이다.
확신이 없으면 묶지 마라 (잘못 묶는 것이 놓치는 것보다 나쁘다).

2개 이상 묶이는 그룹만, JSON 배열로만 답하라: [[id, id, ...], [id, ...]]
묶을 게 없으면 [] 로 답하라."""


def call_model(token, lines):
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "ddm-archive",
    })
    with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
        resp = json.loads(r.read())
    content = resp["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", content, re.S)
    return json.loads(m.group(0)) if m else []


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set — skipped")
        return

    data = json.loads(DATA.read_text(encoding="utf-8"))
    items = data["items"]
    cutoff = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    recent = [(idx, it) for idx, it in enumerate(items)
              if it["kind"] == "뉴스" and it.get("date", "") >= cutoff][:MAX_TITLES]
    if len(recent) < 2:
        print("nothing to cluster")
        return
    lines = [f"{idx}: {it['title']}" for idx, it in recent]
    print(f"clustering {len(recent)} recent news titles")

    try:
        clusters = call_model(token, lines)
    except Exception as e:
        print(f"[ai] FAILED {e} — no-op")
        return

    valid_ids = {idx for idx, _ in recent}
    drop = set()
    merged = 0
    for group in clusters:
        ids = [i for i in group if isinstance(i, int) and i in valid_ids and i not in drop]
        if len(ids) < 2:
            continue
        # 대표 선정: 요약 > 썸네일 > 원문 URL 직접
        def pref(idx):
            it = items[idx]
            return (bool(it.get("summary")), bool(it.get("thumbnail")),
                    "news.google.com" not in it["link"])
        ids.sort(key=pref, reverse=True)
        keep, rest = ids[0], ids[1:]
        for idx in rest:
            items[keep]["tags"] = sorted(set(items[keep]["tags"]) | set(items[idx]["tags"]))
            drop.add(idx)
        merged += len(rest)

    if drop:
        data["items"] = [it for idx, it in enumerate(items) if idx not in drop]
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"clusters: {len(clusters)}, removed {merged} duplicates, total {len(data['items'])}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
