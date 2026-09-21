import unittest
from app import app

class TestDetect(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
    def test_missing_text(self):
        resp = self.client.post('/v1/detect', json={})
        self.assertEqual(resp.status_code, 400)
    def test_basic_score(self):
        resp = self.client.post('/v1/detect', json={'text': 'hello'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('anomaly_score', data)
    def test_idempotency(self):
        headers = {'Idempotency-Key': 'test-key'}
        resp1 = self.client.post('/v1/detect', json={'text': 'hello'}, headers=headers)
        resp2 = self.client.post('/v1/detect', json={'text': 'hello'}, headers=headers)
        self.assertEqual(resp1.get_json(), resp2.get_json())

if __name__ == '__main__':
    unittest.main()
