from flask import Flask, request, jsonify
import yaml
import os
from billing_adapter import BillingAdapter

app = Flask(__name__)

# Load pricing config
with open('pricing.yaml', 'r') as f:
    pricing = yaml.safe_load(f)

billing = BillingAdapter(pricing)

# Simple anomaly detection logic
def detect_anomaly(agent_output):
    # Placeholder: check for empty output, length > 10000, or contains "ERROR"
    if not agent_output:
        return {"anomaly": True, "reason": "empty_output"}
    if len(agent_output) > 10000:
        return {"anomaly": True, "reason": "output_too_long"}
    if "ERROR" in agent_output.upper():
        return {"anomaly": True, "reason": "contains_error_keyword"}
    return {"anomaly": False, "reason": "ok"}

@app.route('/detect', methods=['POST'])
def detect():
    data = request.get_json()
    if not data or 'output' not in data:
        return jsonify({"error": "missing output field"}), 400
    customer_id = data.get('customer_id', 'anonymous')
    event_id = data.get('event_id', None)
    result = detect_anomaly(data['output'])
    # Metering
    amount = billing.charge(customer_id, event_id, result)
    result['charged_usd'] = amount
    return jsonify(result)

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/usage')
def usage():
    customer_id = request.args.get('customer_id', 'anonymous')
    balance = billing.get_balance(customer_id)
    return jsonify({"customer_id": customer_id, "balance_usd": balance})
    @app.route('/')
def home():
    return """
    <!doctype html>
    <html>
      <head>
        <title>Aeon — Agent Output Anomaly Detector</title>
      </head>
      <body>
        <h1>Aeon</h1>
        <h2>Agent Output Anomaly Detector</h2>

        <p>
          Aeon provides a machine-facing API for detecting anomalies
          in AI-agent outputs.
        </p>

        <h3>Pricing</h3>
        <p>$0.003 per detection event.</p>

        <h3>API</h3>
        <p>POST /detect</p>

        <h3>Service status</h3>
        <p><a href="/health">Health check</a></p>
      </body>
    </html>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
