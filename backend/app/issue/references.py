"""Reference, endpoint, constraint, and keyword extraction from issue text."""

import re

from backend.app.issue.models import (
    ConstraintType,
    EndpointReference,
    IssueConstraint,
    IssueReference,
    ReferenceType,
)

# Known technical terms for keyword extraction
KNOWN_TECH_KEYWORDS = {
    "fastapi",
    "pydantic",
    "pytest",
    "python",
    "jwt",
    "auth",
    "authentication",
    "authorization",
    "token",
    "redis",
    "postgres",
    "postgresql",
    "sqlite",
    "docker",
    "api",
    "endpoint",
    "middleware",
    "ast",
    "parser",
    "scanner",
    "index",
    "chunk",
    "symbol",
    "json",
    "yaml",
    "toml",
}


class ReferenceExtractor:
    """Extracts file paths, symbols, HTTP endpoints, constraints, and keywords."""

    def extract_file_references(self, text: str) -> list[IssueReference]:
        """Extract referenced file paths from issue text."""
        references: list[IssueReference] = []
        seen: set[str] = set()

        file_pattern = re.compile(
            r"`?([a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+\.[a-zA-Z0-9]+|README\.md|pyproject\.toml|LICENSE)`?"
        )

        for match in file_pattern.finditer(text):
            val = match.group(1).strip("`").rstrip(".,;")
            if val and val.lower() not in seen:
                seen.add(val.lower())
                references.append(
                    IssueReference(
                        reference_type=ReferenceType.FILE,
                        value=val,
                        confidence=0.95,
                    )
                )

        return references

    def extract_symbol_references(self, text: str) -> list[IssueReference]:
        """Extract function, class, and method symbol references from issue text."""
        references: list[IssueReference] = []
        seen: set[str] = set()

        # 1. Backticked symbols: `func()` or `ClassName` or `Module.symbol`
        backtick_pattern = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_.]*(?:\(\))?)`")
        for match in backtick_pattern.finditer(text):
            val = match.group(1).strip()
            if "/" in val or val.startswith("http") or val.endswith(".py"):
                continue
            if val and val.lower() not in seen:
                seen.add(val.lower())
                references.append(
                    IssueReference(
                        reference_type=ReferenceType.SYMBOL,
                        value=val,
                        confidence=0.9,
                    )
                )

        # 2. Dotted symbols: e.g. RepositoryIndex.search or module.func
        dotted_pattern = re.compile(r"\b([A-Za-z_][a-zA-Z0-9_]*\.[a-zA-Z0-9_]+)\b")
        for match in dotted_pattern.finditer(text):
            val = match.group(1).strip()
            if not val.endswith(".py") and val.lower() not in seen:
                seen.add(val.lower())
                references.append(
                    IssueReference(
                        reference_type=ReferenceType.SYMBOL,
                        value=val,
                        confidence=0.88,
                    )
                )

        # 3. Function calls: func_name()
        func_pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*\(\))")
        for match in func_pattern.finditer(text):
            val = match.group(1).strip()
            if val.lower() not in seen:
                seen.add(val.lower())
                references.append(
                    IssueReference(
                        reference_type=ReferenceType.SYMBOL,
                        value=val,
                        confidence=0.85,
                    )
                )

        # 4. PascalCase class names: ClassName
        pascal_pattern = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")
        for match in pascal_pattern.finditer(text):
            val = match.group(1).strip()
            if val.lower() not in seen:
                seen.add(val.lower())
                references.append(
                    IssueReference(
                        reference_type=ReferenceType.SYMBOL,
                        value=val,
                        confidence=0.8,
                    )
                )

        return references

    def extract_endpoint_references(self, text: str) -> list[EndpointReference]:
        """Extract HTTP method and path endpoint references."""
        endpoints: list[EndpointReference] = []
        seen: set[str] = set()

        pattern = re.compile(
            r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/[a-zA-Z0-9_./{}-]+)"
        )

        for match in pattern.finditer(text):
            method = match.group(1).upper()
            path = match.group(2).rstrip(".,;")
            key = f"{method} {path}"
            if key not in seen:
                seen.add(key)
                endpoints.append(EndpointReference(method=method, path=path))

        return endpoints

    def extract_constraints(self, text: str) -> list[IssueConstraint]:
        """Extract explicit engineering or architectural constraints from text."""
        constraints: list[IssueConstraint] = []
        seen: set[str] = set()

        constraint_regex = re.compile(
            r"(?:must not|do not|don't|should not|cannot|without|keep|preserve|"
            r"compatibility)",
            re.IGNORECASE,
        )

        for line in text.splitlines():
            line_clean = line.strip(" -*+\t")
            if not line_clean or line_clean.startswith("#"):
                continue

            if constraint_regex.search(line_clean):
                if line_clean.lower() not in seen:
                    seen.add(line_clean.lower())
                    constraints.append(
                        IssueConstraint(
                            description=line_clean,
                            constraint_type=ConstraintType.EXPLICIT,
                        )
                    )

        return constraints

    def extract_keywords(self, text: str) -> list[str]:
        """Extract technical keywords, identifiers, and backticked terms."""
        keywords: set[str] = set()

        for match in re.finditer(r"`([^`]+)`", text):
            term = match.group(1).strip()
            if term and len(term) < 50:
                keywords.add(term)

        text_lower = text.lower()
        for kw in KNOWN_TECH_KEYWORDS:
            if re.search(rf"\b{re.escape(kw)}\b", text_lower):
                keywords.add(kw)

        for match in re.finditer(r"\b([A-Z][a-zA-Z0-9_]+)\b", text):
            val = match.group(1)
            if len(val) > 2:
                keywords.add(val)

        return sorted(keywords)
