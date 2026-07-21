# -*- coding: utf-8 -*-
"""유튜브 영상 수집기 (YouTube Data API v3 + GitHub Models).

collect.py의 쿼리로 최근 영상을 검색하고, 제목·채널·설명란을 AI(gpt-4.1)에 넣어
관련성 판정 + 요약(불릿·키워드·코멘트)을 생성해 '영상' 종류로 data.json에 추가한다.
자막(스크립트) 추출은 유튜브가 클라우드 IP를 차단해 크론에서 불안정하므로
설명란 기반으로 요약한다 (260721 결정). 설명란 원문은 저장하지 않는다(요약만).

환경변수: YOUTUBE_API_KEY (없으면 건너뜀), GITHUB_TOKEN (없으면 판정·요약 없이 수집만)
쿼터: 검색 100유닛/쿼리 × 17 + videos.list 1유닛 ≈ 1,700/일 (무료 한도 10,000)
"""
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collect import QUERIES, load_archived_keys

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data.json"
API = "https://www.googleapis.com/youtube/v3"
AI_API = "https://models.github.ai/inference/chat/completions"
AI_MODELS = ["openai/gpt-4.1", "openai/gpt-4.1-mini"]
LOOKBACK_DAYS = int(os.environ.get("YT_LOOKBACK_DAYS", "14"))
PER_QUERY = 10
AI_BATCH = 8

_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

CTX = ssl.create_default_context()
KST = timezone(timedelta(hours=9))

AI_PROMPT = """다음은 '동대문 아카이브'용으로 검색된 유튜브 영상 목록이다.
각 항목은 "번호: [카테고리] 제목 | 채널명 | 설명" 형식이다.
아카이브 범위: 서울 동대문 중심의 도심형 복합 산업 클러스터 — 동대문 패션타운·도매시장·DDP,
종로(광장시장~청량리 경동시장), 청계천 상가(세운상가 등), 을지로~신당동(힙당동)·중앙시장,
명동·남대문 관광쇼핑, 창신동 문구완구거리·동묘, 말랑이·왁뿌볼·슬랑이·키링 등 촉감완구/키덜트 트렌드.

각 영상에 대해:
- relevant: 위 범위와 관련 있으면 true. 무관 예: 정치, 게임/키보드 장비, 위 구역 밖 타지역 단독 콘텐츠,
  단순 브이로그(동대문 일대 방문이 핵심이 아닌 것). 불확실하면 true.
- relevant=true인 영상만: summary(핵심 내용 2~3개 불릿, 각 한 문장), keywords(3~5개),
  comment(동대문 일대 상권·트렌드 관점에서 왜 볼만한지 한 문장)

JSON 배열로만 답하라: [{"id": 번호, "relevant": true/false, "summary": [...], "keywords": [...], "comment": "..."}]"""


def http_json(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read())


def search_videos(key):
    """쿼리별 최근 영상 검색 → {video_id: {snippet 요약, tags}}"""
    published_after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}
    for query, tag in QUERIES:
        q = urllib.parse.quote(query)
        url = (f"{API}/search?part=snippet&type=video&q={q}&order=date"
               f"&publishedAfter={published_after}&maxResults={PER_QUERY}"
               f"&relevanceLanguage=ko&regionCode=KR&key={key}")
        try:
            data = http_json(url, timeout=20)
        except Exception as e:
            print(f"[yt] {query}: FAILED {e}")
            continue
        n = 0
        for it in data.get("items", []):
            vid = (it.get("id") or {}).get("videoId")
            if not vid:
                continue
            if vid in found:
                found[vid]["tags"].add(tag)
            else:
                found[vid] = {"tags": {tag}}
            n += 1
        print(f"[yt] {query}: {n}")
    return found


def fetch_details(key, ids):
    """videos.list로 전체 설명란·정확한 스니펫 확보 (50개 배치, 1유닛)."""
    out = {}
    for i in range(0, len(ids), 50):
        batch = ",".join(ids[i:i + 50])
        url = f"{API}/videos?part=snippet&id={batch}&key={key}"
        try:
            data = http_json(url, timeout=20)
        except Exception as e:
            print(f"[yt/details] FAILED {e}")
            continue
        for it in data.get("items", []):
            s = it["snippet"]
            thumbs = s.get("thumbnails", {})
            thumb = (thumbs.get("high") or thumbs.get("medium") or {}).get("url", "")
            out[it["id"]] = {
                "title": s.get("title", "").strip(),
                "channel": s.get("channelTitle", "").strip(),
                "date": s.get("publishedAt", "")[:10],
                "desc": s.get("description", "")[:600],
                "thumbnail": thumb,
            }
    return out


def ai_judge(token, videos):
    """배치로 관련성 판정 + 요약 생성. 실패 시 None (호출측에서 무판정 처리)."""
    results = {}
    entries = list(videos.items())
    for i in range(0, len(entries), AI_BATCH):
        batch = entries[i:i + AI_BATCH]
        lines = []
        for idx, (vid, v) in enumerate(batch, start=i):
            desc = re.sub(r"\s+", " ", v["desc"])[:300]
            lines.append(f"{idx}: [{'/'.join(sorted(v['tags']))}] {v['title']} | {v['channel']} | {desc}")
        payload = {
            "messages": [{"role": "system", "content": AI_PROMPT},
                         {"role": "user", "content": "\n".join(lines)}],
            "temperature": 0.2,
        }
        resp = None
        for model in AI_MODELS:
            body = json.dumps({**payload, "model": model}).encode()
            try:
                resp = http_json(AI_API, data=body, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json", "User-Agent": "ddm-archive"},
                    timeout=180)
                break
            except Exception as e:
                print(f"[yt/ai] {model} FAILED {e}")
        if not resp:
            continue
        content = resp["choices"][0]["message"]["content"]
        m = re.search(r"\[.*\]", content, re.S)
        if not m:
            continue
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            continue
        for p in parsed:
            try:
                idx = int(p.get("id"))
                vid = entries[idx][0] if 0 <= idx < len(entries) else None
            except Exception:
                continue
            if vid:
                results[vid] = p
    return results


def main():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY not set — skipped")
        return

    data = json.loads(DATA.read_text(encoding="utf-8"))
    existing_links = {i["link"] for i in data.get("items", [])}
    arch_links, _ = load_archived_keys()
    existing_links |= arch_links

    found = search_videos(key)
    new_ids = [vid for vid in found
               if f"https://www.youtube.com/watch?v={vid}" not in existing_links]
    if not new_ids:
        print("no new videos")
        return
    details = fetch_details(key, new_ids)
    videos = {vid: {**details[vid], "tags": found[vid]["tags"]}
              for vid in new_ids if vid in details and details[vid]["title"]}
    print(f"candidates: {len(videos)} new videos")

    token = os.environ.get("GITHUB_TOKEN")
    judged = ai_judge(token, videos) if token else {}
    if token and not judged:
        print("[yt/ai] all batches failed — adding without summary (next-day rel check)")

    today = datetime.now(KST).strftime("%Y-%m-%d")
    added = dropped = 0
    for vid, v in videos.items():
        j = judged.get(vid)
        if j and not j.get("relevant", True):
            dropped += 1
            continue
        rec = {
            "title": v["title"],
            "link": f"https://www.youtube.com/watch?v={vid}",
            "date": v["date"], "source": v["channel"] or "YouTube",
            "kind": "영상", "tags": sorted(v["tags"]),
            "og_checked": True, "added": today,
        }
        if v["thumbnail"]:
            rec["thumbnail"] = v["thumbnail"]
        if j and j.get("summary"):
            rec["summary"] = [str(s).strip() for s in j["summary"]][:4]
            rec["keywords"] = [str(k).strip() for k in j.get("keywords", [])][:5]
            if j.get("comment"):
                rec["comment"] = str(j["comment"]).strip()
            rec["rel_checked"] = True  # AI 판정 완료 — ai_dedup 재검사 불필요
        data["items"].append(rec)
        added += 1

    data["items"].sort(key=lambda x: x.get("date", ""), reverse=True)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"videos added: {added}, AI-dropped: {dropped}, total {len(data['items'])}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
