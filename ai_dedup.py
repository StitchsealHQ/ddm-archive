# -*- coding: utf-8 -*-
"""AI 사건 클러스터링 중복 제거 + 관련성 필터 (GitHub Models, 무료).

1) 클러스터링: 단어 겹침 방식이 못 잡는 '같은 사건, 다른 문구' 헤드라인을
   gpt-4.1에 제목 목록을 보내 사건별로 묶고 대표 1건만 남긴다 (최근 7일 뉴스).
2) 관련성: 동대문(패션타운·시장·DDP·상권·관광)과 무관한 자료를 제거한다.
   판정은 경량 모델(gpt-4.1-mini)로, 한 번 판정한 자료는 rel_checked 마킹해 재판정 안 함.

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
REL_MODEL = "openai/gpt-4.1-mini"  # 관련성 판정은 쉬운 작업 — 무료 한도 넉넉한 경량 모델
API = "https://models.github.ai/inference/chat/completions"
WINDOW_DAYS = 7
MAX_TITLES = 250
REL_BATCH = 100

REL_PROMPT = """다음은 '동대문 아카이브'에 수집된 자료 목록이다. 각 항목은 "id: [카테고리] 제목 | 코멘트" 형식이다.
이 아카이브는 서울 동대문을 중심으로 생산·원부자재·제조·유통·소비·문화가 맞물린
'도심형 복합 산업 클러스터'를 넓게 다룬다. 수집 범위(모두 관련 있음):
- 중심 허브: 동대문 패션타운·도매시장·DDP·흥인지문·동대문역 일대
- 종로 라인: 종로3가~광장시장~신설동 풍물시장~청량리 경동시장
- 청계 라인: 청계천 일대 상가(세운상가·기계공구상가 등)와 재개발 이슈
- 을지로 라인: 을지로3~6가·신당동(힙당동)·서울중앙시장~왕십리 상권
- 글로벌 링크: 회현(남대문)·명동 관광·쇼핑 상권
- 북부(이화·혜화·돈암·보문)·남부(장충동·약수·금호) 인근 상권 이슈
- 완구/트렌드: 창신동 문구완구거리·동묘·동대문종합시장 부자재 상가,
  말랑이·왁뿌볼·슬랑이·키링·키캡키링 등 촉감완구/키덜트 트렌드
  (특정 지역 언급 없는 전국 단위 완구 트렌드 기사도 동대문 완구상권 연관 이슈로 관련 있음)
- 도매 생태계: 사입·사입삼촌, 의류 수출입·중국 무역(따이공 포함), 동대문발 물류,
  원단·부자재·봉제(창신동 등), 도매상 세금·과세 이슈
- 행정/정책: 종로구·서울 중구·성북구의 상권·전통시장·관광·패션산업 관련 정책·지원사업,
  동대문 일대 재개발·상가 부동산 시장 동향

명백히 무관한 항목의 id만 골라라. 무관 예:
- 연예인 근황·인사·부고, 위 구역 언급 없는 일반 경제·유통 기사
- 정치 기사 ('개혁신당' 등 정당의 '신당'은 신당동과 무관)
- 경동나비엔(보일러 기업)·경동제약 등 '경동' 사명 기업 기사
- 키보드 키캡·게이밍 기어 (완구 키링과 무관)
- 위 구역 밖 타지역(강남·홍대·성수 등) 단독 행사·맛집
- 부동산 매물·임대 광고, 무관 블로그 일상글
- 구청 일반 행정 (복지·환경·보건 등 상권과 무관한 소식, 타 지역 '중구'는 전부 무관)
- 위 구역 언급 없는 전국 단위 세금·부동산 일반 기사
- 커뮤니티 홍보·구인·사입대행 광고 글

주의 — 다음은 '관련 있음'이므로 제외하면 안 된다:
- 동대문 기반 기업·플랫폼 소식 (딜리셔스/신상마켓 등 동대문 도매 플랫폼, 두타 입점 브랜드)
- 동대문 패션산업에 영향을 주는 정책·단속 (의류 라벨갈이 단속, K-패션 지원 정책 등)
- DDP에서 열리는 행사·전시 (제목에 DDP가 없어도 DDP 개최면 관련)
제목만으로 판단이 불확실하면 관련 있는 것으로 간주하고 제외하지 마라
(관련 검색으로 수집된 자료라 본문에 관련 내용이 있을 수 있다).

JSON으로만 답하라: {"irrelevant": [id, id, ...]} (없으면 빈 배열)"""

CTX = ssl.create_default_context()

PROMPT = """다음은 최근 수집된 동대문 관련 뉴스 제목 목록이다.
'같은 사건·발표·행사'를 다룬 제목들을 묶어라. 표현이 달라도 실질적으로 같은 소식이면 같은 그룹이다.
특히 보도자료를 받아쓴 기사들(행사 개최, 신제품 출시, 단속 결과, 인사 방문 등)은
매체마다 제목을 완전히 다르게 뽑아도 같은 사건이면 반드시 묶어라.
단, 주제만 비슷한 서로 다른 기사(예: 서로 다른 맛집 소개)는 묶지 마라.

2개 이상 묶이는 그룹만, JSON 배열로만 답하라: [[id, id, ...], [id, ...]]
묶을 게 없으면 [] 로 답하라."""


def call_model(token, lines, model=None, system=None):
    body = json.dumps({
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": system or PROMPT},
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
    m = re.search(r"\[.*\]|\{.*\}", content, re.S)
    return json.loads(m.group(0)) if m else []


def filter_relevance(token, data):
    """동대문 무관 자료 제거 (뉴스·블로그, 미판정분만)."""
    items = data["items"]
    # 영상은 youtube.py가 판정하지만, AI 실패로 무판정 유입된 것은 여기서 재검사
    targets = [(idx, it) for idx, it in enumerate(items)
               if it["kind"] in ("뉴스", "블로그", "영상", "커뮤니티")
               and not it.get("rel_checked")]
    if not targets:
        print("[rel] nothing to check")
        return
    print(f"[rel] checking {len(targets)} items")
    drop = set()
    for i in range(0, len(targets), REL_BATCH):
        batch = targets[i:i + REL_BATCH]
        lines = [f"{idx}: [{'/'.join(it['tags'])}] {it['title']}"
                 + (f" | {it['comment']}" if it.get("comment") else "")
                 for idx, it in batch]
        try:
            result = call_model(token, lines, model=REL_MODEL, system=REL_PROMPT)
        except Exception as e:
            print(f"[rel] batch {i // REL_BATCH}: FAILED {e}")
            continue
        valid = {idx for idx, _ in batch}
        bad = {i for i in result.get("irrelevant", [])
               if isinstance(i, int) and i in valid} if isinstance(result, dict) else set()
        for idx, it in batch:
            if idx in bad:
                drop.add(idx)
            else:
                it["rel_checked"] = True
    if drop:
        removed = [items[i]["title"][:40] for i in sorted(drop)][:10]
        for t in removed:
            print(f"[rel] removed: {t}")
        data["items"] = [it for idx, it in enumerate(items) if idx not in drop]
    print(f"[rel] removed {len(drop)}, total {len(data['items'])}")


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set — skipped")
        return

    data = json.loads(DATA.read_text(encoding="utf-8"))
    cluster_events(token, data)
    filter_relevance(token, data)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def cluster_events(token, data):
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
    print(f"clusters: {len(clusters)}, removed {merged} duplicates, total {len(data['items'])}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
