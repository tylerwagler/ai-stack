import os
import stripe
import json
import psycopg2
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
ENDPOINT_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = "postgres"
DB_PASS = os.getenv("POSTGRES_PASSWORD")
DB_HOST = os.getenv("POSTGRES_HOST", "db")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
PORTAL_URL = os.getenv("PORTAL_URL", "http://localhost:3000")

def get_db_conn():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        host=DB_HOST,
        port=DB_PORT
    )

@app.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    try:
        data = await request.json()
        user_id = data.get("user_id")
        user_email = data.get("user_email")
        price_id = data.get("price_id")  # Stripe Price ID for a plan

        if not user_id or not user_email or not price_id:
            raise HTTPException(status_code=400, detail="Missing required parameters")

        # Create Checkout Session
        checkout_session = stripe.checkout.Session.create(
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f"{PORTAL_URL}/portal?success=true",
            cancel_url=f"{PORTAL_URL}/portal?canceled=true",
            customer_email=user_email,
            client_reference_id=user_id,
            metadata={
                "supabase_user_id": user_id
            }
        )

        return {"url": checkout_session.url}
    except Exception as e:
        print(f"STRIPE_ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create-portal-session")
async def create_portal_session(request: Request):
    try:
        data = await request.json()
        user_id = data.get("user_id")
        
        # We need the stripe_customer_id from the database
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT stripe_customer_id FROM public.profiles WHERE id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row or not row[0]:
            raise HTTPException(status_code=404, detail="Stripe customer not found")

        customer_id = row[0]

        # Authentic portal session for the customer
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{PORTAL_URL}/portal",
        )

        return {"url": portal_session.url}
    except Exception as e:
        print(f"PORTAL_ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook")
async def webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()
    
    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, ENDPOINT_SECRET
        )
    except ValueError as e:
        # Invalid payload
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_completed(session)
    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        handle_subscription_updated(subscription)
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_deleted(subscription)

    return {"status": "success"}

def handle_checkout_completed(session):
    user_id = session.get('client_reference_id')
    customer_id = session.get('customer')
    subscription_id = session.get('subscription')

    if not user_id:
        # Fallback to metadata if client_reference_id is missing
        user_id = session.get('metadata', {}).get('supabase_user_id')

    if user_id and customer_id and subscription_id:
        # Fetch subscription details to get the price_id and period_end
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            price_id = subscription['items']['data'][0]['price']['id']
            # Get period end from the subscription item
            period_end = subscription['items']['data'][0].get('current_period_end')
            update_profile_subscription(user_id, customer_id, 'active', price_id, period_end)
        except Exception as e:
            print(f"SUBSCRIPTION_FETCH_ERROR: {e}")
            # Fallback to updating without tier
            update_profile_subscription(user_id, customer_id, 'active', None, None)

def handle_subscription_updated(subscription):
    customer_id = subscription.get('customer')
    status = subscription.get('status')
    # Get period end from the subscription items
    period_end = None
    if subscription.get('items') and subscription['items'].get('data'):
        period_end = subscription['items']['data'][0].get('current_period_end')

    # Get price_id to maintain tier during active period
    price_id = None
    if subscription.get('items') and subscription['items'].get('data'):
        price_id = subscription['items']['data'][0]['price']['id']

    update_profile_by_customer(customer_id, status, period_end, price_id)

def handle_subscription_deleted(subscription):
    customer_id = subscription.get('customer')
    # When subscription is deleted, downgrade immediately
    update_profile_by_customer(customer_id, 'canceled', None, None)

def update_profile_subscription(user_id, customer_id, status, price_id=None, period_end=None):
    try:
        conn = get_db_conn()
        cur = conn.cursor()

        # Convert period_end timestamp to PostgreSQL timestamp
        period_end_sql = None
        if period_end:
            period_end_sql = f"to_timestamp({period_end})"

        # If we have a price_id, look up which tier it belongs to
        if price_id:
            # Find tier by stripe_price_id
            cur.execute("SELECT id, name FROM tiers WHERE stripe_price_id = %s", (price_id,))
            tier_row = cur.fetchone()

            if tier_row:
                tier_id, tier_name = tier_row
                if period_end_sql:
                    cur.execute(
                        f"""UPDATE public.profiles
                           SET stripe_customer_id = %s, subscription_status = %s,
                               tier_id = %s, subscription_period_end = {period_end_sql},
                               updated_at = now()
                           WHERE id = %s""",
                        (customer_id, status, tier_id, user_id)
                    )
                else:
                    cur.execute(
                        """UPDATE public.profiles
                           SET stripe_customer_id = %s, subscription_status = %s,
                               tier_id = %s,
                               updated_at = now()
                           WHERE id = %s""",
                        (customer_id, status, tier_id, user_id)
                    )
                print(f"Updated user {user_id} subscription to {status} with tier {tier_name}, period_end: {period_end}")
            else:
                # Price ID not found in tiers table, just update subscription status
                if period_end_sql:
                    cur.execute(
                        f"UPDATE public.profiles SET stripe_customer_id = %s, subscription_status = %s, subscription_period_end = {period_end_sql}, updated_at = now() WHERE id = %s",
                        (customer_id, status, user_id)
                    )
                else:
                    cur.execute(
                        "UPDATE public.profiles SET stripe_customer_id = %s, subscription_status = %s, updated_at = now() WHERE id = %s",
                        (customer_id, status, user_id)
                    )
                print(f"Updated user {user_id} subscription to {status} (price_id {price_id} not found in tiers)")
        else:
            # No price_id provided, just update subscription status
            if period_end_sql:
                cur.execute(
                    f"UPDATE public.profiles SET stripe_customer_id = %s, subscription_status = %s, subscription_period_end = {period_end_sql}, updated_at = now() WHERE id = %s",
                    (customer_id, status, user_id)
                )
            else:
                cur.execute(
                    "UPDATE public.profiles SET stripe_customer_id = %s, subscription_status = %s, updated_at = now() WHERE id = %s",
                    (customer_id, status, user_id)
                )
            print(f"Updated user {user_id} subscription to {status} (no tier change)")

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB_UPDATE_ERROR: {e}")

def update_profile_by_customer(customer_id, status, period_end=None, price_id=None):
    try:
        conn = get_db_conn()
        cur = conn.cursor()

        # Convert period_end timestamp to PostgreSQL timestamp
        period_end_sql = None
        if period_end:
            period_end_sql = f"to_timestamp({period_end})"

        # If subscription is canceled but has a future period_end, maintain tier until expiration
        if status == 'canceled':
            if period_end:
                # Subscription canceled but period hasn't ended - maintain current tier
                cur.execute(
                    f"""UPDATE public.profiles
                       SET subscription_status = %s, subscription_period_end = {period_end_sql},
                           updated_at = now()
                       WHERE stripe_customer_id = %s""",
                    (status, customer_id)
                )
                print(f"Updated customer {customer_id} subscription to {status}, maintaining tier until period_end: {period_end}")
            else:
                # No period_end or period has ended - downgrade to free tier
                cur.execute(
                    """UPDATE public.profiles
                       SET subscription_status = %s,
                           tier_id = (SELECT id FROM tiers WHERE name = 'free'),
                           subscription_period_end = NULL,
                           updated_at = now()
                       WHERE stripe_customer_id = %s""",
                    (status, customer_id)
                )
                print(f"Updated customer {customer_id} subscription to {status}, downgraded to free tier")
        else:
            # Active or other status - update tier if we have price_id
            if price_id:
                # Find tier by stripe_price_id
                cur.execute("SELECT id, name FROM tiers WHERE stripe_price_id = %s", (price_id,))
                tier_row = cur.fetchone()

                if tier_row:
                    tier_id, tier_name = tier_row
                    if period_end_sql:
                        cur.execute(
                            f"""UPDATE public.profiles
                               SET subscription_status = %s, tier_id = %s,
                                   subscription_period_end = {period_end_sql},
                                   updated_at = now()
                               WHERE stripe_customer_id = %s""",
                            (status, tier_id, customer_id)
                        )
                    else:
                        cur.execute(
                            """UPDATE public.profiles
                               SET subscription_status = %s, tier_id = %s,
                                   updated_at = now()
                               WHERE stripe_customer_id = %s""",
                            (status, tier_id, customer_id)
                        )
                    print(f"Updated customer {customer_id} subscription to {status} with tier {tier_name}")
                else:
                    # Just update status if tier not found
                    if period_end_sql:
                        cur.execute(
                            f"UPDATE public.profiles SET subscription_status = %s, subscription_period_end = {period_end_sql}, updated_at = now() WHERE stripe_customer_id = %s",
                            (status, customer_id)
                        )
                    else:
                        cur.execute(
                            "UPDATE public.profiles SET subscription_status = %s, updated_at = now() WHERE stripe_customer_id = %s",
                            (status, customer_id)
                        )
                    print(f"Updated customer {customer_id} subscription to {status}")
            else:
                # No price_id, just update status
                if period_end_sql:
                    cur.execute(
                        f"UPDATE public.profiles SET subscription_status = %s, subscription_period_end = {period_end_sql}, updated_at = now() WHERE stripe_customer_id = %s",
                        (status, customer_id)
                    )
                else:
                    cur.execute(
                        "UPDATE public.profiles SET subscription_status = %s, updated_at = now() WHERE stripe_customer_id = %s",
                        (status, customer_id)
                    )
                print(f"Updated customer {customer_id} subscription to {status}")

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB_UPDATE_ERROR: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
