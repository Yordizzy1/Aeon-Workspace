import unittest
from billing_adapter import BillingAdapter

class TestBilling(unittest.TestCase):
    def setUp(self):
        self.pricing = {'unit_price': 0.003}
        self.adapter = BillingAdapter(self.pricing)

    def test_charge_idempotent(self):
        amount1 = self.adapter.charge('cust1', 'evt1', {})
        amount2 = self.adapter.charge('cust1', 'evt1', {})
        self.assertEqual(amount1, amount2)

    def test_get_balance(self):
        self.adapter.charge('cust2', 'evt2', {})
        balance = self.adapter.get_balance('cust2')
        self.assertEqual(balance, 0.003)

if __name__ == '__main__':
    unittest.main()
