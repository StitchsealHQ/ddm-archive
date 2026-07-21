# -*- coding: utf-8 -*-
"""오래된 자료를 연도별 파일로 분리 (data.json 무한 누적 방지).

수집일(added) 기준 KEEP_DAYS(기본 90일)가 지난 자료를 docs/data.json에서
docs/data-<수집연도>.json 으로 이동한다. 라이브 파일은 항상 최근 90일치만 유지되고,
사이트는 검색·북마크·딥링크 때 아카이브 파일을 필요 시에만 불러온다.

- added 가 없는 기존 자료는 오늘 날짜로 백필 (그날부터 90일 라이브 보장)
- 이동 기준: max(발행일, 수집일) < cutoff — 수집 후 90일간은 AI 요약·클러스터링
  대상으로 남도록 발행일이 오래된 블로그도 수집일 기준으로 유지
- 아카이브 병합은 링크 기준 (재수집돼도 요약 있는 쪽 유지, 태그는 합집합)
- data.json 의 archives 필드에 아카이브 파일 목록·건수를 기록 (사이트가 참조)

환경변수 ARCHIVE_KEEP_DAYS 로 보존 기간 조정 가능 (테스트용).
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
DATA = DOCS / "data.json"

KEEP_DAYS = int(os.environ.get("ARCHIVE_KEEP_DAYS", "90"))
KST = timezone(timedelta(hours=9))


def load_archive(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}


def merge_into(archive_items, moved):
    """링크 기준 병합. 요약 있는 쪽 우선, 태그 합집합."""
    by_link = {it["link"]: it for it in archive_items}
    for it in moved:
        prev = by_link.get(it["link"])
        if prev is None:
            by_link[it["link"]] = it
        else:
            tags = sorted(set(prev.get("tags", [])) | set(it.get("tags", [])))
            if it.get("summary") and not prev.get("summary"):
                by_link[it["link"]] = it
            by_link[it["link"]]["tags"] = tags
    out = list(by_link.values())
    out.sort(key=lambda x: x.get("date", ""), reverse=True)
    return out


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    items = data.get("items", [])
    today = datetime.now(KST).strftime("%Y-%m-%d")
    cutoff = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")

    backfilled = 0
    for it in items:
        if not it.get("added"):
            it["added"] = today
            backfilled += 1
    if backfilled:
        print(f"backfilled added={today} on {backfilled} items")

    keep, buckets = [], {}
    for it in items:
        ref = max(it.get("date") or "", it["added"])
        if ref < cutoff:
            year = it["added"][:4]
            buckets.setdefault(year, []).append(it)
        else:
            keep.append(it)

    for year, moved in sorted(buckets.items()):
        path = DOCS / f"data-{year}.json"
        arch = load_archive(path)
        before = len(arch.get("items", []))
        arch["year"] = year
        arch["items"] = merge_into(arch.get("items", []), moved)
        path.write_text(json.dumps(arch, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"data-{year}.json: +{len(moved)} moved "
              f"({before} -> {len(arch['items'])} items)")

    data["items"] = keep
    # 아카이브 목록 메타 (사이트가 lazy-load 대상·전체 건수 계산에 사용)
    archives = []
    for path in sorted(DOCS.glob("data-*.json"), reverse=True):
        m = re.fullmatch(r"data-(\d{4})\.json", path.name)
        if not m:
            continue
        archives.append({"file": path.name, "year": m.group(1),
                         "count": len(load_archive(path).get("items", []))})
    data["archives"] = archives
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    total_arch = sum(a["count"] for a in archives)
    print(f"live: {len(keep)} items (cutoff {cutoff}), archived total: {total_arch}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
