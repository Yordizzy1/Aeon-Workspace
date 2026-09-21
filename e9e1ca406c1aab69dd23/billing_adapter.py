class BillingAdapter:
    def __init__(self, pricing_config):
        self.pricing = pricing_config
        self.balances = {}
        self.charges = {}

    def charge(self, customer_id, event_id, result):
        unit_price = self.pricing.get("unit_price", 0.003)
        amount = unit_price

        if event_id and event_id in self.charges:
            return self.charges[event_id]

        if event_id:
            self.charges[event_id] = amount

        self.balances[customer_id] = (
            self.balances.get(customer_id, 0.0) + amount
        )

        return amount

    def get_balance(self, customer_id):
        return self.balances.get(customer_id, 0.0)