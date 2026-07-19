-- borahodo-daytrade Supabase semasi
-- Supabase > SQL Editor'e yapistir ve calistir.

create table if not exists trades (
  id bigint generated always as identity primary key,
  sym text not null,
  entry double precision not null,
  stop double precision not null,
  opened_at timestamptz not null default now(),
  status text not null default 'open',        -- open | closed
  partial_price double precision,
  partial_pct double precision,
  exit_price double precision,
  exit_reason text,
  closed_at timestamptz,
  r double precision,
  note text
);

create table if not exists plans (
  for_day date not null,
  kind text not null,                          -- evening | premarket
  payload jsonb not null,
  created_at timestamptz not null default now(),
  primary key (for_day, kind)
);

-- Basit kurulum: RLS kapali (anon key sadece sende ve Streamlit secrets'ta).
alter table trades disable row level security;
alter table plans disable row level security;
