# Supabase AI Stack Credentials

Dedicated Supabase instance for the AI stack.

## Access Points
- **Studio (Dashboard)**: [http://localhost:8003](http://localhost:8003)
- **API URL**: [http://localhost:8004](http://localhost:8004)
- **Database (Postgres)**: `localhost:5433` (Password below)

## Credentials
- **Studio Login**: `supabase` / `supabase`
- **Postgres Password**: `-CL8PQXeWzFqGnxCjhHwJn7fXRrHrSWr`
- **JWT Secret**: `0rviDnESRwoc4nzENWcNVJKyCot9vAQKGoe_pKvE5_MX9rUkzihZpQ`

## Keys (Safe for anon usage in frontend)
- **Anon Key**: `eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJyb2xlIjogImFub24iLCAiaXNzIjogInN1cGFiYXNlIiwgImlhdCI6IDE3Njk1MjYyNTgsICJleHAiOiAyMDg0ODg2MjU4fQ.UYjtcXHj4l-9rEHMzNk2rqc-djmaPTtumgylFpG5NfA`
- **Service Role Key** (HIDDEN/SECURE): [Check .env for full key]

## Configuration
Managed via `supabase-ai/docker-compose.yml` and `.env`.
Included in main stack via `include` directive.
