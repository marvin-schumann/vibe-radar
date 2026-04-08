-- Enable RLS on cities table (was missing from initial migration)
alter table public.cities enable row level security;

-- Allow all authenticated users to read cities (public reference data)
create policy "Cities are readable by all"
    on public.cities for select
    using (true);
