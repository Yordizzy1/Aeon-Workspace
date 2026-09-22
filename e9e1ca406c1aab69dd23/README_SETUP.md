# AEON Stripe → Persistent Credits Bridge

Replace the generated business files with:
- `app.py`
- `billing_adapter.py`
- `database.py`
- `requirements.txt`

## Required Render setup

Create a Render Postgres database and add its **Internal Database URL** to the web service as:

- `DATABASE_URL`

Add Stripe webhook secrets as environment variables. Keeping sandbox and live secrets separate lets the same endpoint verify both modes:

- `STRIPE_WEBHOOK_SECRET_TEST` — sandbox `whsec_...`
- `STRIPE_WEBHOOK_SECRET_LIVE` — live `whsec_...`

`STRIPE_WEBHOOK_SECRET` is supported as a temporary backward-compatible fallback, but the TEST/LIVE names are preferred.

Add the public live Payment Link as:

- `PAYMENT_LINK_URL`

Never commit Stripe secrets or database credentials to GitHub.

## Stripe Payment Link redirect

For both sandbox and live Payment Links, set after-payment behavior to redirect to:

`https://aeon-workspace.onrender.com/claim?session_id={CHECKOUT_SESSION_ID}`

The signed webhook is the source of truth for payment. The redirect only lets the customer claim the API key after the webhook records the paid Checkout Session.

## End-to-end flow

1. Customer pays through Stripe Payment Link.
2. Stripe sends a signed Checkout event to `/stripe/webhook`.
3. AEON records the paid purchase in Postgres.
4. Stripe redirects to `/claim?session_id=...`.
5. Customer clicks **Reveal my API key**.
6. AEON grants prepaid credits and displays a new API key exactly once.
7. Customer calls `/detect` with `Authorization: Bearer <key>`.
8. Each successful call consumes one credit atomically.
9. Repeating the same `event_id` does not consume a second credit.

Sandbox and live customers/balances are separated in the database.
