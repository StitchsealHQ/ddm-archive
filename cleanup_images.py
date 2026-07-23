# -*- coding: utf-8 -*-
"""광장 고아 이미지 정리 (Supabase Storage).

글이 삭제돼도 첨부 이미지는 스토리지에 남는다 (사용자에게 삭제 권한을 안 열어둔 구조).
매일 크론에서: 버킷 전체 목록 ↔ plaza_posts.image_url 대조 → 어느 글에서도
참조하지 않고 업로드 24시간이 지난 파일을 삭제한다 (작성 중 이미지 오삭제 방지).

환경변수:
  SUPABASE_SERVICE_KEY — 삭제 권한 키 (GitHub Secret). 없으면 건너뜀.
  DRY_RUN=1            — 삭제 없이 대상만 출력 (로컬 점검용, anon 키로도 가능)
"""
import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

SB_URL = "https://vwbhainvzncciyiruepd.supabase.co"
BUCKET = "plaza"
MIN_AGE_HOURS = 24

CTX = ssl.create_default_context()


def req(method, path, key, body=None):
    r = urllib.request.Request(
        SB_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30, context=CTX) as resp:
        return json.loads(resp.read() or "[]")


def list_dir(key, prefix):
    return req("POST", f"/storage/v1/object/list/{BUCKET}", key,
               {"prefix": prefix, "limit": 1000,
                "sortBy": {"column": "name", "order": "asc"}})


def main():
    dry = os.environ.get("DRY_RUN") == "1"
    key = os.environ.get("SUPABASE_SERVICE_KEY") or (
        os.environ.get("SUPABASE_ANON_KEY") if dry else None)
    if not key:
        print("SUPABASE_SERVICE_KEY not set — skipped")
        return

    # 글·프로필에서 참조 중인 이미지 경로 (프로필 아바타도 보호)
    marker = f"/object/public/{BUCKET}/"
    refs = set()
    for endpoint, col in (("plaza_posts", "image_url"), ("profiles", "avatar_url")):
        rows = req("GET", f"/rest/v1/{endpoint}?select={col}&{col}=not.is.null", key)
        refs |= {r[col].split(marker, 1)[1]
                 for r in rows if marker in (r.get(col) or "")}
    print(f"referenced: {len(refs)}")

    # 버킷 전체 파일 (1단계 폴더 구조: {uid}/{file})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MIN_AGE_HOURS)
    orphans, young = [], 0
    for entry in list_dir(key, ""):
        if entry.get("id"):          # 루트에 바로 있는 파일
            folders = [""]
            break
    else:
        folders = [e["name"] for e in list_dir(key, "") if not e.get("id")]
    for folder in folders:
        for f in list_dir(key, folder):
            if not f.get("id"):
                continue
            path = f"{folder}/{f['name']}" if folder else f["name"]
            if path in refs:
                continue
            created = f.get("created_at", "")
            try:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except Exception:
                ts = cutoff  # 시각 불명이면 삭제 대상 취급
            if ts > cutoff:
                young += 1
                continue
            orphans.append(path)

    print(f"orphans: {len(orphans)} (24h 미만 보류 {young})")
    for p in orphans[:20]:
        print("  -", p)
    if dry or not orphans:
        print("dry-run — no deletion" if dry else "nothing to delete")
        return

    req("DELETE", f"/storage/v1/object/{BUCKET}", key, {"prefixes": orphans})
    print(f"deleted {len(orphans)} orphan images")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
