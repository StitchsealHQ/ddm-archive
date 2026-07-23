-- ============================================================
-- 동대문 광장 스키마 v2 — 260723 (당근 스타일 개편)
-- SQL Editor에 전체 붙여넣고 Run 1회. 재실행 안전.
-- 변경: ① 글 제목 컬럼 추가 ② 주제(category)를 고정 4종 제약에서
--       길이 제약으로 완화 — 주제 목록은 프론트에서 관리
-- ============================================================

alter table public.plaza_posts
  add column if not exists title text;

do $$ begin
  alter table public.plaza_posts
    add constraint plaza_posts_title_len
    check (title is null or char_length(title) between 1 and 100);
exception when duplicate_object then null; end $$;

alter table public.plaza_posts
  drop constraint if exists plaza_posts_category_check;

do $$ begin
  alter table public.plaza_posts
    add constraint plaza_posts_category_len
    check (char_length(category) between 1 and 20);
exception when duplicate_object then null; end $$;
