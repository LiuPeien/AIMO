from fastapi.testclient import TestClient

from app.main import app, build_evolution_prompt, init_db


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


def test_evolution_prompt_contains_controlled_rules():
    prompt = build_evolution_prompt('新增一个代码分析能力')
    assert '受控自我管理执行规则' in prompt
    assert '先理解现状 -> 再给改动计划 -> 等用户确认' in prompt


def test_manage_plan_readonly_mode():
    init_db()
    client = TestClient(app)
    res = client.post('/api/manage/plan', json={
        'request': '请分析当前项目并给出最小改动计划',
        'focus_paths': ['app/main.py', 'README.md'],
        'max_files': 40,
    })
    assert res.status_code == 200
    data = res.json()
    assert data['mode'] == 'manage_plan'
    assert data['scope']['write_enabled'] is False
    assert 'proposed_plan' in data
    planner = data['planner']
    assert planner['planner_version'] == 'v1'
    assert planner['user_goal']
    assert 'involved_files' in planner
    assert 'expected_new_modules' in planner
    assert 'potential_modification_points' in planner
    assert planner['risk_level'] in {'low', 'medium', 'high'}
    assert 'verification_plan' in planner


def test_manage_execute_requires_confirmed_flag():
    init_db()
    client = TestClient(app)
    res = client.post('/api/manage/execute', json={
        'confirmed': False,
        'allowed_files': ['README.md'],
        'edits': [{'path': 'README.md', 'new_content': 'x'}],
    })
    assert res.status_code == 400


def test_manage_verify_endpoint_shape():
    init_db()
    client = TestClient(app)
    res = client.post('/api/manage/verify', json={'changed_files': ['app/main.py']})
    assert res.status_code == 200
    data = res.json()
    assert data['mode'] == 'manage_verify'
    assert 'verification' in data


def test_manage_workflow_plan_shape():
    init_db()
    client = TestClient(app)
    res = client.post('/api/manage/workflow', json={
        'step': 'plan',
        'request': '给项目增加一个简单日志模块'
    })
    assert res.status_code == 200
    data = res.json()
    assert data['workflow_stage'] == 'plan_generated'
    assert data['next_stage'] == 'execute'
