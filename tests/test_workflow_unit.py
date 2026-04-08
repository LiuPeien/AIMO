from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import ManageWorkflowRequest, run_manage_workflow


def test_workflow_plan_stage_returns_next_execute():
    payload = ManageWorkflowRequest(step='plan', request='给项目增加一个简单日志模块')
    result = run_manage_workflow(payload)
    assert result['workflow_stage'] == 'plan_generated'
    assert result['next_stage'] == 'execute'
    assert 'data' in result
    assert result['data']['mode'] == 'manage_plan'


def test_workflow_plan_requires_request():
    payload = ManageWorkflowRequest(step='plan', request='')
    with pytest.raises(HTTPException):
        run_manage_workflow(payload)
