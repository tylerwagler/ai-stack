# Supabase AI Stack Credentials

**TEMPLATE FILE - Copy to `supabase-credentials.md` and fill in your values**

Dedicated Supabase instance for the AI stack.

## Access Points
- **Studio (Dashboard)**: [http://localhost:8003](http://localhost:8003)
- **API URL**: [http://localhost:8004](http://localhost:8004)
- **Database (Postgres)**: `localhost:5433`

## Credentials

- **Studio Login**: `supabase` / `supabase` (default)
- **Postgres Password**: `[GENERATE_WITH_COMMAND_BELOW]`
- **JWT Secret**: `[GENERATE_WITH_COMMAND_BELOW]`

## Keys (Safe for anon usage in frontend)

- **Anon Key**: `[GENERATED_FROM_JWT_SECRET]`
- **Service Role Key**: `[GENERATED_FROM_JWT_SECRET]` (KEEP SECRET - server-side only!)

## Generation Commands

Generate secure credentials:

```bash
# Postgres password (32 chars)
openssl rand -base64 32

# JWT secret (64 chars)
openssl rand -base64 64
```

After setting the JWT_SECRET in .env and starting Supabase, the anon and service role keys will be automatically generated. You can find them in the Supabase Studio at http://localhost:8003 under Settings > API.

## Configuration

Managed via `supabase-ai/docker-compose.yml` and `.env`.
Included in main stack via `include` directive.

## Security Notes

1. **Never commit `supabase-credentials.md`** - it contains production secrets
2. **Only commit this `.example.md` file** - it has placeholders
3. **Rotate credentials** if ever exposed in git history
4. **Store production credentials** in a password manager

## Setup Instructions

1. Copy this template:
   ```bash
   cp docs/supabase-credentials.example.md docs/supabase-credentials.md
   ```

2. Generate credentials using the commands above

3. Update `.env` file with:
   ```bash
   POSTGRES_PASSWORD=your_generated_password
   JWT_SECRET=your_generated_secret
   ```

4. Start Supabase:
   ```bash
   docker compose up -d db kong auth rest
   ```

5. Get the generated keys from Supabase Studio and add to `.env`:
   ```bash
   SUPABASE_ANON_KEY=your_generated_anon_key
   SUPABASE_SERVICE_ROLE_KEY=your_generated_service_role_key
   ```

6. Update your local `docs/supabase-credentials.md` with the actual values (for reference)
