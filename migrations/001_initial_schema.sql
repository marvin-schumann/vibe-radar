-- Vibe Radar: Initial Schema
-- Run this in your Supabase SQL editor (Dashboard → SQL Editor → New Query)

-- ─────────────────────────────────────────
-- 1. PROFILES
-- Extended user data linked to auth.users
-- ─────────────────────────────────────────
create table public.profiles (
    id uuid references auth.users(id) on delete cascade primary key,
    email text not null,
    created_at timestamptz default now() not null,
    is_approved boolean default false not null,   -- waitlist gate
    is_pro boolean default false not null,         -- paid tier
    lemon_squeezy_customer_id text
);

alter table public.profiles enable row level security;

create policy "Users can view own profile"
    on public.profiles for select
    using (auth.uid() = id);

create policy "Users can update own profile"
    on public.profiles for update
    using (auth.uid() = id);

-- Auto-create profile on signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
    insert into public.profiles (id, email)
    values (new.id, new.email);
    return new;
end;
$$;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();


-- ─────────────────────────────────────────
-- 2. CONNECTED ACCOUNTS
-- Per-user Spotify / SoundCloud tokens
-- ─────────────────────────────────────────
create table public.connected_accounts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.profiles(id) on delete cascade not null,
    platform text not null check (platform in ('spotify', 'soundcloud')),
    access_token text,
    refresh_token text,
    token_expires_at timestamptz,
    username text,           -- SoundCloud username or Spotify display name
    last_synced timestamptz,
    unique (user_id, platform)
);

alter table public.connected_accounts enable row level security;

create policy "Users can manage own connected accounts"
    on public.connected_accounts for all
    using (auth.uid() = user_id);


-- ─────────────────────────────────────────
-- 3. USER ARTISTS
-- Artists collected from each user's music sources
-- ─────────────────────────────────────────
create table public.user_artists (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.profiles(id) on delete cascade not null,
    platform text not null check (platform in ('spotify', 'soundcloud')),
    artist_id text not null,       -- platform's artist ID
    name text not null,
    genres text[] default '{}',
    image_url text,
    last_synced timestamptz default now(),
    unique (user_id, platform, artist_id)
);

create index on public.user_artists (user_id);

alter table public.user_artists enable row level security;

create policy "Users can manage own artists"
    on public.user_artists for all
    using (auth.uid() = user_id);


-- ─────────────────────────────────────────
-- 4. CITIES
-- Supported cities with RA / Songkick IDs
-- ─────────────────────────────────────────
create table public.cities (
    id uuid primary key default gen_random_uuid(),
    name text not null unique,
    country text not null,
    ra_area_id int,
    songkick_metro_id int,
    active boolean default false not null
);

-- Seed supported cities
insert into public.cities (name, country, ra_area_id, songkick_metro_id, active) values
    ('Madrid',    'Spain',       41,  28755, true),
    ('Barcelona', 'Spain',       44,  null,  false),
    ('Berlin',    'Germany',     34,  null,  false),
    ('Amsterdam', 'Netherlands', 29,  null,  false),
    ('London',    'UK',          13,  null,  false);


-- ─────────────────────────────────────────
-- 5. EVENTS
-- Scraped events (shared across all users)
-- ─────────────────────────────────────────
create table public.events (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    date date,
    venue text,
    city_id uuid references public.cities(id),
    source text not null check (source in ('resident_advisor', 'songkick', 'bandsintown')),
    source_url text,
    artists jsonb default '[]',    -- array of artist name strings
    raw_data jsonb,
    scraped_at timestamptz default now() not null,
    unique (source, source_url)    -- dedup by source URL
);

create index on public.events (city_id, date);
create index on public.events (date);

-- Events are public (readable by all authenticated users)
alter table public.events enable row level security;

create policy "Authenticated users can read events"
    on public.events for select
    to authenticated
    using (true);


-- ─────────────────────────────────────────
-- 6. USER MATCHES
-- Per-user artist-to-event matches
-- ─────────────────────────────────────────
create table public.user_matches (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.profiles(id) on delete cascade not null,
    event_id uuid references public.events(id) on delete cascade not null,
    match_type text not null check (match_type in ('exact', 'vibe')),
    confidence float not null,
    matched_artist_name text,
    match_reason text,
    notified_at timestamptz,
    created_at timestamptz default now() not null,
    unique (user_id, event_id)
);

create index on public.user_matches (user_id, match_type);

alter table public.user_matches enable row level security;

create policy "Users can view own matches"
    on public.user_matches for all
    using (auth.uid() = user_id);
