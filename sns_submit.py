# -*- coding: utf-8 -*-
"""SNS 게시물 제출 처리 (GitHub Issue 폼 → data.json 카드).

이슈 폼 본문(ISSUE_BODY)에서 링크·제목·분류·코멘트를 파싱하고,
og:title/og:image 추출을 시도해 'SNS' 종류로 추가한다.
인스타그램 등은 비로그인 접근이 막혀 og 추출이 실패할 수 있음 — 그 경우
제출자가 적은 제목을 쓰고, 둘 다 없으면 도메인 기반 기본 제목을 쓴다.

사람이 고른 자료이므로 AI 관련성 재검사는 하지 않는다 (rel_checked).
환경변수: ISSUE_BODY (필수)
출력: 성공 시 'ADDED: <제목>' / 실패 시 'ERROR: <사유>' (워크플로가 이슈 코멘트에 사용)
"""
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data.json"
CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
KST = timezone(timedelta(hours=9))

VALID_TAGS = {"패션", "도매시장", "DDP", "쇼핑/관광", "F&B", "기업/산업", "외국인",
              "완구/트렌드", "인근상권", "원부자재/제조", "상인/단체", "행정/정책"}

SOURCE_BY_DOMAIN = [
    ("instagram.com", "Instagram"), ("threads.", "Threads"),
    ("x.com", "X"), ("twitter.com", "X"), ("tiktok.com", "TikTok"),
    ("facebook.com", "Facebook"), ("youtube.com", "YouTube"), ("youtu.be", "YouTube"),
]


def parse_form(body):
    """이슈 폼 렌더링('### 라벨\\n\\n값') 파싱 → {라벨: 값}."""
    fields = {}
    for m in re.finditer(r"###\s*(.+?)\s*\n+([\s\S]*?)(?=\n###|\Z)", body):
        val = m.group(2).strip()
        if val == "_No response_":
            val = ""
        fields[m.group(1).strip()] = val
    return fields


def fetch_og(url):
    """og:title / og:image 추출 (실패해도 무방)."""
    title = image = ""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            html = r.read().decode("utf-8", "ignore")
        for prop, target in (("og:title", "title"), ("og:image", "image")):
            m = re.search(
                rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']+)',
                html) or re.search(
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{prop}',
                html)
            if m:
                v = unescape(m.group(1)).strip()
                if target == "title":
                    title = v
                elif v.startswith(("http", "//")):
                    image = "https:" + v if v.startswith("//") else v
    except Exception as e:
        print(f"[og] fetch failed: {e}", file=sys.stderr)
    return title, image


def main():
    body = os.environ.get("ISSUE_BODY", "")
    fields = parse_form(body)
    by_key = {k.split(" (")[0]: v for k, v in fields.items()}

    link = by_key.get("게시물 링크", "").strip()
    if not re.match(r"^https?://", link):
        print("ERROR: 유효한 링크가 아닙니다 (http/https URL 필요)")
        sys.exit(1)

    data = json.loads(DATA.read_text(encoding="utf-8"))
    if any(i["link"] == link for i in data.get("items", [])):
        print("ERROR: 이미 등록된 링크입니다")
        sys.exit(1)

    tag = by_key.get("분류", "").strip()
    if tag not in VALID_TAGS:
        tag = "패션"

    og_title, og_image = fetch_og(link)
    title = by_key.get("제목", "").strip() or og_title
    host = urllib.parse.urlparse(link).netloc.lower()
    source = next((name for dom, name in SOURCE_BY_DOMAIN if dom in host),
                  host.removeprefix("www."))
    if not title:
        title = f"{source} 게시물"

    rec = {
        "title": title[:150], "link": link,
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "source": source, "kind": "SNS", "tags": [tag],
        "og_checked": True, "rel_checked": True,
        "added": datetime.now(KST).strftime("%Y-%m-%d"),
    }
    if og_image:
        rec["thumbnail"] = og_image
    note = by_key.get("한줄 코멘트", "").strip()
    if note:
        rec["comment"] = note[:300]

    data["items"].insert(0, rec)
    data["items"].sort(key=lambda x: x.get("date", ""), reverse=True)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ADDED: {title}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
