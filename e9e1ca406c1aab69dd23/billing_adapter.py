import uuid
import time

class BillingAdapter:
    def __init__(self, pricing_config):
        self.pricing = pricing_config
        self_balances = {}  # customer_id -> balance
        self.charges = {}   # event_id -> amount

    def charge(self, customer_id, event_id, result):
        # Determine amount based on pricing and result
        unit_price = self.pricing.get('unit_price', 0.003)
        # For demo, charge per detection regardless of result
        amount = unit_price
        # Apply volume tiers if available
        # ... (simplified)
        # Idempotency: if event_id provided and already charged, return existing amount
        if event_id and event_id in self.charges:
            return self.charges[event_id]
        # Record charge
        if event_id:
            self.charges[event_id] = amount
        # Update balance
        self_balances[customer_id] = self_balances.get(customer_id, 0.0) + amount
        return amount

    def get_balance(self, customer_id):
        return self_balances.get(customer_id, 0.0)
