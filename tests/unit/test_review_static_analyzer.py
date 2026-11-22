"""Unit tests for DeterministicStaticAnalyzer and static code quality rules."""

import pytest

from backend.app.review.models import (
    FindingCategory,
    FindingSeverity,
    ReviewConfiguration,
)
from backend.app.review.static_analyzer import (
    DeterministicStaticAnalyzer,
    compute_content_hash,
    extract_code_excerpt,
)


@pytest.fixture
def analyzer() -> DeterministicStaticAnalyzer:
    return DeterministicStaticAnalyzer()


def test_extract_code_excerpt_and_hash() -> None:
    lines = ["line 1", "line 2", "line 3", "line 4", "line 5"]
    excerpt = extract_code_excerpt(lines, 2, 4)
    assert excerpt == "line 2\nline 3\nline 4"

    digest = compute_content_hash(excerpt)
    assert len(digest) == 64
    assert digest == compute_content_hash(excerpt)


def test_syntax_error_reported_cleanly(analyzer: DeterministicStaticAnalyzer) -> None:
    code = "def broken(\n  return 1"
    findings = analyzer.analyze_source("broken.py", code)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "CORR-001"
    assert f.category == FindingCategory.CORRECTNESS
    assert "Syntax" in f.title or "Syntax" in f.description


def test_unreachable_code_detection(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def compute(x):
    if x > 0:
        return x
        y = 2  # unreachable
        return y
    return 0
"""
    findings = analyzer.analyze_source("compute.py", code)
    unreachable = [f for f in findings if f.rule_id == "CORR-002"]
    assert len(unreachable) == 1
    assert unreachable[0].start_line == 4
    assert "unreachable" in unreachable[0].description.lower()


def test_mutable_default_argument(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def add_item(val, target=[]):
    target.append(val)
    return target
"""
    findings = analyzer.analyze_source("utils.py", code)
    mutable_finds = [f for f in findings if f.rule_id == "CORR-003"]
    assert len(mutable_finds) == 1
    assert mutable_finds[0].affected_symbol == "add_item"
    assert mutable_finds[0].severity == FindingSeverity.MEDIUM


def test_suspicious_identity_comparison(
    analyzer: DeterministicStaticAnalyzer,
) -> None:
    code = """def check_status(status):
    if status is "ACTIVE":
        return True
    if status is not 100:
        return False
    return None
"""
    findings = analyzer.analyze_source("service.py", code)
    identity_finds = [f for f in findings if f.rule_id == "CORR-004"]
    assert len(identity_finds) == 2


def test_bare_and_empty_exception_handlers(
    analyzer: DeterministicStaticAnalyzer,
) -> None:
    code = """def process():
    try:
        do_work()
    except:
        pass
    try:
        do_more()
    except ValueError:
        pass
"""
    findings = analyzer.analyze_source("work.py", code)
    bare = [f for f in findings if f.rule_id == "REL-001"]
    empty = [f for f in findings if f.rule_id == "REL-002"]
    assert len(bare) == 1
    assert len(empty) == 2


def test_unhandled_resource_cleanup(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def load_file():
    f = open("data.txt", "r")
    content = f.read()
    return content

def load_with_context():
    with open("data2.txt", "r") as f:
        return f.read()
"""
    findings = analyzer.analyze_source("loader.py", code)
    resource_finds = [f for f in findings if f.rule_id == "REL-003"]
    assert len(resource_finds) == 1
    assert resource_finds[0].start_line == 2


def test_global_state_modification(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """COUNTER = 0

def increment():
    global COUNTER
    COUNTER += 1
"""
    findings = analyzer.analyze_source("state.py", code)
    global_finds = [f for f in findings if f.rule_id == "REL-004"]
    assert len(global_finds) == 1
    assert global_finds[0].start_line == 4


def test_excessive_parameters(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def many_args(a, b, c, d, e, f, g):
    '''Docstring'''
    return a + b
"""
    findings = analyzer.analyze_source("func.py", code)
    param_finds = [f for f in findings if f.rule_id == "MAINT-002"]
    assert len(param_finds) == 1
    assert param_finds[0].affected_symbol == "many_args"


def test_deeply_nested_control_flow(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def deeply_nested(a, b):
    '''Docstring'''
    if a:
        if b:
            for i in range(10):
                while True:
                    if i > 5:
                        return i
"""
    findings = analyzer.analyze_source("nested.py", code)
    nested_finds = [f for f in findings if f.rule_id == "MAINT-003"]
    assert len(nested_finds) >= 1
    assert nested_finds[0].category == FindingCategory.MAINTAINABILITY


def test_high_cyclomatic_complexity(analyzer: DeterministicStaticAnalyzer) -> None:
    # 11 decisions: 1 base + 11 if statements = complexity 12 (> 10)
    branches = "\n    ".join(f"if x == {i}: return {i}" for i in range(11))
    code = f"""def complex_decisions(x):
    '''Docstring'''
    {branches}
    return 0
"""
    findings = analyzer.analyze_source("complex.py", code)
    complexity_finds = [f for f in findings if f.rule_id == "MAINT-004"]
    assert len(complexity_finds) == 1
    assert "complexity of 12" in complexity_finds[0].description


def test_missing_docstring_detection(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def public_func():
    return 1

class PublicClass:
    def public_method(self):
        return 2

def _private_func():
    return 3
"""
    findings = analyzer.analyze_source("api.py", code)
    doc_finds = [f for f in findings if f.rule_id == "MAINT-005"]
    assert len(doc_finds) == 3
    symbols = {f.affected_symbol for f in doc_finds}
    assert "public_func" in symbols
    assert "PublicClass" in symbols
    assert "public_method" in symbols
    assert "_private_func" not in symbols


def test_repeated_regex_compilation_in_loop(
    analyzer: DeterministicStaticAnalyzer,
) -> None:
    code = """import re

def parse_items(items):
    for item in items:
        pattern = re.compile(r"\\d+")
        match = pattern.search(item)
"""
    findings = analyzer.analyze_source("parser.py", code)
    perf_finds = [f for f in findings if f.rule_id == "PERF-002"]
    assert len(perf_finds) == 1
    assert perf_finds[0].start_line == 5


def test_quadratic_loop_pattern(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def find_pairs(items):
    pairs = []
    for a in items:
        for b in items:
            if a != b:
                pairs.append((a, b))
    return pairs
"""
    findings = analyzer.analyze_source("algo.py", code)
    quad_finds = [f for f in findings if f.rule_id == "PERF-001"]
    assert len(quad_finds) == 1
    assert "items" in quad_finds[0].description


def test_disabled_rules_and_category_filters(
    analyzer: DeterministicStaticAnalyzer,
) -> None:
    code = """def bad_fn(target=[]):
    try:
        open("test.txt")
    except:
        pass
"""
    # 1. Disable CORRECTNESS category
    config1 = ReviewConfiguration(enabled_categories=[FindingCategory.RELIABILITY])
    findings1 = analyzer.analyze_source("test.py", code, config1)
    categories1 = {f.category for f in findings1}
    assert FindingCategory.CORRECTNESS not in categories1
    assert FindingCategory.RELIABILITY in categories1

    # 2. Disable specific rule REL-001 (bare except)
    config2 = ReviewConfiguration(disabled_rule_ids=["REL-001"])
    findings2 = analyzer.analyze_source("test.py", code, config2)
    assert not any(f.rule_id == "REL-001" for f in findings2)


def test_min_severity_filter(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def public_fn(a=[]):
    return 1
"""
    # Public function missing docstring is INFORMATIONAL, mutable default is MEDIUM
    config_med = ReviewConfiguration(min_severity=FindingSeverity.MEDIUM)
    findings = analyzer.analyze_source("test.py", code, config_med)
    assert all(
        f.severity
        in (FindingSeverity.MEDIUM, FindingSeverity.HIGH, FindingSeverity.CRITICAL)
        for f in findings
    )
    assert any(f.rule_id == "CORR-003" for f in findings)
    assert not any(f.rule_id == "MAINT-005" for f in findings)


def test_max_findings_per_file_cap(analyzer: DeterministicStaticAnalyzer) -> None:
    code = """def f1(): pass
def f2(): pass
def f3(): pass
def f4(): pass
def f5(): pass
"""
    config = ReviewConfiguration(max_findings_per_file=2)
    findings = analyzer.analyze_source("test.py", code, config)
    assert len(findings) == 2


def test_analyze_files_multi_file(analyzer: DeterministicStaticAnalyzer) -> None:
    files = {
        "app/mod1.py": "def f1(a=[]): return a",
        "app/mod2.py": "def f2(b=[]): return b",
        "readme.txt": "Just text",
    }
    findings = analyzer.analyze_files(files)
    assert len(findings) >= 2
    paths = {f.file_path for f in findings}
    assert "app/mod1.py" in paths
    assert "app/mod2.py" in paths
    assert "readme.txt" not in paths
