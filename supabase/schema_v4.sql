-- ============================================================
-- 동대문 광장 스키마 v4 — 260723 (카카오 로그인 · 프로필)
-- SQL Editor에 전체 붙여넣고 Run 1회. 재실행 안전.
--
-- 함께 필요한 Supabase 대시보드 설정 (SQL 아님):
-- ① Authentication > Sign In / Providers > Kakao ON
--    (Client ID = 카카오 REST API 키, Secret = 카카오 Client Secret)
-- ② Authentication > URL Configuration > Redirect URLs 에
--    https://stitchsealhq.github.io/ddm-archive/plaza.html 추가
-- ③ Authentication > Sign In / Providers (또는 Settings) 에서
--    "Allow manual linking" ON — 익명 세션 → 카카오 승격(linkIdentity)에 필요
-- ============================================================

-- 프로필 (첫 카카오 로그인 시 닉네임 온보딩으로 생성)
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  nickname text not null check (char_length(nickname) between 2 and 20),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- 읽기는 공개(글 옆 닉네임 표시용), 쓰기는 본인 것만 (upsert = insert+update)
drop policy if exists "profiles read"   on public.profiles;
drop policy if exists "profiles insert" on public.profiles;
drop policy if exists "profiles update" on public.profiles;
create policy "profiles read"   on public.profiles for select using (true);
create policy "profiles insert" on public.profiles for insert to authenticated
  with check (auth.uid() = id);
create policy "profiles update" on public.profiles for update to authenticated
  using (auth.uid() = id) with check (auth.uid() = id);
