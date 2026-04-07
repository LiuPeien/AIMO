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


def test_chat_evolve_mode():
    init_db()
    client = TestClient(app)
    res = client.post('/api/chat', json={
        'model_id': 'anthropic.claude-3-5-sonnet-20240620-v1:0',
        'message': '新增一个总结能力模块',
        'mode': 'evolve'
    })
    assert res.status_code == 200
    assert res.json()['mode'] == 'evolve'


def test_session_update_and_delete():
    init_db()
    client = TestClient(app)
    created = client.post('/api/sessions', json={'title': 'old'}).json()
    sid = created['session_id']

    updated = client.patch(f'/api/sessions/{sid}', json={'title': 'new title'})
    assert updated.status_code == 200
    assert updated.json()['title'] == 'new title'

    deleted = client.delete(f'/api/sessions/{sid}')
    assert deleted.status_code == 200

    sessions = client.get('/api/sessions').json()
    assert all(item['id'] != sid for item in sessions)
