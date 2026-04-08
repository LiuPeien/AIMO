from fastapi.testclient import TestClient

from app.main import app, init_db


def test_agent_plan_endpoint():
    init_db()
    client = TestClient(app)
    res = client.post('/api/agent/plan', json={'request': '请先分析项目并给出最小改动计划'})
    assert res.status_code == 200
    data = res.json()
    assert data['mode'] == 'agent_plan'
    assert data['next_stage'] == 'await_confirm'
    assert data['plan']['need_confirmation'] is True


def test_agent_execute_requires_confirmation():
    init_db()
    client = TestClient(app)
    res = client.post('/api/agent/execute', json={
        'confirmed': False,
        'actions': [{'tool': 'list_dir', 'args': {'path': '.'}}],
    })
    assert res.status_code == 400


def test_agent_execute_allows_read_tool():
    init_db()
    client = TestClient(app)
    res = client.post('/api/agent/execute', json={
        'confirmed': True,
        'actions': [{'tool': 'list_dir', 'args': {'path': '.'}}],
    })
    assert res.status_code == 200
    assert res.json()['mode'] == 'agent_execute'
