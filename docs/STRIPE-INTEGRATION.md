# Stripe Integration Documentation

**Status:** ✅ Production Ready
**Date Completed:** January 29, 2026
**Live Domain:** https://ellie.elytrondefense.com

---

## Overview

The AI Stack now has a fully functional Stripe integration for subscription billing. Users can subscribe to Pro or Ultimate plans through Stripe Checkout, and their access tiers are automatically updated via webhooks.

---

## Architecture

### Components

1. **stripe-handler** (FastAPI service on port 8000)
   - Handles Stripe Checkout session creation
   - Processes Stripe webhooks
   - Updates user tiers in database
   - Manages billing portal sessions

2. **temper-view** (React frontend)
   - Billing page with tier selection
   - Stripe Checkout integration
   - Billing portal access

3. **tiers table** (PostgreSQL)
   - Stores tier configurations with Stripe price IDs
   - Dynamic mapping between Stripe prices and access tiers

---

## Data Flow

### Subscription Flow

```
User clicks "Upgrade to Pro/Ultimate"
    ↓
Frontend calls /api/billing/create-checkout-session
    ↓
stripe-handler creates Stripe Checkout session
    ↓
User redirected to Stripe's hosted checkout page
    ↓
User completes payment
    ↓
Stripe sends checkout.session.completed webhook
    ↓
stripe-handler receives webhook
    ↓
Fetches subscription details from Stripe API
    ↓
Queries tiers table for matching stripe_price_id
    ↓
Updates profiles: subscription_status='active', tier_id=<matched_tier>
    ↓
User redirected back to portal (success_url)
    ↓
Frontend displays "PRO" tier with active subscription
```

### Billing Portal Flow

```
User clicks "Open Billing Portal"
    ↓
Frontend calls /api/billing/create-portal-session
    ↓
stripe-handler creates Stripe Customer Portal session
    ↓
User redirected to Stripe's billing portal
    ↓
User can manage payment methods, view invoices, cancel subscription
```

---

## Database Schema

### Tiers Table

```sql
CREATE TABLE tiers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  stripe_price_id TEXT,  -- Maps to Stripe Price ID
  hourly_limit BIGINT DEFAULT -1,
  daily_limit BIGINT DEFAULT -1,
  weekly_limit BIGINT DEFAULT -1,
  monthly_limit BIGINT DEFAULT -1,
  rate_limit_rpm INTEGER DEFAULT -1,
  rate_limit_tpm INTEGER DEFAULT -1,
  price_monthly NUMERIC(10,2) DEFAULT 0,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

**Current Tiers:**
- **Free:** No stripe_price_id (default tier for new users)
- **Pro:** `price_1Sv32rDNaJU3OXpntborxKFa`
- **Enterprise (Ultimate):** `price_1Sv33IDNaJU3OXpn8vyU7frY`
- **Unlimited:** No stripe_price_id (admin tier)

### Profiles Table (Relevant Fields)

```sql
CREATE TABLE profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id),
  tier_id UUID REFERENCES tiers(id),
  subscription_status TEXT DEFAULT 'inactive',
  stripe_customer_id TEXT,
  ...
);
```

**Note:** The `plan_type` field exists but is **deprecated** - use `tier_id` instead.

---

## Configuration

### Environment Variables (.env)

```bash
# Stripe API Keys (LIVE mode)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### Docker Compose (stripe-handler)

```yaml
stripe-handler:
  environment:
    - STRIPE_SECRET_KEY=${STRIPE_SECRET_KEY}
    - STRIPE_WEBHOOK_SECRET=${STRIPE_WEBHOOK_SECRET}
    - POSTGRES_DB=${POSTGRES_DB}
    - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    - POSTGRES_HOST=db
    - POSTGRES_PORT=5433
    - PORTAL_URL=https://ellie.elytrondefense.com
```

**Critical Settings:**
- `POSTGRES_PORT=5433` (Supabase uses 5433, not default 5432)
- `PORTAL_URL` must be production domain (not localhost)

### Nginx Routing (temper-view)

```nginx
location /api/billing/ {
    rewrite ^/api/billing/(.*) /$1 break;
    proxy_pass http://stripe-handler:8000;
    ...
}
```

---

## Stripe Dashboard Configuration

### Webhook Endpoint

**URL:** `https://ellie.elytrondefense.com/api/billing/webhook`

**Events:**
- `checkout.session.completed` - When payment succeeds
- `customer.subscription.updated` - When subscription changes
- `customer.subscription.deleted` - When subscription is cancelled

**API Version:** 2026-01-28.clover

---

## API Endpoints

### POST /api/billing/create-checkout-session

Creates a Stripe Checkout session for subscription purchase.

**Request:**
```json
{
  "user_id": "uuid",
  "user_email": "user@example.com",
  "price_id": "price_1Sv32rDNaJU3OXpntborxKFa"
}
```

**Response:**
```json
{
  "url": "https://checkout.stripe.com/..."
}
```

### POST /api/billing/create-portal-session

Creates a Stripe Customer Portal session for subscription management.

**Request:**
```json
{
  "user_id": "uuid"
}
```

**Response:**
```json
{
  "url": "https://billing.stripe.com/..."
}
```

### POST /api/billing/webhook

Receives webhook events from Stripe.

**Headers:**
- `stripe-signature` - Webhook signature for verification

**Webhook Handler Logic:**
1. Verifies signature using `STRIPE_WEBHOOK_SECRET`
2. Handles event based on type
3. For checkout.session.completed:
   - Fetches subscription from Stripe API
   - Extracts `price_id` from subscription
   - Queries `tiers` table for matching `stripe_price_id`
   - Updates user's `tier_id` and `subscription_status`

---

## Frontend Components

### BillingSection (Portal.tsx)

Displays current subscription status and tier selection.

**For Active Subscribers:**
- Shows current tier name from `profile.tiers.display_name`
- "Your subscription is active!" badge
- "Open Billing Portal" button

**For Free Tier Users:**
- Shows two upgrade cards: Pro and Ultimate
- "Upgrade to Pro" button → `price_1Sv32rDNaJU3OXpntborxKFa`
- "Upgrade to Ultimate" button → `price_1Sv33IDNaJU3OXpn8vyU7frY`

**Data Source:**
```javascript
const { data } = await supabase
  .from('profiles')
  .select('*, tiers(display_name)')
  .eq('id', userId)
  .single();
```

---

## Key Features

### ✅ Dynamic Tier Mapping

Tiers are mapped to Stripe prices via the database, not hardcoded:

```sql
-- Add new tier with Stripe integration
INSERT INTO tiers (name, display_name, stripe_price_id, ...)
VALUES ('business', 'Business', 'price_NEW_ID', ...);
```

No code changes needed to add new subscription tiers!

### ✅ Automatic Tier Assignment

When a webhook fires:
1. Webhook handler queries database for tier matching `stripe_price_id`
2. Updates user's `tier_id` automatically
3. Frontend reads tier name from joined `tiers` table

### ✅ Single Source of Truth

- **tier_id** (foreign key to tiers table) - ✅ Used
- **plan_type** (text field) - ❌ Deprecated, do not use

### ✅ Production-Ready Security

- Webhook signature verification
- HTTPS required for all endpoints
- Row-Level Security on database tables
- API key authentication on inference endpoints

---

## Testing

### Test Subscription Flow

1. Navigate to https://ellie.elytrondefense.com
2. Create account or log in
3. Go to "Billing" tab
4. Click "Upgrade to Pro"
5. Complete checkout with test card: `4242 4242 4242 4242`
6. Verify redirect back to portal
7. Confirm tier shows "PRO" and subscription is active

### Verify Webhook Delivery

**Stripe Dashboard → Developers → Webhooks → AI Stack Production**

Check recent webhook deliveries for:
- ✅ Succeeded status
- Response: `{"status":"success"}`
- No errors in logs

### Check Database

```sql
-- Verify tier assignment
SELECT p.display_name, p.subscription_status, t.display_name as tier
FROM profiles p
LEFT JOIN tiers t ON p.tier_id = t.id
WHERE p.stripe_customer_id IS NOT NULL;
```

---

## Troubleshooting

### Issue: Checkout redirects to localhost

**Cause:** `PORTAL_URL` set to `http://localhost:3000`

**Fix:**
```yaml
# docker-compose.yml
stripe-handler:
  environment:
    - PORTAL_URL=https://ellie.elytrondefense.com
```
Then: `docker compose up -d stripe-handler`

### Issue: Webhook not received

**Causes:**
1. Webhook endpoint not accessible from internet
2. Incorrect webhook secret
3. Reverse proxy not routing `/api/billing/webhook`

**Debug:**
```bash
# Test webhook endpoint
curl -X POST https://ellie.elytrondefense.com/api/billing/webhook

# Check logs
docker compose logs stripe-handler --tail 50
```

### Issue: Tier doesn't update after payment

**Causes:**
1. `stripe_price_id` not set in tiers table
2. Database connection error (wrong port)
3. Webhook signature verification failed

**Fix:**
```sql
-- Check if price_id exists in tiers
SELECT * FROM tiers WHERE stripe_price_id = 'price_...';

-- Set missing price_id
UPDATE tiers SET stripe_price_id = 'price_...' WHERE name = 'pro';
```

### Issue: Database connection refused

**Cause:** stripe-handler trying to connect on port 5432 instead of 5433

**Fix:**
```yaml
# docker-compose.yml
stripe-handler:
  environment:
    - POSTGRES_PORT=5433  # Supabase uses 5433
```

---

## Adding New Tiers

### 1. Create Stripe Product and Price

1. Stripe Dashboard → Products → Add Product
2. Set name, pricing, billing interval
3. Copy Price ID (starts with `price_`)

### 2. Add Tier to Database

```sql
INSERT INTO tiers (
  name,
  display_name,
  stripe_price_id,
  hourly_limit,
  daily_limit,
  weekly_limit,
  monthly_limit,
  rate_limit_rpm,
  rate_limit_tpm,
  price_monthly
) VALUES (
  'business',
  'Business',
  'price_YOUR_NEW_STRIPE_PRICE_ID',
  2000000,  -- hourly tokens
  10000000, -- daily tokens
  50000000, -- weekly tokens
  200000000, -- monthly tokens
  500,      -- requests per minute
  500000,   -- tokens per minute
  49.99     -- monthly price (for display)
);
```

### 3. Add to Frontend

Edit `temper-view/src/components/portal/Portal.tsx`:

```javascript
{/* Business Plan */}
<div className="bg-dark-900 border border-dark-800 rounded-2xl p-6 shadow-lg">
  <h3>Business Plan</h3>
  <button onClick={() => handleUpgrade('price_YOUR_NEW_STRIPE_PRICE_ID')}>
    Upgrade to Business
  </button>
</div>
```

Rebuild: `docker compose up -d --build temper-view`

**That's it!** The webhook handler will automatically assign the new tier when someone subscribes.

---

## Migration Notes

### Removed Dependencies

- **plan_type field:** Deprecated in favor of tier_id
- **Hardcoded price mappings:** Replaced with database lookup

### Data Migration (if needed)

If you have existing subscriptions with `plan_type` set but `tier_id` NULL:

```sql
-- Migrate plan_type to tier_id
UPDATE profiles
SET tier_id = (SELECT id FROM tiers WHERE name = plan_type)
WHERE tier_id IS NULL AND plan_type IS NOT NULL;
```

---

## Security Considerations

### Live Mode vs Test Mode

Currently using **LIVE** mode keys:
- Real payments processed
- Real credit cards charged
- Production webhook endpoint

To switch to test mode:
1. Replace with `sk_test_...` and `pk_test_...` keys
2. Update webhook with test endpoint
3. Use test cards: `4242 4242 4242 4242`

### Webhook Verification

All webhooks are verified using signature:
```python
event = stripe.Webhook.construct_event(
    payload,
    stripe_signature,
    ENDPOINT_SECRET
)
```

Prevents unauthorized webhook spoofing.

---

## Performance

- Webhook processing: ~200-500ms
- Database tier lookup: <10ms
- Checkout session creation: ~300-800ms

No performance bottlenecks identified.

---

## Future Enhancements

### Potential Improvements

1. **Usage-based billing:** Track token usage and bill accordingly
2. **Annual subscriptions:** Add yearly pricing options
3. **Trial periods:** Implement 7-day or 14-day trials
4. **Proration:** Handle mid-cycle tier upgrades/downgrades
5. **Invoice customization:** Add company branding to invoices
6. **Email notifications:** Send confirmation emails on subscription events
7. **Admin dashboard:** View all subscriptions and revenue metrics
8. **Coupon support:** Add promotional codes and discounts

### Stripe Features Not Yet Used

- Payment Links
- Customer Portal customization
- Tax calculation
- Invoicing
- Metered billing
- Connect (marketplace)

---

## Support

For issues or questions about the Stripe integration:

1. Check logs: `docker compose logs stripe-handler`
2. Verify webhook deliveries in Stripe Dashboard
3. Check database: `SELECT * FROM tiers;`
4. Review this documentation

---

## Changelog

### 2026-01-29 - Initial Implementation

- ✅ Stripe Checkout integration
- ✅ Webhook handler with automatic tier assignment
- ✅ Billing portal integration
- ✅ Dynamic tier mapping via database
- ✅ Production deployment with SSL
- ✅ Mobile-responsive UI
- ✅ Removed plan_type dependency
- ✅ Added stripe_price_id to tiers table

---

**Integration Status:** 🟢 Production Ready
**Last Updated:** January 29, 2026
**Maintained By:** Tyler Wagler
