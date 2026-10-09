"""Repository-aware issue matcher linking IssueAnalysis to repository index."""

import re
from pathlib import Path

from backend.app.indexing.index import RepositoryIndex
from backend.app.issue.models import (
    IssueAnalysis,
    IssueMatch,
    MatchReason,
    ReferenceType,
)
from backend.app.repository.models import CodeChunk, RepositoryInfo, Symbol


class IssueMatcher:
    """Matches structured IssueAnalysis to repository files, symbols, and chunks."""

    def match(
        self,
        analysis: IssueAnalysis,
        index: RepositoryIndex,
        repo_info: RepositoryInfo | None = None,
    ) -> tuple[list[IssueMatch], list[str]]:
        """Perform repository-aware matching for an analyzed issue.

        Returns:
            Tuple of (ranked IssueMatch list, list of unresolved reference values).
        """
        unresolved: list[str] = []

        # Prepare maps from repo_info if available
        repo_files: dict[str, str] = {}  # rel_path -> rel_path
        repo_symbols: dict[str, list[Symbol]] = {}  # symbol_name -> Symbol list
        symbol_docstrings: dict[tuple[str, str], str] = {}

        if repo_info:
            for f in repo_info.files:
                repo_files[f.relative_path] = f.relative_path
                # Also index basename for flexible matching
                repo_files[Path(f.relative_path).name] = f.relative_path

            for s in repo_info.symbols:
                repo_symbols.setdefault(s.name, []).append(s)
                if s.docstring:
                    symbol_docstrings[(s.file_path, s.name)] = s.docstring

        # Accumulator for chunk match scores and reasons
        candidate_chunks: dict[str, CodeChunk] = {}
        chunk_scores: dict[str, float] = {}
        chunk_reasons: dict[str, list[MatchReason]] = {}

        # Fill candidate chunks from index
        all_chunks: list[CodeChunk] = list(index._chunks.values())
        for chunk in all_chunks:
            candidate_chunks[chunk.chunk_id] = chunk
            chunk_scores[chunk.chunk_id] = 0.0
            chunk_reasons[chunk.chunk_id] = []

        # 1. Match File References (+10 points)
        for ref in analysis.references:
            if ref.reference_type == ReferenceType.FILE:
                clean_ref = ref.value.strip("`'\"")
                base_name = Path(clean_ref).name
                target_file = repo_files.get(clean_ref) or repo_files.get(base_name)

                matched = False
                for chunk in all_chunks:
                    if chunk.file_path == target_file or chunk.file_path == clean_ref:
                        matched = True
                        chunk_scores[chunk.chunk_id] += 10.0
                        chunk_reasons[chunk.chunk_id].append(
                            MatchReason(
                                rule_name="exact_file_reference",
                                score=10.0,
                                description=f"File matched: {clean_ref}",
                            )
                        )
                if not matched and clean_ref not in unresolved:
                    unresolved.append(clean_ref)

        # 2. Match Symbol References (+10 points)
        for ref in analysis.references:
            if ref.reference_type == ReferenceType.SYMBOL:
                clean_sym = ref.value.strip("`'\"")
                # Remove call parens e.g. verify_token() -> verify_token
                sym_name = re.sub(r"\(\)$", "", clean_sym)
                simple_name = sym_name.split(".")[-1]

                matched = False
                for chunk in all_chunks:
                    if chunk.symbol_name in (sym_name, simple_name, clean_sym):
                        matched = True
                        chunk_scores[chunk.chunk_id] += 10.0
                        chunk_reasons[chunk.chunk_id].append(
                            MatchReason(
                                rule_name="exact_symbol_reference",
                                score=10.0,
                                description=f"Symbol matched: {clean_sym}",
                            )
                        )
                if not matched and sym_name in repo_symbols:
                    matched = True  # Symbol exists in repo_symbols

                if not matched and clean_sym not in unresolved:
                    unresolved.append(clean_sym)

        # 3. Match Endpoint References (+8 points)
        for ep in analysis.endpoints:
            ep_str = f"{ep.method} {ep.path}"
            matched = False
            for chunk in all_chunks:
                if ep.path.lower() in chunk.content.lower():
                    matched = True
                    chunk_scores[chunk.chunk_id] += 8.0
                    chunk_reasons[chunk.chunk_id].append(
                        MatchReason(
                            rule_name="endpoint_reference",
                            score=8.0,
                            description=f"Endpoint reference matched: {ep_str}",
                        )
                    )
            if not matched and ep_str not in unresolved:
                unresolved.append(ep_str)

        # 4. Match Keywords
        for kw in analysis.keywords:
            kw_lower = kw.lower()
            if len(kw_lower) < 2:
                continue

            for chunk in all_chunks:
                # Symbol keyword (+5)
                if chunk.symbol_name and kw_lower in chunk.symbol_name.lower():
                    chunk_scores[chunk.chunk_id] += 5.0
                    chunk_reasons[chunk.chunk_id].append(
                        MatchReason(
                            rule_name="symbol_keyword_match",
                            score=5.0,
                            description=(
                                f"Keyword '{kw}' matched symbol '{chunk.symbol_name}'"
                            ),
                        )
                    )

                # File keyword (+4)
                if kw_lower in chunk.file_path.lower():
                    chunk_scores[chunk.chunk_id] += 4.0
                    chunk_reasons[chunk.chunk_id].append(
                        MatchReason(
                            rule_name="file_keyword_match",
                            score=4.0,
                            description=(
                                f"Keyword '{kw}' matched file path '{chunk.file_path}'"
                            ),
                        )
                    )

                # Docstring keyword (+3)
                if chunk.symbol_name:
                    doc = symbol_docstrings.get(
                        (chunk.file_path, chunk.symbol_name), ""
                    )
                    if doc and kw_lower in doc.lower():
                        chunk_scores[chunk.chunk_id] += 3.0
                        chunk_reasons[chunk.chunk_id].append(
                            MatchReason(
                                rule_name="docstring_keyword_match",
                                score=3.0,
                                description=(
                                    f"Keyword '{kw}' matched docstring of "
                                    f"'{chunk.symbol_name}'"
                                ),
                            )
                        )

                # Content keyword (+1)
                if kw_lower in chunk.content.lower():
                    chunk_scores[chunk.chunk_id] += 1.0
                    chunk_reasons[chunk.chunk_id].append(
                        MatchReason(
                            rule_name="content_keyword_match",
                            score=1.0,
                            description=f"Keyword '{kw}' matched chunk content",
                        )
                    )

        # 5. Test File Boost (+2 points)
        issue_text = f"{analysis.issue.title} {analysis.issue.description}".lower()
        wants_tests = any(
            term in issue_text
            for term in ("test", "tests", "unit test", "integration test", "spec")
        )

        if wants_tests:
            for chunk in all_chunks:
                file_lower = chunk.file_path.lower()
                is_test_file = (
                    file_lower.startswith("tests/")
                    or "test_" in file_lower
                    or "_test" in file_lower
                )
                if is_test_file:
                    chunk_scores[chunk.chunk_id] += 2.0
                    chunk_reasons[chunk.chunk_id].append(
                        MatchReason(
                            rule_name="test_file_boost",
                            score=2.0,
                            description="Test file boost applied",
                        )
                    )

        # Collect matches with score > 0
        matches: list[IssueMatch] = []
        for chunk_id, chunk in candidate_chunks.items():
            score = chunk_scores[chunk_id]
            reasons = chunk_reasons[chunk_id]
            if score > 0.0:
                matches.append(
                    IssueMatch(
                        file_path=chunk.file_path,
                        symbol_name=chunk.symbol_name,
                        score=round(score, 2),
                        reasons=reasons,
                        chunk=chunk,
                    )
                )

        # Sort matches descending by score
        matches.sort(key=lambda m: m.score, reverse=True)

        return matches, unresolved
