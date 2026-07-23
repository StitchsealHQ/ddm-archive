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

# 채널 모니터링 — 검색이 못 잡는 채널의 업로드를 직접 훑어 AI가 엄선 (260723)
# 업로드 재생목록 조회는 1유닛/페이지 (search의 1/100)
CHANNELS = [("UC7uDyFIqExDnfXAIZqumFrQ", "셜록현준")]
CH_SEED = 400         # 최초 스캔 시 검토할 업로드 수 (260723 조사: 전 346편 중 관련작은 22~24년에 분포)
CH_AI_BATCH = 20      # 채널 판정 배치 (제목+설명뿐이라 크게 묶어 호출 수 절약)
CH_SEEN_CAP = 800     # 판정 이력(ch_seen) 보존 상한

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
  아파트 분양·부동산 재테크·일반 경제 콘텐츠, 단순 브이로그(동대문 일대 방문이 핵심이 아닌 것). 불확실하면 true.
- relevant=true인 영상만: summary(핵심 내용 2~3개 불릿, 각 한 문장), keywords(3~5개),
  comment(동대문 일대 상권·트렌드 관점에서 왜 볼만한지 한 문장)

JSON 배열로만 답하라: [{"id": 번호, "relevant": true/false, "summary": [...], "keywords": [...], "comment": "..."}]"""

# 채널 스캔용 — 건축·도시 일반 채널이라 기본값을 '무관'으로 두는 엄격 판정
CH_PROMPT = """다음은 유튜브 채널 '{name}'의 최근 영상 목록이다. 각 항목은 "번호: 제목 | 설명" 형식이다.
이 채널은 건축·도시 콘텐츠 전반을 다루므로 대부분은 '동대문 아카이브'와 무관하다.
아카이브 범위와 명확히 관련된 영상만 골라라. 범위: 서울 동대문 패션타운·도매시장·DDP·흥인지문,
종로(광장시장~경동시장), 청계천 상가(세운상가 등), 을지로~신당동·중앙시장, 명동·남대문,
창신동(봉제·문구완구), 서울 도심 전통시장·상권·재개발.

각 영상에 대해:
- relevant: 위 장소·주제를 영상이 실제로 다루는 경우만 true.
  서울 일반론·타지역·해외·건축 이론 일반은 false. 불확실하면 false.
- relevant=true인 영상만: tag(다음 중 정확히 하나: {tags}),
  summary(핵심 2~3개 불릿, 각 한 문장), keywords(3~5개),
  comment(동대문 일대 상권·트렌드 관점에서 왜 볼만한지 한 문장)

JSON 배열로만 답하라: [{{"id": 번호, "relevant": true/false, "tag": "...", "summary": [...], "keywords": [...], "comment": "..."}}]"""


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


def channel_uploads(key, cid, limit):
    """채널 업로드 재생목록의 최근 영상 [(video_id, date), ...] (최신순)."""
    playlist = "UU" + cid[2:]
    vids, page = [], ""
    while len(vids) < limit:
        url = (f"{API}/playlistItems?part=snippet&playlistId={playlist}"
               f"&maxResults=50&key={key}" + (f"&pageToken={page}" if page else ""))
        data = http_json(url, timeout=20)
        for it in data.get("items", []):
            s = it["snippet"]
            vid = (s.get("resourceId") or {}).get("videoId")
            if vid:
                vids.append((vid, s.get("publishedAt", "")[:10]))
        page = data.get("nextPageToken", "")
        if not page:
            break
    return vids[:limit]


def ai_judge_channel(token, name, videos):
    """채널 영상 엄격 판정 (+태그 선택). 판정 성공한 영상만 결과에 포함."""
    tags = ", ".join(sorted({t for _, t in QUERIES}))
    system = CH_PROMPT.format(name=name, tags=tags)
    results = {}
    entries = list(videos.items())
    for i in range(0, len(entries), CH_AI_BATCH):
        batch = entries[i:i + CH_AI_BATCH]
        lines = []
        for idx, (vid, v) in enumerate(batch, start=i):
            desc = re.sub(r"\s+", " ", v["desc"])[:300]
            lines.append(f"{idx}: {v['title']} | {desc}")
        payload = {
            "messages": [{"role": "system", "content": system},
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
                print(f"[yt/ch-ai] {model} FAILED {e}")
        if not resp:
            continue
        m = re.search(r"\[.*\]", resp["choices"][0]["message"]["content"], re.S)
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


def collect_channels(key, token, data, existing_links):
    """채널 업로드를 AI로 엄선해 추가. 판정 이력은 data['ch_seen']에 저장해 재판정 방지."""
    if not token:
        print("[yt/ch] no GITHUB_TOKEN — channel scan skipped (판정 없이는 추가 안 함)")
        return 0
    seen = data.setdefault("ch_seen", {})
    valid_tags = {t for _, t in QUERIES}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)) \
        .strftime("%Y-%m-%d")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    added = 0
    for cid, name in CHANNELS:
        judged_ids = set(seen.get(cid, []))
        try:
            uploads = channel_uploads(key, cid, CH_SEED)
        except Exception as e:
            print(f"[yt/ch] {name}: FAILED {e}")
            continue
        cands = []
        for vid, pub in uploads:
            if vid in judged_ids:
                continue
            if f"https://www.youtube.com/watch?v={vid}" in existing_links:
                continue
            # 최초 스캔(판정 이력 없음)은 시드 전체, 이후엔 최근 업로드만
            if judged_ids and pub < cutoff:
                continue
            cands.append(vid)
        if not cands:
            print(f"[yt/ch] {name}: no new uploads")
            continue
        details = fetch_details(key, cands)
        videos = {vid: details[vid] for vid in cands
                  if vid in details and details[vid]["title"]}
        print(f"[yt/ch] {name}: judging {len(videos)} videos")
        judged = ai_judge_channel(token, name, videos)
        for vid, j in judged.items():
            judged_ids.add(vid)  # 판정 성공분만 이력에 — 실패분은 다음 실행 때 재시도
            if not j.get("relevant"):
                continue
            v = videos[vid]
            tag = j.get("tag") if j.get("tag") in valid_tags else "인근상권"
            rec = {
                "title": v["title"],
                "link": f"https://www.youtube.com/watch?v={vid}",
                "date": v["date"], "source": v["channel"] or name,
                "kind": "영상", "tags": [tag],
                "og_checked": True, "rel_checked": True, "added": today,
            }
            if v["thumbnail"]:
                rec["thumbnail"] = v["thumbnail"]
            if j.get("summary"):
                rec["summary"] = [str(s).strip() for s in j["summary"]][:4]
                rec["keywords"] = [str(k).strip() for k in j.get("keywords", [])][:5]
            if j.get("comment"):
                rec["comment"] = str(j["comment"]).strip()
            data["items"].append(rec)
            added += 1
        seen[cid] = list(judged_ids)[-CH_SEEN_CAP:]
        print(f"[yt/ch] {name}: added {added}, judged {len(judged)}")
    return added


def main():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY not set — skipped")
        return

    data = json.loads(DATA.read_text(encoding="utf-8"))
    existing_links = {i["link"] for i in data.get("items", [])}
    arch_links, _ = load_archived_keys()
    existing_links |= arch_links

    token = os.environ.get("GITHUB_TOKEN")

    found = search_videos(key)
    new_ids = [vid for vid in found
               if f"https://www.youtube.com/watch?v={vid}" not in existing_links]
    added = dropped = 0
    if new_ids:
        details = fetch_details(key, new_ids)
        videos = {vid: {**details[vid], "tags": found[vid]["tags"]}
                  for vid in new_ids if vid in details and details[vid]["title"]}
        print(f"candidates: {len(videos)} new videos")

        judged = ai_judge(token, videos) if token else {}
        if token and not judged:
            print("[yt/ai] all batches failed — adding without summary (next-day rel check)")

        today = datetime.now(KST).strftime("%Y-%m-%d")
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
    else:
        print("no new videos from search")

    ch_added = collect_channels(key, token, data,
                                existing_links | {i["link"] for i in data["items"]})

    if added or dropped or ch_added:
        data["items"].sort(key=lambda x: x.get("date", ""), reverse=True)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"videos added: {added} (+{ch_added} channel), AI-dropped: {dropped}, "
          f"total {len(data['items'])}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
