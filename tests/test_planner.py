from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.planner import build_structured_plan


def test_build_structured_plan_fields():
    plan = build_structured_plan(
        user_goal='给项目添加一个模块并补充测试',
        involved_files=['app/main.py', 'tests/test_api.py'],
    )
    assert plan['planner_version'] == 'v1'
    assert plan['user_goal']
    assert plan['involved_files'] == ['app/main.py', 'tests/test_api.py']
    assert 'expected_new_modules' in plan
    assert 'potential_modification_points' in plan
    assert plan['risk_level'] in {'low', 'medium', 'high'}
    assert len(plan['verification_plan']) >= 1
