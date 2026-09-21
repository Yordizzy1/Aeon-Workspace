from flask import Flask, request, jsonify
import yaml
from billing_adapter import BillingAdapter

app = Flask(__name__)

# Load pricing configuration
with open("pricing.yaml", "r") as f:
    pricing = yaml.safe_load(f)

billing = BillingAdapter(pricing)


def detect_anomaly(agent_output):
    """
    Basic deterministic anomaly detection.

    Flags:
    - Empty output
    - Extremely long output
    - Outputs containing the word ERROR
    """

    if not agent_output:
        return {
            "anomaly": True,
            "reason": "empty_output",
        }

    if len(agent_output) > 10000:
        return {
            "anomaly": True,
            "reason": "output_too_long",
        }

    if "ERROR" in agent_output.upper():
        return {
            "anomaly": True,
            "reason": "contains_error_keyword",
        }

    return {
        "anomaly": False,
        "reason": "ok",
    }


@app.route("/")
def home():
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">

        <title>Aeon — Agent Output Anomaly Detector</title>

        <style>
          body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 60px auto;
            padding: 0 24px;
            line-height: 1.6;
          }

          h1 {
            margin-bottom: 0;
          }

          h2 {
            margin-top: 6px;
            font-weight: normal;
          }

          code {
            background: #f3f3f3;
            padding: 3px 6px;
            border-radius: 4px;
          }

          .status {
            margin-top: 30px;
          }
        </style>
      </head>

      <body>

        <h1>Aeon</h1>

        <h2>Agent Output Anomaly Detector</h2>

        <p>
          Aeon provides a machine-facing API for detecting anomalies
          in AI-agent outputs.
        </p>

        <p>
          The service can identify malformed, empty, excessively large,
          or error-containing agent responses before downstream systems
          consume them.
        </p>

        <h3>Pricing</h3>

        <p>
          $0.003 per detection event.
        </p>

        <h3>API Endpoint</h3>

        <p>
          <code>POST /detect</code>
        </p>

        <p>
          Example JSON body:
        </p>

        <pre>
{
  "output": "Agent response here",
  "customer_id": "customer_123",
  "event_id": "event_001"
}
        </pre>

        <h3>Usage</h3>

        <p>
          <code>GET /usage?customer_id=customer_123</code>
        </p>

        <div class="status">

          <h3>Service Status</h3>

          <p>
            <a href="/health">Check service health</a>
          </p>

        </div>

      </body>
    </html>
    """


@app.route("/detect", methods=["POST"])
def detect():
    data = request.get_json(silent=True)

    if not data or "output" not in data:
        return jsonify({
            "error": "missing output field",
        }), 400

    agent_output = data["output"]

    customer_id = data.get(
        "customer_id",
        "anonymous",
    )

    event_id = data.get(
        "event_id",
    )

    result = detect_anomaly(
        agent_output,
    )

    amount = billing.charge(
        customer_id,
        event_id,
        result,
    )

    result["charged_usd"] = amount

    return jsonify(
        result,
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "Aeon Agent Output Anomaly Detector",
    })


@app.route("/usage")
def usage():
    customer_id = request.args.get(
        "customer_id",
        "anonymous",
    )

    balance = billing.get_balance(
        customer_id,
    )

    return jsonify({
        "customer_id": customer_id,
        "balance_usd": balance,
    })


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "route_not_found",
    }), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "internal_server_error",
    }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
    )