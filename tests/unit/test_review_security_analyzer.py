"""Unit tests for the SecurityAnalyzer review engine."""

from pathlib import Path

import pytest

from backend.app.review.models import (
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    ReviewConfiguration,
)
from backend.app.review.security_analyzer import SecurityAnalyzer


@pytest.fixture
def analyzer() -> SecurityAnalyzer:
    """Fixture providing a fresh SecurityAnalyzer instance."""
    return SecurityAnalyzer()


def test_clean_source_produces_no_findings(analyzer: SecurityAnalyzer) -> None:
    code = """
def add(a: int, b: int) -> int:
    return a + b
"""
    findings = analyzer.analyze_source("math_ops.py", code)
    assert len(findings) == 0


def test_sql_injection_detection(analyzer: SecurityAnalyzer) -> None:
    vulnerable_code = """
def get_user(cursor, user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)

def get_by_name(conn, name):
    conn.execute("SELECT * FROM items WHERE name = '%s'" % name)

def get_by_tag(db, tag):
    db.execute("SELECT * FROM tags WHERE tag = '{}'".format(tag))

def safe_query(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
"""
    findings = analyzer.analyze_source("db.py", vulnerable_code)
    sql_findings = [f for f in findings if f.rule_id == "SEC-001"]
    assert len(sql_findings) == 3

    for f in sql_findings:
        assert f.category == FindingCategory.SECURITY
        assert f.severity == FindingSeverity.CRITICAL
        assert f.confidence == FindingConfidence.HIGH
        assert f.requires_human_review is True
        assert "injection" in f.description.lower()
        assert f.evidence is not None
        assert f.evidence.file_path == "db.py"


def test_command_injection_detection(analyzer: SecurityAnalyzer) -> None:
    code = """
import os
import subprocess

def run_user_cmd(cmd):
    subprocess.run(cmd, shell=True)
    os.system(cmd)
    os.popen("ls " + cmd)

def safe_cmd(cmd):
    subprocess.run(["ls", "-la"], shell=False)
"""
    findings = analyzer.analyze_source("runner.py", code)
    cmd_findings = [f for f in findings if f.rule_id == "SEC-002"]
    assert len(cmd_findings) == 3

    for f in cmd_findings:
        assert f.category == FindingCategory.SECURITY
        assert f.severity == FindingSeverity.CRITICAL
        assert f.requires_human_review is True
        assert "shell" in f.description.lower()


def test_dynamic_execution_detection(analyzer: SecurityAnalyzer) -> None:
    code = """
def dynamic_eval(payload):
    result = eval(payload)
    exec(payload)
    return result

def safe_eval(payload):
    import ast
    return ast.literal_eval(payload)
"""
    findings = analyzer.analyze_source("dynamic.py", code)
    exec_findings = [f for f in findings if f.rule_id == "SEC-003"]
    assert len(exec_findings) == 2

    for f in exec_findings:
        assert f.severity == FindingSeverity.CRITICAL
        assert f.requires_human_review is True
        assert "dynamic" in f.description.lower() or "eval" in f.description.lower()


def test_hardcoded_secrets_detection_and_redaction(
    analyzer: SecurityAnalyzer,
) -> None:
    # Use generic dummy tokens that do not trigger GitHub push protection
    code = """
API_KEY = "mock_secret_key_1234567890abcdef"
AUTH_TOKEN = "mock_auth_token_9876543210zyxwvu"
def connect():
    pass
"""
    findings = analyzer.analyze_source("config.py", code)
    secret_findings = [f for f in findings if f.rule_id == "SEC-004"]
    assert len(secret_findings) >= 2

    for f in secret_findings:
        assert f.category == FindingCategory.SECURITY
        assert f.severity == FindingSeverity.HIGH
        assert f.requires_human_review is True
        assert f.evidence is not None
        # Verify evidence does not leak full unredacted plaintext secret
        assert "REDACTED" in f.evidence.code_excerpt


def test_path_traversal_detection(analyzer: SecurityAnalyzer) -> None:
    code = """
def read_user_file(filename):
    with open("../" + filename, "r") as f:
        return f.read()

def read_nested(folder, name):
    with open(f"{folder}/../{name}", "r") as f:
        return f.read()

def safe_open(path):
    with open("safe.txt", "r") as f:
        return f.read()
"""
    findings = analyzer.analyze_source("io.py", code)
    path_findings = [f for f in findings if f.rule_id == "SEC-005"]
    assert len(path_findings) == 2

    for f in path_findings:
        assert f.severity == FindingSeverity.HIGH
        assert f.requires_human_review is True
        assert "traversal" in f.description.lower()


def test_insecure_deserialization_detection(analyzer: SecurityAnalyzer) -> None:
    code = """
import pickle
import yaml

def load_payload(raw):
    data1 = pickle.loads(raw)
    data2 = yaml.load(raw)
    return data1, data2

def safe_load(raw):
    import json
    return json.loads(raw), yaml.safe_load(raw)
"""
    findings = analyzer.analyze_source("loader.py", code)
    deser_findings = [f for f in findings if f.rule_id == "SEC-006"]
    assert len(deser_findings) == 2

    for f in deser_findings:
        assert f.severity == FindingSeverity.CRITICAL
        assert f.requires_human_review is True
        assert "deserialization" in f.description.lower()


def test_insecure_tempfile_detection(analyzer: SecurityAnalyzer) -> None:
    code = """
import tempfile

def create_temp():
    path = tempfile.mktemp()
    with open("/tmp/tempfile_fixed.txt", "w") as f:
        f.write("data")
    return path

def safe_temp():
    return tempfile.NamedTemporaryFile()
"""
    findings = analyzer.analyze_source("temp_ops.py", code)
    temp_findings = [f for f in findings if f.rule_id == "SEC-007"]
    assert len(temp_findings) == 2

    for f in temp_findings:
        assert f.severity == FindingSeverity.MEDIUM
        assert "temporary" in f.description.lower()


def test_analyze_patch_diff(analyzer: SecurityAnalyzer) -> None:
    patch = """diff --git a/app.py b/app.py
index 0000000..1111111 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,5 @@
 def start():
+    token = "Bearer mock_bearer_token_1234567890abcdef"
     pass
"""
    findings = analyzer.analyze_patch(patch)
    assert len(findings) >= 1
    assert findings[0].rule_id == "SEC-004"
    assert "REDACTED" in findings[0].evidence.code_excerpt


def test_config_filters_and_category_toggle(analyzer: SecurityAnalyzer) -> None:
    code = """
def run(cmd):
    eval(cmd)
"""
    # Test category disabled
    cfg_no_sec = ReviewConfiguration(
        enabled_categories=[FindingCategory.MAINTAINABILITY]
    )
    assert len(analyzer.analyze_source("mod.py", code, cfg_no_sec)) == 0

    # Test rule disabled
    cfg_rule_disabled = ReviewConfiguration(disabled_rule_ids=["SEC-003"])
    assert len(analyzer.analyze_source("mod.py", code, cfg_rule_disabled)) == 0

    # Test severity threshold
    cfg_high = ReviewConfiguration(min_severity=FindingSeverity.CRITICAL)
    findings = analyzer.analyze_source("mod.py", code, cfg_high)
    assert len(findings) == 1


def test_analyze_files_and_repository(
    analyzer: SecurityAnalyzer, tmp_path: Path
) -> None:
    files = {
        "src/a.py": "eval('2+2')",
        "src/b.py": "import os\nos.system('echo hi')",
        "src/c.py": "def clean(): pass",
    }
    findings = analyzer.analyze_files(files)
    assert len(findings) == 2

    # Write files to disk and scan repository
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "safe.py").write_text("x = 1\n", encoding="utf-8")
    (repo_dir / "vuln.py").write_text("eval('1+1')\n", encoding="utf-8")

    repo_findings = analyzer.analyze_repository(repo_dir)
    assert len(repo_findings) == 1
    assert repo_findings[0].file_path == "vuln.py"
