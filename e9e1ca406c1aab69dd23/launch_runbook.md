# Launch Runbook: Agent Output Anomaly Detector

## Overview
Self-service machine-native business for detecting anomalies in agent outputs. Pay-per-event pricing.

## Prerequisites
- Python 3.8+
- Docker (optional)
- Payment account (Stripe, PayPal, etc.)

## Deployment Steps
1. Clone or download the source files.
2. Install dependencies: `pip install -r requirements.txt`
3. Configure payment provider: Edit `billing_adapter.py` to replace the in-memory store with a real payment gateway SDK.
4. Start the service: `./run.sh` or `python app.py`
5. The API will be available at `http://localhost:8080`

## API Usage
- **Health Check**: `GET /health`
- **Detect Anomaly**: `POST /detect` with body `{"output": "...", "customer_id": "..."}`
- **Usage**: `GET /usage?customer_id=...`

## Billing Setup
- Obtain API keys from your payment provider.
- Update `billing_adapter.py` with provider credentials.
- Test with the provided `test_billing.py`.

## Monitoring
- The service exposes `/health` and `/usage` endpoints for self-monitoring.
- Use external monitoring tools to track uptime and errors.

## Scaling
- The Flask app can be scaled horizontally behind a load balancer.
- The in-memory billing store is not suitable for multi-process; use a database for production.

## Support
- For issues, refer to the README or contact the owner.
