# -*- coding: utf-8 -*-
"""오늘의 동대문 장사운 생성 (GitHub Models, 무료).

매일 실행되며 data.json의 fortune 필드를 갱신한다 (같은 날짜면 건너뜀).
팀 아침 인사용 재미 콘텐츠 — 실제 조언이 아님을 프론트에 명시.
경량 모델(gpt-4.1-mini) 우선, 실패 시 gpt-4.1 폴백.

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
MODELS = ["openai/gpt-4.1-mini", "openai/gpt-4.1"]

CTX = ssl.create_default_context()
KST = timezone(timedelta(hours=9))
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

ZODIAC = ["쥐", "소", "호랑이", "토끼", "용", "뱀", "말", "양", "원숭이", "닭", "개", "돼지"]

PROMPT = """너는 동대문 시장 골목에서 오래 장사꾼들을 봐온 재치있는 이야기꾼이다.
오늘 날짜의 '오늘의 동대문 장사운'을 써라. 패션 유통 팀의 아침 인사용 재미 콘텐츠다.

- text: 공통 운세 2~3문장. 장사·사입·발주·거래처·신상 같은 동대문 상인 정서를 담아 밝고 유쾌하게.
  불안 조장·미신 강요 금지, 실제 재무·투자 조언처럼 들리는 표현 금지.
- color: 오늘의 럭키 컬러 (한글, 예: 버건디)
- item: 오늘의 럭키 아이템 (동대문에서 구할 수 있는 소품, 예: 체크 머플러)
- number: 1~99 사이 럭키 숫자
- zodiac: 12띠 각각의 오늘 운세 1~2문장. 띠마다 소재(사입/발주/거래처/신상/매대/손님/원단 등)와
  분위기를 다르게 변주하고, 공통 운세와 겹치지 않게. 부정 운세도 가볍고 귀엽게
  ("오후엔 계산기 한 번 더 두드려 보기" 정도), 겁주는 표현 금지.
- pool: 개인 운세 조합용 문장 풀. total(오늘의 총운)·biz(장사/재물운)·tip(오늘의 한마디) 각 10문장.
  서로 겹치지 않게 다양하게, 각 한 문장, 위와 같은 동대문 상인 정서·같은 금지 규칙.

JSON으로만 답하라:
{"text": "...", "color": "...", "item": "...", "number": 숫자,
 "zodiac": {"쥐": "...", "소": "...", "호랑이": "...", "토끼": "...", "용": "...", "뱀": "...",
            "말": "...", "양": "...", "원숭이": "...", "닭": "...", "개": "...", "돼지": "..."},
 "pool": {"total": ["...", 10개], "biz": ["...", 10개], "tip": ["...", 10개]}}"""


def call_model(token, model, user_content):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.9,
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "ddm-archive",
    })
    with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
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
    today = now.strftime("%Y-%m-%d")
    prev = data.get("fortune") or {}
    if prev.get("date") == today and prev.get("zodiac") and prev.get("pool"):
        print("already generated today — skipped")
        return

    user_content = f"오늘은 {today} ({WEEKDAYS[now.weekday()]}요일)이다."
    result = None
    for model in MODELS:
        try:
            result = call_model(token, model, user_content)
            print(f"model: {model}")
            break
        except Exception as e:
            print(f"[{model}] FAILED {e}")
    if not result or not str(result.get("text", "")).strip():
        print("no fortune generated — kept existing")
        return

    data["fortune"] = {
        "date": today,
        "text": str(result["text"]).strip(),
        "color": str(result.get("color", "")).strip(),
        "item": str(result.get("item", "")).strip(),
        "number": result.get("number", ""),
    }
    z = result.get("zodiac")
    if isinstance(z, dict):
        zodiac = {k: str(z[k]).strip() for k in ZODIAC if str(z.get(k, "")).strip()}
        if len(zodiac) == 12:
            data["fortune"]["zodiac"] = zodiac
        else:
            print(f"zodiac incomplete ({len(zodiac)}/12) — common only")
    p = result.get("pool")
    if isinstance(p, dict):
        pool = {k: [str(s).strip() for s in p.get(k, []) if str(s).strip()][:10]
                for k in ("total", "biz", "tip")}
        if all(len(v) >= 6 for v in pool.values()):  # 조합 다양성 최소선
            data["fortune"]["pool"] = pool
        else:
            print("pool incomplete — personal fortune skipped")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"fortune saved: {data['fortune']['text'][:40]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
