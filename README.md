# 동대문 아카이브

동대문 관련 뉴스·블로그·이미지 자료를 자동 수집해 카드뉴스형 대시보드로 보여주는 팀 내부 공유용 사이트.

- **대시보드**: GitHub Pages (`docs/`) — 검색, 카테고리 7종 필터, AI 요약 모달
- **수집**: 매일 KST 08:00 GitHub Actions
  1. `collect.py` — 구글 뉴스 RSS + Bing 뉴스 RSS(og:image 썸네일) + 네이버 검색 API(뉴스/블로그/이미지), 유사 제목 중복 병합
  2. `ai_dedup.py` — GitHub Models(gpt-4.1)로 같은 사건 다른 문구 헤드라인 클러스터링
  3. `summarize.py` — 원문 URL 있는 뉴스에 문서형 AI 요약(핵심 불릿·동대문 관점·키워드) 생성
- **데이터**: `docs/data.json` 누적 (제목+링크+출처만 저장, 본문·이미지 파일 미저장)

## Secrets

| 이름 | 용도 |
|---|---|
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 네이버 검색 API |

AI(요약·클러스터링)는 Actions 내장 `GITHUB_TOKEN`으로 GitHub Models를 호출하므로 별도 키가 필요 없다.

## 로컬 실행

```
# .env 파일에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 작성 후
python collect.py
python -m http.server 8137 --directory docs
```
