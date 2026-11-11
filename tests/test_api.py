from fastapi.testclient import TestClient

from omni.api.app import app


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_task_submission():
    with TestClient(app) as client:
        response = client.post('/tasks', json={'goal': 'Create a demo project.'})
    assert response.status_code == 200
    data = response.json()
    assert 'task_id' in data
    assert len(data['plan']) > 0
