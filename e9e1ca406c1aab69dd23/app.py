from flask import Flask, request, jsonify, Response
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timezone
import html
import os

import stripe
import yaml

from billing_adapter import BillingAdapter
from database import (
    DatabaseError,
    PurchaseAlreadyClaimed,
    PurchaseNotReady,
    claim_purchase,
    db_health,
    get_public_business_metrics,
    init_db,
    process_stripe_event,
)

app = Flask(__name__)

with open("pricing.yaml", "r", encoding="utf-8") as f:
    pricing = yaml.safe_load(f) or {}

UNIT_PRICE_USD = Decimal(str(pricing.get("unit_price", 0.003)))
PAYMENT_LINK_URL = os.environ.get("PAYMENT_LINK_URL", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_WEBHOOK_SECRET_TEST = os.environ.get("STRIPE_WEBHOOK_SECRET_TEST", "").strip()
STRIPE_WEBHOOK_SECRET_LIVE = os.environ.get("STRIPE_WEBHOOK_SECRET_LIVE", "").strip()

billing = BillingAdapter()
init_db()


def detect_anomaly(agent_output):
    if not agent_output:
        return {"anomaly": True, "reason": "empty_output"}
    if len(agent_output) > 10000:
        return {"anomaly": True, "reason": "output_too_long"}
    if "ERROR" in agent_output.upper():
        return {"anomaly": True, "reason": "contains_error_keyword"}
    return {"anomaly": False, "reason": "ok"}


def get_api_key_from_request():
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    token = request.headers.get("X-Aeon-API-Key", "").strip()
    return token or None


def purchase_credits_from_session(session):
    metadata = session.get("metadata") or {}
    explicit = metadata.get("aeon_credits")
    if explicit is not None:
        try:
            credits = int(explicit)
            if credits > 0:
                return credits
        except (TypeError, ValueError):
            pass

    amount_total = session.get("amount_total")
    if amount_total is None or UNIT_PRICE_USD <= 0:
        return 0

    amount_usd = Decimal(int(amount_total)) / Decimal("100")
    return int((amount_usd / UNIT_PRICE_USD).to_integral_value(rounding=ROUND_FLOOR))


@app.route("/")
def home():
    buy_html = (
        f'<p><a href="{html.escape(PAYMENT_LINK_URL, quote=True)}">Buy prepaid API credits</a></p>'
        if PAYMENT_LINK_URL
        else "<p>Purchasing is temporarily unavailable.</p>"
    )

    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Aeon — Agent Output Anomaly Detector</title>
        <style>
          body {{font-family: Arial, sans-serif; max-width: 820px; margin: 60px auto; padding: 0 24px; line-height: 1.6;}}
          code, pre {{background: #f3f3f3; border-radius: 5px;}}
          code {{padding: 3px 6px;}}
          pre {{padding: 14px; overflow-x: auto;}}
        </style>
      </head>
      <body>
        <h1>Aeon</h1>
        <h2>Agent Output Anomaly Detector</h2>
        <p>A machine-facing API for detecting malformed, empty, excessively large, or error-containing AI-agent outputs.</p>
        <h3>Pricing</h3>
        <p>${UNIT_PRICE_USD} per detection event, sold as prepaid credits.</p>
        {buy_html}
        <h3>API</h3>
        <p><code>POST /detect</code></p>
        <pre>{{
  "output": "Agent response here",
  "event_id": "unique-client-event-id"
}}</pre>
        <p>Authenticate with <code>Authorization: Bearer YOUR_AEON_API_KEY</code>.</p>
        <p><a href="/health">Service health</a></p>
      </body>
    </html>
    """


@app.route("/health")
def health():
    healthy, detail = db_health()
    return jsonify({
        "status": "healthy" if healthy else "degraded",
        "service": "Aeon Agent Output Anomaly Detector",
        "database": detail,
    }), 200 if healthy else 503


@app.route("/aeon/metrics")
def aeon_metrics():
    """Aggregate operational telemetry for the local AEON owner dashboard.

    No emails, API keys, Stripe secrets, database credentials, Checkout Session
    IDs, or customer-level records are returned. Test and live data stay separate.
    """
    healthy, detail = db_health()
    if not healthy:
        return jsonify({
            "schema": "aeon.business_metrics.v1",
            "status": "degraded",
            "service": "Aeon Agent Output Anomaly Detector",
            "database": detail,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "live": {},
            "test": {},
            "truth_semantics": (
                "DATABASE_UNAVAILABLE; NO_REVENUE_ASSUMED; "
                "NO_CUSTOMER_OR_CREDENTIAL_DATA_EXPOSED"
            ),
        }), 200

    try:
        metrics = get_public_business_metrics()
    except DatabaseError:
        return jsonify({
            "schema": "aeon.business_metrics.v1",
            "status": "degraded",
            "service": "Aeon Agent Output Anomaly Detector",
            "database": "metrics_query_failed",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "live": {},
            "test": {},
            "truth_semantics": (
                "METRICS_QUERY_FAILED; NO_REVENUE_ASSUMED; "
                "NO_CUSTOMER_OR_CREDENTIAL_DATA_EXPOSED"
            ),
        }), 200

    return jsonify({
        "schema": "aeon.business_metrics.v1",
        "status": "healthy",
        "service": "Aeon Agent Output Anomaly Detector",
        "database": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "live": metrics["live"],
        "test": metrics["test"],
        "truth_semantics": (
            "LIVE_GROSS_IS_SUM_OF_STRIPE_SIGNED_PAID_CHECKOUTS_RECORDED_IN_POSTGRES; "
            "SANDBOX_EXCLUDED_FROM_LIVE; PAID_DOES_NOT_BY_ITSELF_PROVE_BANK_PAYOUT_OR_SPENDABLE_SETTLEMENT; "
            "NO_CUSTOMER_OR_CREDENTIAL_DATA_EXPOSED"
        ),
    }), 200


@app.route("/detect", methods=["POST"])
def detect():
    api_key = get_api_key_from_request()
    if not api_key:
        return jsonify({"error": "missing_api_key"}), 401

    data = request.get_json(silent=True)
    if not data or "output" not in data:
        return jsonify({"error": "missing_output_field"}), 400

    agent_output = data["output"]
    if not isinstance(agent_output, str):
        return jsonify({"error": "output_must_be_string"}), 400

    event_id = data.get("event_id")
    if event_id is not None and not isinstance(event_id, str):
        return jsonify({"error": "event_id_must_be_string"}), 400

    result = detect_anomaly(agent_output)

    try:
        usage = billing.consume(api_key=api_key, event_id=event_id)
    except billing.InvalidApiKey:
        return jsonify({"error": "invalid_api_key"}), 401
    except billing.InsufficientCredits:
        return jsonify({"error": "insufficient_credits"}), 402
    except billing.EventIdConflict:
        return jsonify({"error": "event_id_conflict"}), 409
    except DatabaseError:
        return jsonify({"error": "billing_database_unavailable"}), 503

    result["event_id"] = usage["event_id"]
    result["credits_charged"] = usage["credits_charged"]
    result["credits_remaining"] = usage["credits_remaining"]
    result["idempotent_replay"] = usage["idempotent_replay"]
    return jsonify(result)


@app.route("/usage")
def usage():
    api_key = get_api_key_from_request()
    if not api_key:
        return jsonify({"error": "missing_api_key"}), 401

    try:
        summary = billing.usage(api_key)
    except billing.InvalidApiKey:
        return jsonify({"error": "invalid_api_key"}), 401
    except DatabaseError:
        return jsonify({"error": "billing_database_unavailable"}), 503

    return jsonify(summary)


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    secrets_to_try = [
        value
        for value in (
            STRIPE_WEBHOOK_SECRET_TEST,
            STRIPE_WEBHOOK_SECRET_LIVE,
            STRIPE_WEBHOOK_SECRET,
        )
        if value
    ]

    if not secrets_to_try:
        return jsonify({"error": "webhook_secret_not_configured"}), 503

    payload = request.get_data()
    signature = request.headers.get("Stripe-Signature", "")

    event = None
    saw_bad_payload = False

    for webhook_secret in secrets_to_try:
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                webhook_secret,
            )
            break
        except ValueError:
            saw_bad_payload = True
            break
        except stripe.error.SignatureVerificationError:
            continue

    if saw_bad_payload:
        return jsonify({"error": "invalid_payload"}), 400

    if event is None:
        return jsonify({"error": "invalid_signature"}), 400

    event_type = event.get("type", "")

    if event_type in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
    }:
        session = event["data"]["object"]

        if event_type == "checkout.session.async_payment_succeeded":
            effective_payment_status = "paid"
        elif event_type == "checkout.session.async_payment_failed":
            effective_payment_status = "failed"
        else:
            effective_payment_status = session.get("payment_status", "unknown")

        credits = purchase_credits_from_session(session) if effective_payment_status == "paid" else 0

        try:
            process_stripe_event(
                event_id=event["id"],
                event_type=event_type,
                livemode=bool(event.get("livemode", False)),
                session_id=session["id"],
                email=((session.get("customer_details") or {}).get("email") or session.get("customer_email")),
                amount_total=int(session.get("amount_total") or 0),
                currency=str(session.get("currency") or "usd"),
                payment_status=effective_payment_status,
                credits=credits,
            )
        except DatabaseError:
            return jsonify({"error": "database_unavailable"}), 500

    return jsonify({"received": True}), 200


@app.route("/claim", methods=["GET", "POST"])
def claim():
    if request.method == "GET":
        session_id = request.args.get("session_id", "").strip()
        if not session_id:
            return Response("<h1>Missing Checkout Session ID.</h1>", status=400, mimetype="text/html")

        try:
            state = billing.purchase_status(session_id)
        except DatabaseError:
            return Response("<h1>The billing database is temporarily unavailable.</h1>", status=503, mimetype="text/html")

        if state is None:
            return Response("<h1>Payment confirmation is still arriving.</h1><p>Wait a few seconds and refresh.</p>", status=202, mimetype="text/html")
        if state["payment_status"] != "paid":
            return Response("<h1>Your payment is not confirmed as paid yet.</h1>", status=202, mimetype="text/html")
        if state["claimed"]:
            return Response("<h1>This purchase was already claimed.</h1><p>For security, API keys are shown only once.</p>", status=409, mimetype="text/html")

        safe_session = html.escape(session_id, quote=True)
        return Response(
            f"""
            <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Claim Aeon API credits</title></head>
            <body><h1>Payment confirmed</h1><p>This purchase contains <strong>{state['credits']}</strong> Aeon API credits.</p>
            <p>Your API key will be displayed exactly once. Save it somewhere secure.</p>
            <form method="post" action="/claim"><input type="hidden" name="session_id" value="{safe_session}"><button type="submit">Reveal my API key</button></form></body></html>
            """,
            status=200,
            mimetype="text/html",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"},
        )

    session_id = request.form.get("session_id", "").strip()
    if not session_id:
        return Response("<h1>Missing Checkout Session ID.</h1>", status=400, mimetype="text/html")

    try:
        claimed = claim_purchase(session_id)
    except PurchaseNotReady:
        return Response("<h1>Payment confirmation is still processing.</h1><p>Please go back and try again shortly.</p>", status=202, mimetype="text/html")
    except PurchaseAlreadyClaimed:
        return Response("<h1>This purchase was already claimed.</h1><p>For security, API keys are shown only once.</p>", status=409, mimetype="text/html")
    except DatabaseError:
        return Response("<h1>The billing database is temporarily unavailable.</h1>", status=503, mimetype="text/html")

    api_key = html.escape(claimed["api_key"])
    email = html.escape(claimed["email"] or "Stripe customer")
    prefix = html.escape(claimed["api_key_prefix"])

    return Response(
        f"""
        <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Aeon API key</title></head>
        <body><h1>Your Aeon API key</h1><p>Customer: {email}</p><p>Credits available: <strong>{claimed['credits_remaining']}</strong></p>
        <p>Save this key now. It will not be displayed again.</p><pre>{api_key}</pre><p>Key prefix: {prefix}</p>
        <h2>Example request</h2><pre>curl -X POST https://aeon-workspace.onrender.com/detect \\
  -H "Authorization: Bearer YOUR_AEON_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"output":"Hello world","event_id":"example-001"}}'</pre></body></html>
        """,
        status=200,
        mimetype="text/html",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"},
    )


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "route_not_found"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "internal_server_error"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
