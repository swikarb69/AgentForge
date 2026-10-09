"""Unit tests for IssueParser."""

from backend.app.issue.parser import IssueParser


def test_normalize_text() -> None:
    """Verify line break normalization and whitespace collapsing."""
    raw = "Header  \r\n\r\n\r\nLine 1\n\n\n\nLine 2  "
    normalized = IssueParser.normalize_text(raw)
    assert "Header" in normalized
    assert "Line 1" in normalized
    assert "Line 2" in normalized
    assert "\r" not in normalized


def test_extract_sections() -> None:
    """Verify Markdown section extraction by heading."""
    text = """
# Issue Title

Some overview text.

## Requirements
- Item 1
- Item 2

## Acceptance Criteria
- Criteria 1
"""
    sections = IssueParser.extract_sections(text)
    assert "Issue Title" in sections or "Overview" in sections
    assert "Requirements" in sections
    assert "Acceptance Criteria" in sections
    assert "- Item 1" in sections["Requirements"]
    assert "- Criteria 1" in sections["Acceptance Criteria"]


def test_extract_bullet_items() -> None:
    """Verify bullet list item extraction."""
    text = """
Requirements:
- First item
* Second item
+ Third item
1. Fourth item
"""
    bullets = IssueParser.extract_bullet_items(text)
    assert len(bullets) == 4
    assert bullets[0] == "First item"
    assert bullets[1] == "Second item"
    assert bullets[2] == "Third item"
    assert bullets[3] == "Fourth item"


def test_extract_code_blocks() -> None:
    """Verify triple-backtick code block extraction."""
    text = """
Here is code:
```python
def foo():
    pass
```
And another:
```json
{"key": "val"}
```
"""
    blocks = IssueParser.extract_code_blocks(text)
    assert len(blocks) == 2
    assert "def foo()" in blocks[0]
    assert '{"key": "val"}' in blocks[1]
