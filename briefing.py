# -*- coding: utf-8 -*-
"""주간 AI 브리핑 생성 (GitHub Models, 무료).

최근 7일 자료에서 '이번 주 동대문 핵심 5가지'를 생성해 data.json의 briefing 필드에 저장.
매일 실행되지만 월요일(KST)이거나 브리핑이 없을 때만 생성 (BRIEF_FORCE=1로 강제).
gpt-4.1 사용, 레이트리밋(429) 시 gpt-4.1-mini 폴백.

GITHUB_TOKEN 환경변수 필요.
"""
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data.json"
API = "https://models.github.ai/inference/chat/completions"
MODELS = ["openai/gpt-4.1", "openai/gpt-4.1-mini"]
MAX_ITEMS = 120

CTX = ssl.create_default_context()
KST = timezone(timedelta(hours=9))

PROMPT = """다음은 지난 7일간 수집된 동대문 일대(패션타운·도매시장·DDP·인근상권·완구/트렌드·관광) 관련 자료 목록이다.
팀(패션 유통 비즈니스) 내부 공유용 주간 브리핑으로 '이번 주 핵심 5가지'를 뽑아라.

기준: 동대문 상권·패션산업에 영향이 큰 순. 같은 주제는 하나로 합쳐라.
각 항목: title(10자 내외 헤드라인), body(1~2문장, 팀 관점에서 왜 중요한지 포함), id(대표 자료 번호).

JSON으로만 답하라: {"points": [{"title": "...", "body": "...", "id": 번호}]}"""


def call_model(token, model, user_content):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "ddm-archive",
    })
    with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
        resp = json.loads(r.read())
    content = resp["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0)) if m else None


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set — skipped")
        return

    data = json.loads(DATA.read_text(encoding="utf-8"))
    now = datetime.now(KST)
    force = os.environ.get("BRIEF_FORCE") == "1"
    existing = data.get("briefing")
    if not force:
        if existing and now.weekday() != 0:
            print("not Monday, briefing exists — skipped")
            return
        if existing and existing.get("generated_at", "")[:10] == now.strftime("%Y-%m-%d"):
            print("already generated today — skipped")
            return

    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [(idx, it) for idx, it in enumerate(data["items"])
              if it.get("date", "") >= cutoff and it["kind"] in ("뉴스", "블로그")][:MAX_ITEMS]
    if len(recent) < 5:
        print("too few recent items — skipped")
        return

    lines = []
    for idx, it in recent:
        line = f"{idx}: [{'/'.join(it['tags'])}] {it['title']}"
        if it.get("summary"):
            line += f" — {it['summary'][0]}"
        lines.append(line)
    user_content = "\n".join(lines)
    print(f"briefing from {len(recent)} items")

    result = None
    for model in MODELS:
        try:
            result = call_model(token, model, user_content)
            print(f"model: {model}")
            break
        except Exception as e:
            print(f"[{model}] FAILED {e}")
    if not result or not result.get("points"):
        print("no briefing generated — kept existing")
        return

    valid = {idx for idx, _ in recent}
    points = []
    for p in result["points"][:5]:
        rec = {"title": str(p.get("title", "")).strip(),
               "body": str(p.get("body", "")).strip()}
        try:
            idx = int(p.get("id"))
            if idx in valid:
                rec["link"] = data["items"][idx]["link"]
        except Exception:
            pass
        if rec["title"] and rec["body"]:
            points.append(rec)
    if not points:
        print("empty points — kept existing")
        return

    start = now - timedelta(days=7)
    data["briefing"] = {
        "range": f"{start.month}/{start.day}~{now.month}/{now.day}",
        "generated_at": now.strftime("%Y-%m-%d"),
        "points": points,
    }
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"briefing saved: {len(points)} points")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
