import pytest
from app import detect_anomaly

def test_basic():
    res = detect_anomaly("hello world")
    assert 'anomaly_score' in res
    assert res['drift_flag'] == False

def test_drift():
    res = detect_anomaly("very long text", baseline=0.1)
    # score likely >0.5
    assert res['drift_flag'] == True