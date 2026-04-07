from fastapi.testclient import TestClient

from app.main import app, init_db


def test_models_endpoint():
    init_db()
    client = TestClient(app)
    data = client.get('/api/models').json()
    assert 'models' in data


def test_session_create_and_chat_mock():
    init_db()
    client = TestClient(app)
    sid = client.post('/api/sessions', json={'title': 't'}).json()['session_id']
    res = client.post('/api/chat', json={
        'session_id': sid,
        'model_id': 'anthropic.claude-3-5-sonnet-20240620-v1:0',
        'message': '你好'
    })
    assert res.status_code == 200
    assert 'reply' in res.json()
