create extension if not exists pgcrypto;

create table if not exists public.rank_readings (
  id uuid primary key default gen_random_uuid(),
  bucket text not null,
  category integer not null,
  world text not null default '',
  checked_at timestamptz not null default now(),
  updated_at text not null default '',
  created_at timestamptz not null default now()
);

create table if not exists public.rank_players (
  id bigint generated always as identity primary key,
  reading_id uuid not null references public.rank_readings(id) on delete cascade,
  player_key text not null,
  name text not null,
  rank integer not null,
  vocation text not null default '',
  world text not null default '',
  level integer not null,
  points bigint not null,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists rank_readings_bucket_checked_at_idx
  on public.rank_readings (bucket, checked_at desc);

create index if not exists rank_players_reading_id_rank_idx
  on public.rank_players (reading_id, rank);

create index if not exists rank_players_player_key_idx
  on public.rank_players (player_key);

create table if not exists public.party_configs (
  id uuid primary key default gen_random_uuid(),
  party_key text not null unique,
  name text not null,
  members jsonb not null default '[]'::jsonb,
  highlight boolean not null default false,
  target_xp bigint not null default 0,
  notes text not null default '',
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists party_configs_highlight_idx
  on public.party_configs (highlight);
