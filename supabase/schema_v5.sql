-- ============================================================
-- 동대문 광장 스키마 v5 — 260723 (댓글 알림)
-- SQL Editor에 전체 붙여넣고 Run 1회. 재실행 안전.
-- ============================================================

-- ① 알림 읽음 시점 (기기 간 읽음 상태 동기화)
alter table public.profiles
  add column if not exists noti_seen_at timestamptz not null default now();

-- ② 프로필 사진 URL (사진 변경 기능용 — 컬럼만 선반영)
alter table public.profiles
  add column if not exists avatar_url text;

-- ③ 댓글 실시간 (새 댓글 알림 즉시 반영)
do $$ begin
  alter publication supabase_realtime add table public.plaza_comments;
exception when duplicate_object then null; end $$;
