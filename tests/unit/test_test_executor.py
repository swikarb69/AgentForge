"""Unit tests for TestExecutor and output parser."""

from pathlib import Path

import pytest

from backend.app.execution.docker_runner import DockerRunner, MockDockerClient
from backend.app.execution.models import ExecutionStatus, VerificationProfile
from backend.app.execution.test_executor import TestExecutor


def test_parse_pytest_output_all_passed() -> None:
    """Verify parsing of clean passing pytest output."""
    raw = (
        "collected 6 items\n\n"
        "tests/test_a.py ...\n"
        "tests/test_b.py ...\n\n"
        "=== 6 passed in 0.42s ==="
    )
    summary = TestExecutor.parse_pytest_output(raw, exit_code=0)
    assert summary.total == 6
    assert summary.passed == 6
    assert summary.failed == 0
    assert summary.errors == 0
    assert summary.is_all_passed


def test_parse_pytest_output_with_failures_and_errors() -> None:
    """Verify parsing of pytest output with failures, errors, and skips."""
    raw = (
        "=== short test summary info ===\n"
        "FAILED tests/test_a.py::test_fail\n"
        "ERROR tests/test_b.py::test_err\n"
        "=== 2 failed, 4 passed, 1 skipped, 1 error in 1.12s ==="
    )
    summary = TestExecutor.parse_pytest_output(raw, exit_code=1)
    assert summary.total == 8
    assert summary.passed == 4
    assert summary.failed == 2
    assert summary.skipped == 1
    assert summary.errors == 1
    assert not summary.is_all_passed


def test_parse_pytest_output_fallback_clean() -> None:
    """Verify fallback parsing for clean runs without explicit numbers."""
    raw = "OK"
    summary = TestExecutor.parse_pytest_output(raw, exit_code=0)
    assert summary.total == 1
    assert summary.passed == 1
    assert summary.is_all_passed


def test_parse_pytest_output_fallback_error() -> None:
    """Verify fallback parsing for crashes or collection errors without summary."""
    raw = "Internal crash during collection"
    summary = TestExecutor.parse_pytest_output(raw, exit_code=1)
    assert summary.total == 1
    assert summary.errors == 1
    assert not summary.is_all_passed


def test_parse_ruff_output_clean() -> None:
    """Verify parsing of successful ruff check output."""
    raw = "All checks passed!\n"
    summary = TestExecutor.parse_ruff_output(raw, exit_code=0)
    assert summary.total == 1
    assert summary.passed == 1
    assert summary.failed == 0
    assert summary.is_all_passed


def test_parse_ruff_output_errors() -> None:
    """Verify parsing of ruff violations output."""
    raw = (
        "backend/app/main.py:10:1: F401 unused import\n"
        "backend/app/main.py:12:1: E501 line too long\n"
        "Found 5 errors.\n"
    )
    summary = TestExecutor.parse_ruff_output(raw, exit_code=1)
    assert summary.total == 5
    assert summary.failed == 5
    assert summary.passed == 0
    assert not summary.is_all_passed


def test_run_tests_integration_sync(tmp_path: Path) -> None:
    """Verify synchronous test execution attaches parsed TestSummary."""
    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=0,
        simulated_stdout="=== 4 passed in 0.10s ===\n",
    )
    runner = DockerRunner(docker_client=mock_client)
    executor = TestExecutor(docker_runner=runner)

    result = executor.run_tests(
        workspace_path=tmp_path,
        profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.test_summary is not None
    assert result.test_summary.total == 4
    assert result.test_summary.passed == 4
    assert result.test_summary.is_all_passed


@pytest.mark.anyio
async def test_run_tests_integration_async(tmp_path: Path) -> None:
    """Verify asynchronous test execution attaches parsed TestSummary."""
    mock_client = MockDockerClient(
        available=True,
        simulated_exit_code=1,
        simulated_stdout="=== 1 failed, 2 passed in 0.20s ===\n",
    )
    runner = DockerRunner(docker_client=mock_client)
    executor = TestExecutor(docker_runner=runner)

    result = await executor.run_tests_async(
        workspace_path=tmp_path,
        profile=VerificationProfile.PYTHON_UNIT_TESTS,
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.test_summary is not None
    assert result.test_summary.total == 3
    assert result.test_summary.passed == 2
    assert result.test_summary.failed == 1
    assert not result.test_summary.is_all_passed
