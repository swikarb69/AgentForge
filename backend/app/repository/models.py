"""Data models for repository intelligence, AST symbols, and chunks."""

from enum import StrEnum

from pydantic import BaseModel, Field


class SymbolType(StrEnum):
    """Types of Python AST code symbols."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    IMPORT = "import"


class Symbol(BaseModel):
    """Representation of an extracted code symbol."""

    name: str = Field(description="Name of the symbol")
    symbol_type: SymbolType = Field(description="Type category of the symbol")
    file_path: str = Field(description="Relative path of source file")
    start_line: int = Field(description="1-indexed starting line number")
    end_line: int = Field(description="1-indexed ending line number")
    parent_symbol: str | None = Field(
        default=None, description="Enclosing parent symbol"
    )
    signature: str | None = Field(
        default=None, description="Parameter signature string"
    )
    docstring: str | None = Field(
        default=None, description="Extracted docstring if present"
    )


class RepositoryFile(BaseModel):
    """Metadata representation of a repository file."""

    relative_path: str = Field(description="Path relative to repository root")
    file_extension: str = Field(description="File extension including leading dot")
    language: str = Field(description="Detected programming or markup language")
    size_bytes: int = Field(description="File size in bytes")
    line_count: int = Field(description="Total line count of the file")
    is_test: bool = Field(default=False, description="True if test file")
    is_ignored: bool = Field(default=False, description="True if ignored")
    parse_status: str = Field(
        default="success", description="Status: success/skipped/failed"
    )
    error_message: str | None = Field(
        default=None, description="Error details if failed"
    )


class CodeChunk(BaseModel):
    """Semantic or structural chunk of repository source code."""

    chunk_id: str = Field(description="Unique deterministic chunk identifier")
    file_path: str = Field(description="Relative file path of source file")
    language: str = Field(description="Detected programming language")
    symbol_name: str | None = Field(default=None, description="Associated symbol name")
    symbol_type: SymbolType | None = Field(
        default=None, description="Associated symbol type"
    )
    start_line: int = Field(description="Starting line number")
    end_line: int = Field(description="Ending line number")
    content: str = Field(description="Raw source code content")


class RepositoryInfo(BaseModel):
    """Complete structured metadata summary of a repository."""

    repository_name: str = Field(description="Name of the repository directory")
    root_path: str = Field(description="Canonical path of repository root")
    total_files: int = Field(description="Total files discovered")
    total_source_files: int = Field(description="Total non-ignored source files")
    total_test_files: int = Field(description="Total test files discovered")
    language_counts: dict[str, int] = Field(description="Count per language")
    files: list[RepositoryFile] = Field(default_factory=list, description="File list")
    symbols: list[Symbol] = Field(default_factory=list, description="Symbol list")
    chunks: list[CodeChunk] = Field(default_factory=list, description="Chunk list")
    parse_errors: list[str] = Field(default_factory=list, description="Parse errors")


class SearchResult(BaseModel):
    """Scored search match result."""

    chunk: CodeChunk = Field(description="Matched code chunk")
    score: float = Field(description="Relevance score (0.0 to 1.0)")
    matched_fields: list[str] = Field(description="Matched query fields")


class AnalyzeRequest(BaseModel):
    """API Request model for repository analysis."""

    path: str = Field(description="Path to repository root")


class AnalyzeResponse(BaseModel):
    """API Response model for repository analysis summary."""

    repository_name: str
    root_path: str
    total_files: int
    total_source_files: int
    total_test_files: int
    language_counts: dict[str, int]
    files: list[RepositoryFile]
    symbols_count: int
    chunks_count: int
    parse_errors: list[str]


class SearchRequest(BaseModel):
    """API Request model for repository deterministic search."""

    repository_path: str = Field(description="Path to repository root")
    query: str = Field(description="Search query string")
    limit: int = Field(default=10, ge=1, le=100, description="Max results limit")


class SearchResponse(BaseModel):
    """API Response model for repository deterministic search."""

    query: str
    total_matches: int
    results: list[SearchResult]
