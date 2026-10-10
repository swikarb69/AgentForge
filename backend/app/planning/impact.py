"""Change impact analysis engine evaluating direct and indirect affected targets."""

from pathlib import Path

from pydantic import BaseModel, Field

from backend.app.issue.models import IssueContext, ReferenceType
from backend.app.planning.models import ChangeType, FileChange, SymbolChange
from backend.app.repository.models import RepositoryInfo, SymbolType


class ImpactResult(BaseModel):
    """Structured result of change impact analysis."""

    files_to_modify: list[FileChange] = Field(default_factory=list)
    files_to_create: list[FileChange] = Field(default_factory=list)
    files_to_delete: list[FileChange] = Field(default_factory=list)
    symbol_changes: list[SymbolChange] = Field(default_factory=list)
    existing_tests: list[str] = Field(default_factory=list)
    tests_to_modify: list[str] = Field(default_factory=list)
    tests_to_add: list[str] = Field(default_factory=list)
    direct_files: list[str] = Field(default_factory=list)
    indirect_files: list[str] = Field(default_factory=list)


class ImpactAnalyzer:
    """Analyzes IssueContext to determine direct and indirect codebase impact."""

    def analyze(
        self,
        context: IssueContext,
        repo_info: RepositoryInfo | None = None,
    ) -> ImpactResult:
        """Determine affected files, symbols, and test assets from IssueContext.

        Args:
            context: Structured context from Sprint 2 Issue Intelligence.
            repo_info: Optional complete repository metadata from Sprint 1.

        Returns:
            Structured ImpactResult detailing direct and indirect impact.
        """
        direct_files: list[str] = []
        indirect_files: list[str] = []
        files_to_modify: list[FileChange] = []
        files_to_create: list[FileChange] = []
        files_to_delete: list[FileChange] = []
        symbol_changes: list[SymbolChange] = []

        existing_tests: list[str] = []
        tests_to_modify: list[str] = []
        tests_to_add: list[str] = []

        # 1. Distinguish test files vs source files in relevant_files
        repo_test_files: set[str] = set()
        repo_source_files: set[str] = set()

        if repo_info:
            for f in repo_info.files:
                if f.is_test or self._is_test_path(f.relative_path):
                    repo_test_files.add(f.relative_path)
                else:
                    repo_source_files.add(f.relative_path)
        else:
            for p in context.relevant_files:
                if self._is_test_path(p):
                    repo_test_files.add(p)
                else:
                    repo_source_files.add(p)

        # 2. Identify Direct Files and New Files
        matched_files = [m.file_path for m in context.matches if m.score > 0]
        # Preserve order while deduplicating
        ordered_files: list[str] = []
        for p in matched_files:
            if p not in ordered_files:
                ordered_files.append(p)

        # If no matches, fall back to relevant_files
        if not ordered_files:
            ordered_files = list(context.relevant_files)

        # Separate implementation files vs test files
        impl_files: list[str] = [p for p in ordered_files if not self._is_test_path(p)]
        test_files: list[str] = [p for p in ordered_files if self._is_test_path(p)]

        # Add explicit new files from references or title
        explicit_file_refs = [
            r.value
            for r in context.analysis.references
            if r.reference_type == ReferenceType.FILE
        ]
        issue_text = (
            f"{context.analysis.issue.title}\n{context.analysis.issue.description}"
        ).lower()

        wants_create = any(
            w in issue_text for w in ("create", "add new file", "implement new file")
        )
        wants_delete = any(
            w in issue_text for w in ("delete file", "remove file", "deprecated file")
        )

        for ref in explicit_file_refs:
            if ref in context.unresolved_references and wants_create:
                files_to_create.append(
                    FileChange(
                        path=ref,
                        change_type=ChangeType.CREATE,
                        reason=f"New file requested in issue: {ref}",
                    )
                )
                direct_files.append(ref)
            elif wants_delete and (ref in impl_files or ref in test_files):
                files_to_delete.append(
                    FileChange(
                        path=ref,
                        change_type=ChangeType.DELETE,
                        reason=f"File removal requested in issue: {ref}",
                    )
                )
                direct_files.append(ref)

        # 3. Direct Implementation Files to Modify
        seen_modified: set[str] = set()
        for p in impl_files:
            if p not in seen_modified and not any(
                df.path == p for df in files_to_delete
            ):
                seen_modified.add(p)
                direct_files.append(p)

                # Determine affected symbols for this file
                file_symbols = [
                    s.name for s in context.relevant_symbols if s.file_path == p
                ]
                if not file_symbols:
                    # Check chunks in matches for symbol names
                    file_symbols = [
                        m.symbol_name
                        for m in context.matches
                        if m.file_path == p and m.symbol_name
                    ]

                files_to_modify.append(
                    FileChange(
                        path=p,
                        change_type=ChangeType.MODIFY,
                        reason=f"Targeted implementation change for issue in {p}",
                        affected_symbols=list(dict.fromkeys(file_symbols)),
                    )
                )

        # 4. Symbol-Level Impact
        seen_syms: set[tuple[str, str]] = set()
        for sym in context.relevant_symbols:
            key = (sym.file_path, sym.name)
            if key not in seen_syms and sym.file_path in direct_files:
                seen_syms.add(key)
                symbol_changes.append(
                    SymbolChange(
                        file_path=sym.file_path,
                        symbol_name=sym.name,
                        symbol_type=sym.symbol_type,
                        change_type=ChangeType.MODIFY,
                        reason=f"Update symbol {sym.name} for requirements",
                    )
                )

        # Fallback to matches if relevant_symbols is empty
        if not symbol_changes:
            for m in context.matches:
                if m.symbol_name and m.file_path in direct_files:
                    key = (m.file_path, m.symbol_name)
                    if key not in seen_syms:
                        seen_syms.add(key)
                        symbol_changes.append(
                            SymbolChange(
                                file_path=m.file_path,
                                symbol_name=m.symbol_name,
                                symbol_type=SymbolType.FUNCTION,
                                change_type=ChangeType.MODIFY,
                                reason=f"Matched symbol {m.symbol_name} changes",
                            )
                        )

        # 5. Indirect Impact — Test Files Discovery
        for tf in repo_test_files.union(test_files):
            existing_tests.append(tf)

        for direct_path in direct_files:
            base_stem = Path(direct_path).stem  # e.g. "auth" from "app/auth.py"
            found_test = False

            # Search in existing tests
            for tf in existing_tests:
                if (
                    base_stem in Path(tf).name.lower()
                    or f"test_{base_stem}" in tf.lower()
                ):
                    found_test = True
                    if tf not in tests_to_modify:
                        tests_to_modify.append(tf)
                    if tf not in indirect_files:
                        indirect_files.append(tf)
                    if not any(fc.path == tf for fc in files_to_modify):
                        files_to_modify.append(
                            FileChange(
                                path=tf,
                                change_type=ChangeType.TEST,
                                reason=f"Update tests covering {direct_path}",
                            )
                        )

            # If no existing test file, plan new test file
            if not found_test and not self._is_test_path(direct_path):
                new_test_path = f"tests/test_{base_stem}.py"
                if new_test_path not in tests_to_add:
                    tests_to_add.append(new_test_path)
                    indirect_files.append(new_test_path)
                    files_to_create.append(
                        FileChange(
                            path=new_test_path,
                            change_type=ChangeType.CREATE,
                            reason=f"Add unit tests for {direct_path}",
                        )
                    )

        # Deduplicate existing tests list
        existing_tests = list(dict.fromkeys(existing_tests))
        tests_to_modify = list(dict.fromkeys(tests_to_modify))
        tests_to_add = list(dict.fromkeys(tests_to_add))
        direct_files = list(dict.fromkeys(direct_files))
        indirect_files = list(dict.fromkeys(indirect_files))

        return ImpactResult(
            files_to_modify=files_to_modify,
            files_to_create=files_to_create,
            files_to_delete=files_to_delete,
            symbol_changes=symbol_changes,
            existing_tests=existing_tests,
            tests_to_modify=tests_to_modify,
            tests_to_add=tests_to_add,
            direct_files=direct_files,
            indirect_files=indirect_files,
        )

    @staticmethod
    def _is_test_path(path: str) -> bool:
        """Check if relative path represents a test file."""
        low = path.lower()
        return (
            low.startswith("tests/")
            or "/tests/" in low
            or Path(low).name.startswith("test_")
            or Path(low).name.endswith("_test.py")
        )
