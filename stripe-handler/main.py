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

    if user_id and customer_id:
        update_profile_subscription(user_id, customer_id, 'active')

def handle_subscription_updated(subscription):
    customer_id = subscription.get('customer')
    status = subscription.get('status')
    update_profile_by_customer(customer_id, status)

def handle_subscription_deleted(subscription):
    customer_id = subscription.get('customer')
    update_profile_by_customer(customer_id, 'canceled')

def update_profile_subscription(user_id, customer_id, status):
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.profiles SET stripe_customer_id = %s, subscription_status = %s, updated_at = now() WHERE id = %s",
            (customer_id, status, user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"Updated user {user_id} subscription to {status}")
    except Exception as e:
        print(f"DB_UPDATE_ERROR: {e}")

def update_profile_by_customer(customer_id, status):
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.profiles SET subscription_status = %s, updated_at = now() WHERE stripe_customer_id = %s",
            (status, customer_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"Updated customer {customer_id} subscription to {status}")
    except Exception as e:
        print(f"DB_UPDATE_ERROR: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
