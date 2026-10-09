"""Data models for issue intelligence, requirement extraction, and matching."""

from enum import StrEnum

from pydantic import BaseModel, Field

from backend.app.repository.models import CodeChunk, Symbol


class IssueSource(StrEnum):
    """Source origin of a software issue."""

    LOCAL = "local"
    GITHUB = "github"
    MANUAL = "manual"


class Issue(BaseModel):
    """Structured representation of an incoming software issue."""

    id: str = Field(default="ISSUE-LOCAL", description="Unique issue identifier")
    title: str = Field(description="Issue title or headline")
    description: str = Field(description="Raw body text of issue description")
    source: IssueSource = Field(default=IssueSource.LOCAL, description="Issue source")


class RequirementType(StrEnum):
    """Classification of extracted engineering requirements."""

    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    UNKNOWN = "unknown"


class IssueRequirement(BaseModel):
    """Extracted explicit engineering requirement."""

    id: str = Field(description="Requirement identifier")
    description: str = Field(description="Statement of requirement")
    requirement_type: RequirementType = Field(
        default=RequirementType.FUNCTIONAL, description="Type classification"
    )


class AcceptanceCriterion(BaseModel):
    """Extracted explicit acceptance criterion."""

    id: str = Field(description="Criterion identifier")
    description: str = Field(description="Statement of acceptance condition")
    is_explicit: bool = Field(default=True, description="True if explicitly stated")


class ConstraintType(StrEnum):
    """Classification of extracted development constraints."""

    EXPLICIT = "explicit"
    IMPLICIT = "implicit"


class IssueConstraint(BaseModel):
    """Extracted development or architectural constraint."""

    description: str = Field(description="Statement of constraint")
    constraint_type: ConstraintType = Field(
        default=ConstraintType.EXPLICIT, description="Constraint type"
    )


class ReferenceType(StrEnum):
    """Category of code or architectural entity referenced in issue."""

    FILE = "file"
    SYMBOL = "symbol"
    ENDPOINT = "endpoint"
    UNKNOWN = "unknown"


class IssueReference(BaseModel):
    """Entity reference extracted from issue text."""

    reference_type: ReferenceType = Field(description="Category of reference")
    value: str = Field(description="Referenced path or symbol identifier")
    confidence: float = Field(default=1.0, description="Extraction confidence score")


class EndpointReference(BaseModel):
    """HTTP API endpoint referenced in issue text."""

    method: str = Field(description="HTTP method (GET, POST, PUT, DELETE, etc.)")
    path: str = Field(description="API route path string")


class MatchReason(BaseModel):
    """Explanation breakdown for repository relevance scoring."""

    rule_name: str = Field(description="Name of matching heuristic rule")
    score: float = Field(description="Points contributed by rule")
    description: str = Field(description="Detailed explanation of match")


class IssueMatch(BaseModel):
    """Matched repository file or symbol for an issue."""

    file_path: str = Field(description="Relative file path in repository")
    symbol_name: str | None = Field(default=None, description="Matched symbol if any")
    score: float = Field(description="Cumulative match score")
    reasons: list[MatchReason] = Field(description="Detailed scoring reasons")
    chunk: CodeChunk | None = Field(default=None, description="Associated code chunk")


class IssueAnalysis(BaseModel):
    """Structured understanding extracted from issue text."""

    issue: Issue = Field(description="Original issue payload")
    requirements: list[IssueRequirement] = Field(description="Extracted requirements")
    acceptance_criteria: list[AcceptanceCriterion] = Field(
        description="Extracted acceptance criteria"
    )
    constraints: list[IssueConstraint] = Field(description="Extracted constraints")
    keywords: list[str] = Field(description="Extracted technical keywords")
    references: list[IssueReference] = Field(description="Extracted entity references")
    endpoints: list[EndpointReference] = Field(description="Extracted API endpoints")
    is_ambiguous: bool = Field(default=False, description="True if issue is vague")
    ambiguity_reasons: list[str] = Field(
        default_factory=list, description="Reasons for ambiguity"
    )


class IssueContext(BaseModel):
    """Complete context package linking IssueAnalysis to repository intelligence."""

    analysis: IssueAnalysis = Field(description="Structured issue analysis")
    relevant_files: list[str] = Field(description="Ranked list of relevant file paths")
    relevant_symbols: list[Symbol] = Field(
        description="Ranked list of relevant symbols"
    )
    relevant_chunks: list[CodeChunk] = Field(
        description="Ranked list of relevant code chunks"
    )
    matches: list[IssueMatch] = Field(description="Detailed match scoring list")
    unresolved_references: list[str] = Field(
        default_factory=list, description="Issue references not found in repo"
    )


class AnalyzeIssueRequest(BaseModel):
    """API Request for analyzing software issue text."""

    title: str = Field(description="Issue title")
    description: str = Field(default="", description="Issue description body")
    id: str | None = Field(default="ISSUE-LOCAL", description="Optional issue ID")


class IssueContextRequest(BaseModel):
    """API Request for mapping an issue to repository context."""

    repository_path: str = Field(description="Path to repository root")
    title: str = Field(description="Issue title")
    description: str = Field(default="", description="Issue description body")
    id: str | None = Field(default="ISSUE-LOCAL", description="Optional issue ID")
