# Agent Output Anomaly Detector

A self-service API that monitors agent outputs for anomalies and drift. Designed for ML engineers and QA teams.

## Features
- Per-event pricing: $0.003 per detection
- Free tier: 1000 events/month
- Idempotent billing
- Simple rule-based anomaly detection (configurable)

## API Endpoints
- `POST /detect`: Submit agent output for anomaly detection
- `GET /health`: Service health check
- `GET /usage`: Get usage and balance for a customer

## Quick Start
1. Install dependencies: `pip install -r requirements.txt`
2. Run: `python app.py`
3. Send a detection request:

curl -X POST http://localhost:8080/detect \
  -H "Content-Type: application/json" \
  -d '{"output": "Hello world", "customer_id": "test"}'

## Configuration
- Pricing: Edit `pricing.yaml`
- Billing adapter: Edit `billing_adapter.py` to integrate a payment provider

## Testing
Run the billing tests: `python -m unittest test_billing.py`

## License
MIT
