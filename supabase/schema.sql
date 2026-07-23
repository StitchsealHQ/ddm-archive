-- ============================================================
-- 동대문 광장 (plaza) 스키마 v1 — 260723
-- Supabase 대시보드 > SQL Editor 에 전체 붙여넣고 Run 1회.
-- 재실행해도 안전 (if not exists / drop-recreate).
-- 실행 후 Authentication > Sign In / Providers 에서
-- "Allow anonymous sign-ins" 를 켜야 글쓰기가 동작한다.
-- ============================================================

-- 글
create table if not exists public.plaza_posts (
  id uuid primary key default gen_random_uuid(),
  author uuid not null default auth.uid(),
  nickname text not null check (char_length(nickname) between 1 and 20),
  body text not null check (char_length(body) between 1 and 2000),
  image_url text,
  category text not null default '자유'
    check (category in ('자유', '정보', '질문', '거래')),
  created_at timestamptz not null default now()
);
create index if not exists plaza_posts_created
  on public.plaza_posts (created_at desc);

-- 댓글
create table if not exists public.plaza_comments (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.plaza_posts (id) on delete cascade,
  author uuid not null default auth.uid(),
  nickname text not null check (char_length(nickname) between 1 and 20),
  body text not null check (char_length(body) between 1 and 500),
  created_at timestamptz not null default now()
);
create index if not exists plaza_comments_post
  on public.plaza_comments (post_id, created_at);

-- 좋아요 (사용자당 글마다 1개 — PK가 중복을 막음)
create table if not exists public.plaza_likes (
  post_id uuid not null references public.plaza_posts (id) on delete cascade,
  author uuid not null default auth.uid(),
  created_at timestamptz not null default now(),
  primary key (post_id, author)
);

-- RLS: 읽기는 모두, 쓰기는 본인 세션만, 삭제는 본인 것만
alter table public.plaza_posts    enable row level security;
alter table public.plaza_comments enable row level security;
alter table public.plaza_likes    enable row level security;

drop policy if exists "posts read"    on public.plaza_posts;
drop policy if exists "posts insert"  on public.plaza_posts;
drop policy if exists "posts delete"  on public.plaza_posts;
create policy "posts read"   on public.plaza_posts for select using (true);
create policy "posts insert" on public.plaza_posts for insert to authenticated
  with check (auth.uid() = author);
create policy "posts delete" on public.plaza_posts for delete to authenticated
  using (auth.uid() = author);

drop policy if exists "comments read"   on public.plaza_comments;
drop policy if exists "comments insert" on public.plaza_comments;
drop policy if exists "comments delete" on public.plaza_comments;
create policy "comments read"   on public.plaza_comments for select using (true);
create policy "comments insert" on public.plaza_comments for insert to authenticated
  with check (auth.uid() = author);
create policy "comments delete" on public.plaza_comments for delete to authenticated
  using (auth.uid() = author);

drop policy if exists "likes read"   on public.plaza_likes;
drop policy if exists "likes insert" on public.plaza_likes;
drop policy if exists "likes delete" on public.plaza_likes;
create policy "likes read"   on public.plaza_likes for select using (true);
create policy "likes insert" on public.plaza_likes for insert to authenticated
  with check (auth.uid() = author);
create policy "likes delete" on public.plaza_likes for delete to authenticated
  using (auth.uid() = author);

-- 도배 방지 (글 5개/10분, 댓글 20개/10분 per 사용자)
create or replace function public.plaza_post_limit() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from plaza_posts
      where author = auth.uid()
        and created_at > now() - interval '10 minutes') >= 5 then
    raise exception '글 작성이 잠시 제한됐습니다. 조금 뒤에 다시 시도해 주세요.';
  end if;
  return new;
end $$;
drop trigger if exists plaza_post_limit_t on public.plaza_posts;
create trigger plaza_post_limit_t before insert on public.plaza_posts
  for each row execute function public.plaza_post_limit();

create or replace function public.plaza_comment_limit() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from plaza_comments
      where author = auth.uid()
        and created_at > now() - interval '10 minutes') >= 20 then
    raise exception '댓글 작성이 잠시 제한됐습니다. 조금 뒤에 다시 시도해 주세요.';
  end if;
  return new;
end $$;
drop trigger if exists plaza_comment_limit_t on public.plaza_comments;
create trigger plaza_comment_limit_t before insert on public.plaza_comments
  for each row execute function public.plaza_comment_limit();

-- 이미지 버킷 (공개 읽기, 로그인 세션만 업로드, 5MB, 이미지 형식만)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('plaza', 'plaza', true, 5242880,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do nothing;

drop policy if exists "plaza image read" on storage.objects;
create policy "plaza image read" on storage.objects
  for select using (bucket_id = 'plaza');
drop policy if exists "plaza image upload" on storage.objects;
create policy "plaza image upload" on storage.objects
  for insert to authenticated with check (bucket_id = 'plaza');

-- 실시간 (새 글이 열려있는 화면에 바로 반영)
do $$ begin
  alter publication supabase_realtime add table public.plaza_posts;
exception when duplicate_object then null; end $$;
