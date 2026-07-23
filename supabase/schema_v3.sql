-- ============================================================
-- 동대문 광장 스키마 v3 — 260723 (조회수·투표·신고·관리자 삭제)
-- SQL Editor에 전체 붙여넣고 Run 1회. 재실행 안전.
--
-- ⚠️ 실행 후 관리자 키 등록 1줄을 "직접 타이핑"으로 추가 실행:
--    insert into public.plaza_admin (key) values ('원하는-비밀키');
--    (이 파일은 공개 저장소에 올라가므로 키를 파일에 적지 않는다)
-- ============================================================

-- ① 조회수
alter table public.plaza_posts
  add column if not exists views integer not null default 0;

create or replace function public.plaza_view(pid uuid) returns void
language sql security definer set search_path = public as
$$ update plaza_posts set views = views + 1 where id = pid; $$;

-- ② 투표
alter table public.plaza_posts
  add column if not exists poll_options text[];

do $$ begin
  alter table public.plaza_posts
    add constraint plaza_posts_poll_len
    check (poll_options is null or array_length(poll_options, 1) between 2 and 4);
exception when duplicate_object then null; end $$;

create table if not exists public.plaza_poll_votes (
  post_id uuid not null references public.plaza_posts (id) on delete cascade,
  author uuid not null default auth.uid(),
  option_idx smallint not null check (option_idx between 0 and 3),
  created_at timestamptz not null default now(),
  primary key (post_id, author)
);
alter table public.plaza_poll_votes enable row level security;

drop policy if exists "votes read"   on public.plaza_poll_votes;
drop policy if exists "votes insert" on public.plaza_poll_votes;
drop policy if exists "votes update" on public.plaza_poll_votes;
create policy "votes read"   on public.plaza_poll_votes for select using (true);
create policy "votes insert" on public.plaza_poll_votes for insert to authenticated
  with check (auth.uid() = author);
create policy "votes update" on public.plaza_poll_votes for update to authenticated
  using (auth.uid() = author) with check (auth.uid() = author);

-- ③ 신고 (작성만 가능 — 목록은 대시보드에서 관리자만 열람)
create table if not exists public.plaza_reports (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.plaza_posts (id) on delete cascade,
  author uuid not null default auth.uid(),
  reason text check (reason is null or char_length(reason) <= 200),
  created_at timestamptz not null default now(),
  unique (post_id, author)
);
alter table public.plaza_reports enable row level security;

drop policy if exists "reports insert" on public.plaza_reports;
create policy "reports insert" on public.plaza_reports for insert to authenticated
  with check (auth.uid() = author);

-- ④ 관리자 삭제 — 키 테이블(RLS on·정책 없음 = API로 아무도 못 읽음)
create table if not exists public.plaza_admin (key text primary key);
alter table public.plaza_admin enable row level security;

create or replace function public.plaza_admin_delete(pid uuid, akey text) returns boolean
language plpgsql security definer set search_path = public as $$
begin
  if not exists (select 1 from plaza_admin where key = akey) then
    return false;
  end if;
  delete from plaza_posts where id = pid;
  return true;
end $$;
