# -*- coding: utf-8 -*-
"""AI 요약 생성기 (GitHub Models, 무료).

원문 URL이 있는 자료(Bing/네이버 수집분)의 본문을 가져와 GitHub Models(gpt-4.1)로
3~4줄 요약 + 키워드 + 동대문 관점 코멘트를 생성, docs/data.json 에 저장한다.
구글 뉴스 경유 링크는 원문 접근이 불가하므로 대상에서 제외.

GITHUB_TOKEN 환경변수 필요 (GitHub Actions에서는 내장 토큰 사용 — 키/결제 불필요).
"""
import json
import os
import re
import ssl
import sys
import urllib.request
from html import unescape
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data.json"

PER_RUN = 30      # 실행 1회당 요약 대상 상한
PER_CALL = 5      # API 1회 호출당 기사 수 (레이트리밋 절약)
MODEL = "openai/gpt-4.1"
API = "https://models.github.ai/inference/chat/completions"

CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

PROMPT = """다음은 동대문 관련 수집 기사들이다. 각 기사를 팀 내부 공유용으로 정리하라.
각 기사마다:
- summary: 핵심 내용 3~4개 불릿 (각 한 문장, 사실 위주)
- keywords: 핵심 키워드 3~5개
- comment: 동대문(패션·상권·관광) 관점에서 왜 주목할 만한지 한 문장

JSON 배열로만 답하라: [{"id": <기사번호>, "summary": [...], "keywords": [...], "comment": "..."}]
"""


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


def extract_text(url):
    """기사 본문 텍스트 추출 (간이 휴리스틱)."""
    try:
        html = http_get(url).decode("utf-8", "ignore")
    except Exception:
        return ""
    desc = ""
    m = re.search(r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)', html)
    if m:
        desc = unescape(m.group(1))
    body = re.search(r"<article[^>]*>(.*?)</article>", html, re.S)
    text = body.group(1) if body else html
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return (desc + "\n" + text)[:2500]


def call_model(token, articles):
    lines = []
    for idx, (item, text) in enumerate(articles):
        lines.append(f"[기사 {idx}] 제목: {item['title']}\n본문: {text}\n")
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        **UA, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
        resp = json.loads(r.read())
    content = resp["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", content, re.S)
    return json.loads(m.group(0)) if m else []


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set — skipped (요약은 GitHub Actions에서 실행)")
        return

    data = json.loads(DATA.read_text(encoding="utf-8"))
    items = data["items"]
    targets = [i for i in items
               if not i.get("summary") and not i.get("sum_failed")
               and "news.google.com" not in i["link"]
               and i["kind"] == "뉴스"][:PER_RUN]
    print(f"targets: {len(targets)}")

    with_text = []
    for it in targets:
        text = extract_text(it["link"])
        if len(text) > 200:
            with_text.append((it, text))
        else:
            it["sum_failed"] = True  # 본문 추출 실패 — 재시도 안 함
    print(f"text extracted: {len(with_text)}")

    done = 0
    for i in range(0, len(with_text), PER_CALL):
        batch = with_text[i:i + PER_CALL]
        try:
            results = call_model(token, batch)
        except Exception as e:
            print(f"[ai] batch {i // PER_CALL}: FAILED {e}")
            continue
        for r in results:
            try:
                item = batch[int(r["id"])][0]
                item["summary"] = [s.strip() for s in r["summary"]][:4]
                item["keywords"] = [k.strip() for k in r["keywords"]][:5]
                item["comment"] = (r.get("comment") or "").strip()
                done += 1
            except Exception:
                pass

    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(1 for i in items if i.get("summary"))
    print(f"summarized {done} this run, {total} total")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
