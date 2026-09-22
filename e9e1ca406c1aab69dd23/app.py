from flask import Flask, request, jsonify, Response, make_response, redirect
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timezone
import html
import json
import os
import re

import stripe
import yaml
from urllib.parse import urlparse

from billing_adapter import BillingAdapter
from database import (
    DatabaseError,
    PurchaseAlreadyClaimed,
    PurchaseNotReady,
    claim_purchase,
    db_health,
    get_public_business_metrics,
    get_acquisition_metrics,
    attribute_purchase_source,
    record_acquisition_event,
    ensure_acquisition_schema,
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
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://aeon-workspace.onrender.com"
).strip().rstrip("/")
INDEXNOW_KEY = "ebc79b0ab2b9072a00d8e592f9aad85c"
ACQUISITION_COOKIE = "aeon_acquisition_source"
_SOURCE_RE = re.compile(r"[^a-z0-9_-]+")
_BOT_RE = re.compile(
    r"bot|crawler|spider|slurp|preview|headless|wget|curl|python-requests|"
    r"facebookexternalhit|linkedinbot|twitterbot|bingpreview|googlebot",
    re.IGNORECASE,
)

billing = BillingAdapter()
init_db()
ensure_acquisition_schema()



def normalize_acquisition_source(value):
    value = str(value or '').strip().lower()[:80]
    value = _SOURCE_RE.sub('-', value).strip('-_')
    return value[:64] or 'direct'


def is_probable_bot():
    return bool(_BOT_RE.search(str(request.headers.get('User-Agent') or '')))


def referrer_source():
    explicit = request.args.get('src')
    if explicit:
        return normalize_acquisition_source(explicit)
    cookie = request.cookies.get(ACQUISITION_COOKIE)
    if cookie:
        return normalize_acquisition_source(cookie)
    ref = str(request.referrer or '').strip()
    if not ref:
        return 'direct'
    try:
        host = (urlparse(ref).hostname or '').lower()
    except ValueError:
        return 'other'
    if 'google.' in host:
        return 'google'
    if host.endswith('bing.com'):
        return 'bing'
    if host.endswith('github.com'):
        return 'github'
    if host.endswith('postman.com'):
        return 'postman'
    if host.endswith('producthunt.com'):
        return 'producthunt'
    if host.endswith('news.ycombinator.com'):
        return 'showhn'
    return 'referral'


def set_source_cookie(response, source):
    response.set_cookie(
        ACQUISITION_COOKIE,
        normalize_acquisition_source(source),
        max_age=60 * 60 * 24 * 30,
        secure=True,
        httponly=True,
        samesite='Lax',
    )
    return response


def safe_acquisition_event(event_type, source, reference=None):
    try:
        record_acquisition_event(event_type, source, reference=reference)
    except DatabaseError:
        # Analytics must never block checkout, claim, or API service.
        pass


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
    source = referrer_source()
    bot = is_probable_bot()
    safe_acquisition_event('crawler_hit' if bot else 'landing_view', source)
    buy_url = f"/go/{source}" if PAYMENT_LINK_URL else ""
    schema_json = json.dumps({
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Aeon Agent Output Anomaly Detector",
        "applicationCategory": "DeveloperApplication",
        "operatingSystem": "Web API",
        "url": PUBLIC_BASE_URL + "/",
        "description": (
            "Deterministic API guardrail for AI-agent outputs: empty output, "
            "oversized output, and error-keyword detection with prepaid usage credits."
        ),
        "offers": {
            "@type": "Offer",
            "price": str(UNIT_PRICE_USD),
            "priceCurrency": "USD",
            "description": "Per detection event",
        },
    })
    buy_html = (
        f'<a class="cta" href="{html.escape(buy_url, quote=True)}">Buy API credits</a>'
        if buy_url
        else '<span class="cta disabled">Purchasing temporarily unavailable</span>'
    )
    body = f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>AI Agent Output Anomaly Detector API | Aeon</title>
        <meta name="description" content="Deterministic AI-agent output guardrail API for catching empty, oversized, and error-containing agent responses before downstream automation consumes them.">
        <meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1">
        <link rel="canonical" href="{PUBLIC_BASE_URL}/">
        <link rel="alternate" type="application/json" href="{PUBLIC_BASE_URL}/openapi.json" title="OpenAPI">
        <link rel="alternate" type="text/plain" href="{PUBLIC_BASE_URL}/llms.txt" title="LLM discovery">
        <meta property="og:title" content="Aeon Agent Output Anomaly Detector API">
        <meta property="og:description" content="A deterministic, machine-facing guardrail for AI-agent outputs. $0.003 per detection event.">
        <meta property="og:type" content="website">
        <meta property="og:url" content="{PUBLIC_BASE_URL}/">
        <script type="application/ld+json">{schema_json}</script>
        <style>
          :root {{ color-scheme: dark; }}
          body {{ margin:0; font-family:Inter,system-ui,Arial,sans-serif; background:#071018; color:#eafaff; }}
          main {{ max-width:980px; margin:0 auto; padding:64px 24px 80px; }}
          .eyebrow {{ color:#63e6be; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }}
          h1 {{ font-size:clamp(38px,7vw,72px); line-height:1.02; margin:10px 0 18px; max-width:900px; }}
          .lead {{ font-size:20px; color:#b8cad5; max-width:780px; line-height:1.6; }}
          .actions {{ display:flex; flex-wrap:wrap; gap:12px; margin:28px 0 34px; }}
          .cta {{ display:inline-block; padding:13px 18px; border-radius:10px; background:#63e6be; color:#04100d; font-weight:900; text-decoration:none; }}
          .secondary {{ background:#142632; color:#d9f7ff; }}
          .disabled {{ opacity:.55; }}
          .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:28px 0; }}
          .card {{ border:1px solid #24404f; background:#0b1922; border-radius:12px; padding:18px; }}
          .card b {{ display:block; margin-bottom:6px; }}
          code,pre {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }}
          pre {{ overflow:auto; padding:16px; border:1px solid #24404f; background:#061018; border-radius:10px; color:#c9f6ff; }}
          a {{ color:#71ddff; }}
          .fine {{ color:#7f98a6; font-size:13px; line-height:1.5; }}
          @media(max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} }}
        </style>
      </head>
      <body>
        <main>
          <div class="eyebrow">AI-agent output guardrail API</div>
          <h1>Stop broken agent outputs before they break the next step.</h1>
          <p class="lead">Aeon provides a deterministic, low-cost validation endpoint for autonomous workflows. Catch empty responses, runaway output size, and explicit error signals before downstream tools, databases, or agents consume them.</p>
          <div class="actions">
            {buy_html}
            <a class="cta secondary" href="/docs?src={source}">Read API docs</a>
            <a class="cta secondary" href="/openapi.json">OpenAPI 3.1</a>
          </div>
          <div class="grid">
            <div class="card"><b>Deterministic</b>No model call is needed for the current checks, so results are fast and repeatable.</div>
            <div class="card"><b>Agent-ready</b>Bearer-key API, idempotent event IDs, OpenAPI, llms.txt, and agent discovery metadata.</div>
            <div class="card"><b>Usage priced</b>${UNIT_PRICE_USD} per detection event through prepaid credits. No recurring human fulfillment loop.</div>
          </div>
          <h2>One request</h2>
          <pre>curl -X POST {PUBLIC_BASE_URL}/detect \\
  -H "Authorization: Bearer YOUR_AEON_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"output":"Agent response here","event_id":"run-001"}}'</pre>
          <h2>Designed for</h2>
          <div class="grid">
            <div class="card"><b>Agent pipelines</b>Gate model/tool output before another autonomous step executes.</div>
            <div class="card"><b>CI and regression checks</b>Add a cheap deterministic sanity layer around agent-generated artifacts.</div>
            <div class="card"><b>Workflow reliability</b>Use stable event IDs so retries do not double-consume credits.</div>
          </div>
          <p class="fine">The current detector is intentionally narrow: empty output, outputs above 10,000 characters, and outputs containing the word ERROR. The public telemetry never exposes customer emails, API keys, Stripe secrets, or database credentials.</p>
          <p><a href="/health">Health</a> · <a href="/docs?src={source}">Docs</a> · <a href="/openapi.json">OpenAPI</a> · <a href="/llms.txt">llms.txt</a> · <a href="/agents.json">agents.json</a></p>
        </main>
      </body>
    </html>
    """
    response = make_response(body)
    if not bot:
        set_source_cookie(response, source)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route("/docs")
def docs():
    source = referrer_source()
    bot = is_probable_bot()
    safe_acquisition_event('crawler_hit' if bot else 'docs_view', source)
    body = f"""
    <!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Aeon API Documentation</title><meta name="description" content="Documentation for the Aeon Agent Output Anomaly Detector API.">
    <style>body{{font-family:system-ui,Arial,sans-serif;max-width:900px;margin:50px auto;padding:0 22px;line-height:1.65}}pre{{padding:14px;background:#f3f5f6;overflow:auto}}code{{font-family:ui-monospace,Consolas,monospace}}</style></head><body>
    <h1>Aeon Agent Output Anomaly Detector API</h1>
    <p>Base URL: <code>{PUBLIC_BASE_URL}</code></p>
    <p>Price: <strong>${UNIT_PRICE_USD} per detection event</strong> through prepaid credits.</p>
    <h2>Authentication</h2><p>Use <code>Authorization: Bearer YOUR_AEON_API_KEY</code>.</p>
    <h2>POST /detect</h2>
    <pre>curl -X POST {PUBLIC_BASE_URL}/detect \\
  -H "Authorization: Bearer YOUR_AEON_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"output":"Hello world","event_id":"example-001"}}'</pre>
    <p>Each new event ID consumes one credit. Repeating the same event ID is idempotent and does not consume another credit.</p>
    <h2>GET /usage</h2><p>Returns remaining credits and usage for the authenticated key.</p>
    <h2>Detection rules</h2><ul><li>empty output</li><li>output longer than 10,000 characters</li><li>output containing the word <code>ERROR</code>, case-insensitive</li></ul>
    <h2>Machine-readable discovery</h2><p><a href="/openapi.json">OpenAPI 3.1</a> · <a href="/llms.txt">llms.txt</a> · <a href="/agents.txt">agents.txt</a> · <a href="/agents.json">agents.json</a></p>
    <p><a href="/go/{source}">Buy prepaid credits</a> · <a href="/">Home</a></p>
    </body></html>
    """
    response = make_response(body)
    if not bot:
        set_source_cookie(response, source)
    return response


@app.route("/go/<source>")
def acquisition_go(source):
    source = normalize_acquisition_source(source)
    if not PAYMENT_LINK_URL:
        return jsonify({"error": "payment_link_not_configured"}), 503
    if not is_probable_bot():
        safe_acquisition_event('checkout_click', source)
    response = redirect(PAYMENT_LINK_URL, code=302)
    set_source_cookie(response, source)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route("/openapi.json")
def openapi_spec():
    return jsonify({
        "openapi": "3.1.0",
        "info": {
            "title": "Aeon Agent Output Anomaly Detector API",
            "version": "1.0.0",
            "description": "Deterministic guardrail API for AI-agent outputs.",
        },
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/health": {
                "get": {"operationId": "health", "summary": "Service and database health", "responses": {"200": {"description": "Healthy"}}}
            },
            "/detect": {
                "post": {
                    "operationId": "detectAgentOutputAnomaly",
                    "summary": "Detect basic anomalies in an AI-agent output",
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["output"],
                            "properties": {
                                "output": {"type": "string"},
                                "event_id": {"type": "string", "description": "Idempotency key for one billable event"},
                            },
                        }}},
                    },
                    "responses": {
                        "200": {"description": "Detection result and remaining credits"},
                        "401": {"description": "Missing or invalid API key"},
                        "402": {"description": "Insufficient prepaid credits"},
                        "409": {"description": "Event ID belongs to a different customer"},
                    },
                }
            },
            "/usage": {
                "get": {
                    "operationId": "getUsage",
                    "summary": "Get authenticated usage and remaining credits",
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "Usage summary"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
    })


@app.route("/llms.txt")
def llms_txt():
    text = f"""# Aeon Agent Output Anomaly Detector\n\n> Deterministic, machine-facing API for detecting basic AI-agent output anomalies before downstream automation consumes them.\n\n## Core URLs\n- Home: {PUBLIC_BASE_URL}/\n- Docs: {PUBLIC_BASE_URL}/docs\n- OpenAPI: {PUBLIC_BASE_URL}/openapi.json\n- Health: {PUBLIC_BASE_URL}/health\n- Purchase credits: {PUBLIC_BASE_URL}/go/llms\n\n## API\n- POST /detect — authenticated anomaly detection; one prepaid credit per new event_id.\n- GET /usage — authenticated remaining credits and usage.\n\n## Current deterministic checks\n- empty output\n- output longer than 10,000 characters\n- output containing ERROR, case-insensitive\n\n## Pricing\n${UNIT_PRICE_USD} per detection event through prepaid credits.\n"""
    return Response(text, mimetype='text/plain')


@app.route("/agents.txt")
def agents_txt():
    text = f"""# Aeon\nSITE: {PUBLIC_BASE_URL}/\nOPENAPI: {PUBLIC_BASE_URL}/openapi.json\nLLMS: {PUBLIC_BASE_URL}/llms.txt\nPAYMENTS: {PUBLIC_BASE_URL}/go/agents\nAUTH: Bearer API key after prepaid-credit purchase\nCAPABILITY: deterministic AI-agent output anomaly detection\n"""
    return Response(text, mimetype='text/plain')


@app.route("/agents.json")
def agents_json():
    return jsonify({
        "version": "1.0",
        "site": {"name": "Aeon Agent Output Anomaly Detector", "url": PUBLIC_BASE_URL + "/"},
        "capabilities": [{
            "name": "agent_output_anomaly_detection",
            "description": "Detect empty, oversized, or ERROR-containing agent outputs.",
            "openapi": PUBLIC_BASE_URL + "/openapi.json",
            "docs": PUBLIC_BASE_URL + "/docs",
        }],
        "payments": {"purchase_url": PUBLIC_BASE_URL + "/go/agents"},
    })


@app.route("/.well-known/api-catalog")
def api_catalog():
    return jsonify({
        "schema": "aeon.api_catalog.v1",
        "name": "Aeon Agent Output Anomaly Detector",
        "homepage": PUBLIC_BASE_URL + "/",
        "docs": PUBLIC_BASE_URL + "/docs",
        "openapi": PUBLIC_BASE_URL + "/openapi.json",
        "llms": PUBLIC_BASE_URL + "/llms.txt",
        "agents": PUBLIC_BASE_URL + "/agents.json",
        "health": PUBLIC_BASE_URL + "/health",
    })


@app.route("/robots.txt")
def robots_txt():
    return Response(
        f"User-agent: *\\nAllow: /\\nSitemap: {PUBLIC_BASE_URL}/sitemap.xml\\n",
        mimetype='text/plain',
    )


@app.route("/sitemap.xml")
def sitemap_xml():
    urls = ["/", "/docs", "/openapi.json", "/llms.txt", "/agents.txt", "/agents.json"]
    items = ''.join(f'<url><loc>{PUBLIC_BASE_URL}{path}</loc></url>' for path in urls)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + items + '</urlset>',
        mimetype='application/xml',
    )


@app.route(f"/{INDEXNOW_KEY}.txt")
def indexnow_key_file():
    return Response(INDEXNOW_KEY, mimetype='text/plain')


@app.route("/aeon/acquisition")
def aeon_acquisition():
    try:
        metrics = get_acquisition_metrics()
    except DatabaseError:
        return jsonify({
            "schema": "aeon.acquisition_metrics.v1",
            "status": "degraded",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "funnel": {},
            "sources": [],
            "error": "metrics_query_failed",
        }), 200

    funnel = {
        "landing_views": metrics["landing_views"],
        "docs_views": metrics["docs_views"],
        "checkout_clicks": metrics["checkout_clicks"],
        "live_paid_checkouts": metrics["live_paid_checkouts"],
        "live_paid_gross_usd": metrics["live_paid_gross_usd"],
        "api_key_claims": metrics["api_key_claims"],
        "crawler_hits": metrics["crawler_hits"],
        "tracked_click_to_paid_rate": metrics["tracked_click_to_paid_rate"],
    }
    return jsonify({
        "schema": "aeon.acquisition_metrics.v1",
        "status": "healthy",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "funnel": funnel,
        "sources": metrics["sources"],
        "last_event_at": metrics["last_event_at"],
        "discovery_surfaces": [
            PUBLIC_BASE_URL + "/sitemap.xml",
            PUBLIC_BASE_URL + "/openapi.json",
            PUBLIC_BASE_URL + "/llms.txt",
            PUBLIC_BASE_URL + "/agents.txt",
            PUBLIC_BASE_URL + "/agents.json",
            PUBLIC_BASE_URL + "/.well-known/api-catalog",
        ],
        "truth_semantics": (
            "LANDING_AND_CLICK_COUNTS_EXCLUDE_RECOGNIZED_BOTS; "
            "PAID_CHECKOUTS_ARE_STRIPE_SIGNED LIVE PAID PURCHASES; "
            "SOURCE_ATTRIBUTION_IS FIRST-PARTY COOKIE/REDIRECT ATTRIBUTION AND MAY BE UNATTRIBUTED; "
            "NO_IPS_PII_OR_CREDENTIALS_RETURNED"
        ),
    })


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
        source = normalize_acquisition_source(request.cookies.get(ACQUISITION_COOKIE) or 'direct')
        try:
            attribute_purchase_source(session_id, source)
            record_acquisition_event('paid_return', source, reference=session_id)
        except DatabaseError:
            pass
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

    source = normalize_acquisition_source(request.cookies.get(ACQUISITION_COOKIE) or 'direct')
    safe_acquisition_event('api_key_claim', source, reference=session_id)

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
