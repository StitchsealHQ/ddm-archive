# -*- coding: utf-8 -*-
"""동대문 아카이브 수집기.

소스:
  1. Bing 뉴스 RSS (키 불필요) — 원문 URL 제공 → og:image 썸네일·AI 요약 가능
  2. 네이버 검색 API (뉴스/블로그/이미지) — NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
     환경변수가 있을 때만 동작. 없으면 건너뜀.

원문 접근이 불가능한 소스(구글 뉴스 리다이렉트, MSN)는 AI 요약을 만들 수 없어 제외.
결과는 docs/data.json 에 누적 저장 (링크·제목 기준 중복 제거).
"""
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "data.json"

# .env 로드 (배포 시 이 파일은 커밋 금지 — Secrets 사용)
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

QUERIES = [
    ("동대문 패션", "패션"),
    ("동대문시장 도매", "도매시장"),
    ("동대문디자인플라자 DDP", "DDP"),
    ("동대문 쇼핑", "쇼핑/관광"),
    ("동대문 맛집", "F&B"),
    ("동대문 기업", "기업/산업"),
    ("동대문 외국인", "외국인"),
    # 완구/트렌드 — 창신동 문구완구거리·동대문종합시장 부자재, 말랑이·왁뿌볼 등 촉감완구 (260721 DRY-RUN 선별)
    ("동대문 완구거리", "완구/트렌드"),
    ("창신동 완구", "완구/트렌드"),
    ("말랑이 도매", "완구/트렌드"),
    ("동대문 키링", "완구/트렌드"),
    # 인근상권 — 재규정 동대문(도심형 복합 산업 클러스터) 구역: 종로·청계·을지로·신당·명동 라인
    ("광장시장", "인근상권"),
    ("경동시장", "인근상권"),
    ("청계천 상가", "인근상권"),
    ("을지로 상권", "인근상권"),
    ("힙당동", "인근상권"),
    ("명동 쇼핑", "인근상권"),
]

OG_ENRICH_LIMIT = 60  # 실행 1회당 og:image 조회 상한

CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


def strip_tags(s):
    return unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def norm_title(title, source=""):
    """중복 판정용 제목 정규화 (구글 제목의 ' - 매체명' 접미 제거)."""
    t = title
    if source and t.endswith(f" - {source}"):
        t = t[: -len(f" - {source}")]
    t = re.sub(r"\s*-\s*[^-]{1,25}$", "", t) if " - " in t else t
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", t).lower()


def parse_date(s):
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def title_tokens(title, source=""):
    """유사 판정용 토큰 집합 (매체명 접미 제거, 구분자로 복합어 분리)."""
    t = title
    if " - " in t:
        t = re.sub(r"\s*-\s*[^-]{1,25}$", "", t)
    # 특수문자를 공백으로 치환해 '공예·한복' 같은 복합어를 분리
    t = re.sub(r"[^0-9a-zA-Z가-힣]", " ", t)
    toks = set()
    for w in t.split():
        w = w.lower().replace("동대문디자인플라자", "ddp")  # 동의어 통일
        # 말단 조사 제거 (DDP에서→DDP)
        m = re.match(r"^(.{2,}?)(에서|에게|으로|부터|까지|서|는|은|이|가|을|를|의|와|과|로|에|도)$", w)
        if m:
            w = m.group(1)
        if w:
            toks.add(w)
    return toks


def shared_tokens(a, b):
    """두 토큰 집합의 공유 토큰 수 (접두 일치 허용: 세계유산위↔세계유산위원회)."""
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    used, n = set(), 0
    for x in small:
        for y in big:
            if y in used:
                continue
            if x == y or (len(x) >= 2 and len(y) >= 2
                          and (x.startswith(y) or y.startswith(x))):
                used.add(y)
                n += 1
                break
    return n


REL = re.compile(r"동대문|DDP|디디피|두타|밀리오레|평화시장|창신동", re.I)

# 광역 쿼리 태그의 앵커어 — AI 관련성 필터가 이미지는 검사하지 않으므로,
# 이 태그의 이미지 항목은 제목에 앵커어가 있어야만 유지 (260721 DRY-RUN: 무필터 이미지 노이즈 확인)
AREA_ANCHOR = {
    "완구/트렌드": re.compile(
        r"동대문|창신동|동묘|완구|문구|말랑이|왁뿌볼|슬랑이|키링|키캡|촉감|키덜트|장난감|피젯", re.I),
    "인근상권": re.compile(
        r"동대문|광장시장|경동시장|청량리|청계|세운|을지로|신당|힙당동|중앙시장|명동|왕십리|종로|전통시장", re.I),
}


def enforce_relevance(items):
    """규칙 기반 관련성 강제 (AI 필터가 못 미치는 영역 보완).

    - 기업/산업 태그: '동대문 기업' 검색이 무관 기업 기사를 끌고 오므로 제목에 동대문 언급 필수
    - 완구/트렌드·인근상권 이미지: 광역 쿼리라 무관 이미지가 섞이는데 AI 필터는
      뉴스·블로그만 검사하므로 제목 앵커어 필수
    태그 제거 후 남는 태그가 없으면 항목 자체를 제외.
    """
    out = []
    for it in items:
        if "기업/산업" in it["tags"] and not REL.search(it["title"]):
            it["tags"] = [t for t in it["tags"] if t != "기업/산업"]
        if it["kind"] == "이미지":
            it["tags"] = [t for t in it["tags"]
                          if t not in AREA_ANCHOR or AREA_ANCHOR[t].search(it["title"])]
        if not it["tags"]:
            continue
        out.append(it)
    return out


def days_between(d1, d2):
    if not d1 or not d2:
        return 0  # 날짜 없으면 날짜 조건은 통과
    from datetime import date
    a = date(*map(int, d1.split("-")))
    b = date(*map(int, d2.split("-")))
    return abs((a - b).days)


def dedup_similar(items):
    """같은 사건을 다룬 유사 제목 기사/블로그를 대표 1건으로 병합.

    기준: 공유 토큰 ≥3개(짧은 제목은 ≥2) 그리고 포함계수 ≥0.6, 발행일 5일 이내.
    뉴스·블로그만 대상 (이미지는 각각이 콘텐츠라 유지).
    대표 선정: 요약 있음 > 썸네일 있음 > 원문 URL 직접 > 기존 순서.
    """
    def pref(i):
        return (bool(i.get("summary")), bool(i.get("thumbnail")),
                "news.google.com" not in i["link"])
    ordered = sorted(items, key=pref, reverse=True)
    kept, passthrough = [], []
    for it in ordered:
        if it["kind"] not in ("뉴스", "블로그"):
            passthrough.append(it)
            continue
        toks = title_tokens(it["title"], it.get("source", ""))
        # 뉴스는 통신사발 동일 사건 변주가 많아 더 공격적으로(0.5), 블로그는 보수적으로(0.6)
        threshold = 0.5 if it["kind"] == "뉴스" else 0.6
        dup = None
        for k, ktoks in kept:
            if k["kind"] != it["kind"]:
                continue
            shared = shared_tokens(toks, ktoks)
            need = 2 if min(len(toks), len(ktoks)) <= 3 else 3
            if shared < need:
                continue
            if shared / max(1, min(len(toks), len(ktoks))) < threshold:
                continue
            if days_between(it.get("date"), k.get("date")) > 5:
                continue
            dup = k
            break
        if dup:
            dup["tags"] = sorted(set(dup["tags"]) | set(it["tags"]))
        else:
            kept.append((it, toks))
    return [k for k, _ in kept] + passthrough


def collect_bing_news():
    items = []
    for query, tag in QUERIES:
        q = urllib.parse.quote(query)
        url = f"https://www.bing.com/news/search?q={q}&format=rss&setmkt=ko-KR&count=30"
        try:
            xml = http_get(url).decode("utf-8", "ignore")
        except Exception as e:
            print(f"[bing] {query}: FAILED {e}")
            continue
        n = 0
        for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
            def f(tagname):
                m = re.search(rf"<{tagname}>(.*?)</{tagname}>", block, re.S)
                return m.group(1).strip() if m else ""
            title = strip_tags(f("title"))
            raw_link = unescape(f("link"))
            m = re.search(r"[?&]url=([^&]+)", raw_link)
            link = urllib.parse.unquote(m.group(1)) if m else raw_link
            if not title or not link:
                continue
            if "msn.com" in link:  # JS 렌더링이라 본문 추출·요약 불가
                continue
            img = unescape(f("News:Image"))
            rec = {
                "title": title, "link": link,
                "date": parse_date(f("pubDate")),
                "source": strip_tags(f("News:Source")) or "Bing News",
                "kind": "뉴스", "tags": [tag],
            }
            if img:
                rec["thumbnail"] = img.replace("http://", "https://")
            items.append(rec)
            n += 1
        print(f"[bing] {query}: {n}")
    return items


def collect_naver():
    cid = os.environ.get("NAVER_CLIENT_ID")
    sec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        print("[naver] no API key, skipped")
        return []
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec}
    endpoints = [("news", "뉴스"), ("blog", "블로그"), ("image", "이미지")]
    items = []
    for query, tag in QUERIES:
        q = urllib.parse.quote(query)
        for ep, kind in endpoints:
            sort = "" if ep == "image" else "&sort=date"
            url = f"https://openapi.naver.com/v1/search/{ep}.json?query={q}&display=30{sort}"
            try:
                data = json.loads(http_get(url, headers))
            except Exception as e:
                print(f"[naver/{ep}] {query}: FAILED {e}")
                continue
            n = 0
            for it in data.get("items", []):
                date = ""
                if it.get("pubDate"):
                    date = parse_date(it["pubDate"])
                elif it.get("postdate"):
                    d = it["postdate"]
                    date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                rec = {
                    "title": strip_tags(it.get("title", "")),
                    "link": it.get("originallink") or it.get("link", ""),
                    "date": date,
                    "source": strip_tags(it.get("bloggername", "")) or "네이버",
                    "kind": kind, "tags": [tag],
                }
                if kind == "이미지":
                    rec["thumbnail"] = it.get("thumbnail", "")
                    rec["source"] = "네이버 이미지"
                if rec["title"] and rec["link"]:
                    items.append(rec)
                    n += 1
            print(f"[naver/{ep}] {query}: {n}")
    return items


def fetch_og_image(item):
    """원문 페이지에서 og:image 추출 (구글 리다이렉트 링크는 원문 접근 불가라 제외)."""
    try:
        html = http_get(item["link"], timeout=12).decode("utf-8", "ignore")
        m = re.search(
            r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            html) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image',
            html)
        if m:
            url = unescape(m.group(1)).strip()
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http"):
                item["thumbnail"] = url
    except Exception:
        pass
    item["og_checked"] = True
    return item


def enrich_thumbnails(items):
    targets = [i for i in items
               if not i.get("thumbnail") and not i.get("og_checked")
               and "news.google.com" not in i["link"]][:OG_ENRICH_LIMIT]
    if not targets:
        print("[og] nothing to enrich")
        return
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(fetch_og_image, targets))
    got = sum(1 for i in targets if i.get("thumbnail"))
    print(f"[og] enriched {got}/{len(targets)}")


def load_archived_keys():
    """연도별 아카이브 파일의 링크·정규화 제목 집합 (재수집 방지용)."""
    links, titles = set(), set()
    for p in (ROOT / "docs").glob("data-*.json"):
        try:
            arch = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in arch.get("items", []):
            links.add(it["link"])
            titles.add(it["kind"] + norm_title(it["title"], it.get("source", "")))
    return links, titles


def main():
    data = {}
    if OUT.exists():
        data = json.loads(OUT.read_text(encoding="utf-8"))
    existing = data.get("items", [])

    fresh = collect_bing_news() + collect_naver()

    # 아카이브로 이동한 자료는 다시 추가하지 않음 (이미지 API 등이 옛 자료를 재반환)
    arch_links, arch_titles = load_archived_keys()
    if arch_links:
        before = len(fresh)
        fresh = [it for it in fresh
                 if it["link"] not in arch_links
                 and it["kind"] + norm_title(it["title"], it.get("source", ""))
                 not in arch_titles]
        if before != len(fresh):
            print(f"[archive] skipped {before - len(fresh)} already-archived items")

    # 수집일 스탬프 (archive.py가 90일 보존 판정에 사용)
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    for it in fresh:
        it["added"] = today

    # 1차: 링크 기준 병합, 2차: 정규화 제목 기준(썸네일 있는 쪽 우선)
    by_link = {}
    for it in existing + fresh:
        key = it["link"]
        if key in by_link:
            by_link[key]["tags"] = sorted(set(by_link[key]["tags"]) | set(it["tags"]))
        else:
            by_link[key] = it
    by_title = {}
    for it in by_link.values():
        key = it["kind"] + norm_title(it["title"], it.get("source", ""))
        prev = by_title.get(key)
        if prev is None:
            by_title[key] = it
        else:
            prev["tags"] = sorted(set(prev["tags"]) | set(it["tags"]))
            if it.get("thumbnail") and not prev.get("thumbnail"):
                prev["thumbnail"] = it["thumbnail"]
                prev["link"] = it["link"]
    items = enforce_relevance(dedup_similar(list(by_title.values())))
    items.sort(key=lambda x: x.get("date", ""), reverse=True)

    enrich_thumbnails(items)

    # 다른 필드(trend·briefing·archives)는 보존하고 items만 갱신
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["items"] = items
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    thumbs = sum(1 for i in items if i.get("thumbnail"))
    print(f"total: {len(items)} items ({thumbs} with thumbnail) -> {OUT}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
