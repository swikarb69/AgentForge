"""Issue context builder service pipeline."""

from pathlib import Path

from backend.app.core.config import settings
from backend.app.issue.extractor import RequirementExtractor
from backend.app.issue.matcher import IssueMatcher
from backend.app.issue.models import (
    Issue,
    IssueAnalysis,
    IssueContext,
    IssueSource,
)
from backend.app.issue.parser import IssueParser
from backend.app.issue.references import ReferenceExtractor
from backend.app.repository.analyzer import RepositoryAnalyzer
from backend.app.repository.models import CodeChunk, Symbol


class IssueContextBuilder:
    """High-level service building structured issue analysis and repository context."""

    def __init__(self) -> None:
        self.parser = IssueParser()
        self.requirement_extractor = RequirementExtractor()
        self.reference_extractor = ReferenceExtractor()
        self.matcher = IssueMatcher()
        self.repo_analyzer = RepositoryAnalyzer()

    def analyze_issue(
        self,
        title: str,
        description: str = "",
        issue_id: str | None = None,
        source: IssueSource = IssueSource.LOCAL,
    ) -> IssueAnalysis:
        """Analyze issue text into structured requirements and references."""
        clean_title = title.strip()
        clean_desc = description.strip()

        if not clean_title:
            raise ValueError("Issue title cannot be empty")

        if len(clean_title) > settings.MAX_ISSUE_TITLE_LENGTH:
            raise ValueError(
                f"Issue title exceeds maximum length of "
                f"{settings.MAX_ISSUE_TITLE_LENGTH} characters"
            )

        if len(clean_desc) > settings.MAX_ISSUE_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Issue description exceeds maximum length of "
                f"{settings.MAX_ISSUE_DESCRIPTION_LENGTH} characters"
            )

        issue = Issue(
            id=issue_id or "ISSUE-LOCAL",
            title=clean_title,
            description=clean_desc,
            source=source,
        )

        # Extract requirements & acceptance criteria
        reqs = self.requirement_extractor.extract_requirements(clean_title, clean_desc)
        criteria = self.requirement_extractor.extract_acceptance_criteria(clean_desc)

        # Extract references, endpoints, constraints & keywords
        file_refs = self.reference_extractor.extract_file_references(
            f"{clean_title}\n{clean_desc}"
        )
        sym_refs = self.reference_extractor.extract_symbol_references(
            f"{clean_title}\n{clean_desc}"
        )
        endpoints = self.reference_extractor.extract_endpoint_references(
            f"{clean_title}\n{clean_desc}"
        )
        constraints = self.reference_extractor.extract_constraints(clean_desc)
        keywords = self.reference_extractor.extract_keywords(
            f"{clean_title}\n{clean_desc}"
        )

        all_references = file_refs + sym_refs

        # Detect ambiguity
        ambiguity_reasons: list[str] = []
        is_ambiguous = False

        # Check if the only requirement is the title fallback
        has_explicit_body_reqs = any(
            r.description.lower() != clean_title.lower() for r in reqs
        )

        if len(clean_desc) < 20 and not all_references and not has_explicit_body_reqs:
            is_ambiguous = True
            ambiguity_reasons.append(
                "Issue description is too short and lacks explicit technical details"
            )

        if not reqs and not criteria and not all_references:
            is_ambiguous = True
            ambiguity_reasons.append(
                "No explicit requirements, criteria, or entity references found"
            )

        return IssueAnalysis(
            issue=issue,
            requirements=reqs,
            acceptance_criteria=criteria,
            constraints=constraints,
            keywords=keywords,
            references=all_references,
            endpoints=endpoints,
            is_ambiguous=is_ambiguous,
            ambiguity_reasons=ambiguity_reasons,
        )

    def build_context(
        self,
        repository_path: str | Path,
        title: str,
        description: str = "",
        issue_id: str | None = None,
    ) -> IssueContext:
        """Analyze repository and map software issue to relevant code context."""
        repo_path = Path(repository_path).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repository directory does not exist: {repository_path}")

        # Analyze repository intelligence
        repo_info = self.repo_analyzer.analyze(repo_path)
        index = self.repo_analyzer.index

        # Analyze issue
        analysis = self.analyze_issue(
            title=title, description=description, issue_id=issue_id
        )

        # Perform repository-aware matching
        matches, unresolved = self.matcher.match(analysis, index, repo_info)

        # Extract relevant files, symbols, chunks
        seen_files: set[str] = set()
        relevant_files: list[str] = []
        for m in matches:
            if m.file_path not in seen_files:
                seen_files.add(m.file_path)
                relevant_files.append(m.file_path)

        # Match relevant symbols
        relevant_symbols: list[Symbol] = []
        matched_sym_names = {
            m.symbol_name for m in matches if m.symbol_name is not None
        }
        for s in repo_info.symbols:
            if s.name in matched_sym_names:
                relevant_symbols.append(s)

        # Match relevant chunks
        relevant_chunks: list[CodeChunk] = [
            m.chunk for m in matches if m.chunk is not None
        ]

        return IssueContext(
            analysis=analysis,
            relevant_files=relevant_files,
            relevant_symbols=relevant_symbols,
            relevant_chunks=relevant_chunks,
            matches=matches,
            unresolved_references=unresolved,
        )
