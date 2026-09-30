"""Unit tests for language detection and test file classification."""

from backend.app.repository.metadata import detect_language, is_test_file


def test_detect_language_supported() -> None:
    """Verify supported extensions map to expected languages."""
    assert detect_language(".py") == "python"
    assert detect_language(".js") == "javascript"
    assert detect_language(".jsx") == "javascript"
    assert detect_language(".ts") == "typescript"
    assert detect_language(".tsx") == "typescript"
    assert detect_language(".java") == "java"
    assert detect_language(".c") == "c"
    assert detect_language(".cpp") == "cpp"
    assert detect_language(".go") == "go"
    assert detect_language(".rs") == "rust"
    assert detect_language(".md") == "markdown"
    assert detect_language(".json") == "json"
    assert detect_language(".yaml") == "yaml"
    assert detect_language(".toml") == "toml"


def test_detect_language_unknown() -> None:
    """Verify unknown or unmapped extensions return 'unknown'."""
    assert detect_language(".xyz") == "unknown"
    assert detect_language(".unknown") == "unknown"
    assert detect_language("") == "unknown"


def test_is_test_file_detection() -> None:
    """Verify test file detection logic."""
    assert is_test_file("tests/test_main.py") is True
    assert is_test_file("backend/tests/unit/test_auth.py") is True
    assert is_test_file("src/components/button.test.tsx") is True
    assert is_test_file("src/utils/math.spec.js") is True
    assert is_test_file("app/main_test.py") is True

    # Non-test files
    assert is_test_file("backend/app/main.py") is False
    assert is_test_file("src/components/button.tsx") is False
    assert is_test_file("README.md") is False
