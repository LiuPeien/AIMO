from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.verifier import run_post_change_verification


def test_verifier_reports_unverifiable_without_tests(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "foo.py").write_text("x = 1\n", encoding="utf-8")

    result = run_post_change_verification(project, ["foo.py"])

    names = {item["name"]: item["status"] for item in result["checks"]}
    assert names["tests"] == "unverifiable"
    assert names["syntax"] in {"success", "failed", "unverifiable"}
    assert "overall_status" in result


def test_verifier_syntax_failure_on_bad_python(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    bad = project / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")

    result = run_post_change_verification(project, ["bad.py"])

    syntax = next(item for item in result["checks"] if item["name"] == "syntax")
    assert syntax["status"] == "failed"
    assert result["overall_status"] == "failed"
