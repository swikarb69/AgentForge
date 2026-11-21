# AgentForge — Code Generation Engine (Sprint 4)

The **Code Generation Engine** is the fourth major layer of AgentForge. It takes a validated `ImplementationPlan` from Sprint 3 and produces structured, reviewable code proposals and unified diff patches outside the target repository, without ever mutating the target repository directly.

---

## 1. High-Level Architecture & Workflow

```text
ImplementationPlan (from Sprint 3)
         ↓
SourceContextBuilder (`backend/app/generation/context.py`)
  ├── Gathers authorized target files (files_to_modify, files_to_create, tests)
  ├── Computes SHA256 hashes of original files
  ├── Collects relevant AST symbols and reference files
  └── Enforces strict size (`MAX_CONTEXT_SIZE`) and path boundaries
         ↓
Structured CodeGenerator (`backend/app/generation/generator.py`)
  ├── Versioned prompt templates (`backend/app/generation/prompts.py`)
  ├── Injection-resistant delimiter boundaries
  └── Pluggable CodeGenerationProvider (Mock or Real)
         ↓
PatchValidator (`backend/app/generation/validator.py`)
  ├── Plan authorization verification (rejects out-of-scope targets)
  ├── Path security check (rejects traversal `..`, colons, symlink escapes)
  ├── Protected paths check (blocks `.env`, `.git`, credentials, etc.)
  ├── Prohibits dangerous file deletions
  └── Python syntax parsing via `ast.parse`
         ↓
PatchBuilder (`backend/app/generation/patch.py`)
  ├── Builds unified diffs against in-memory source snapshots
  ├── Formats auditable patch headers (Generation ID, Plan ID, Timestamp)
  └── Stores patch artifact in reviewable `agentforge-artifacts/generation/`
         ↓
GenerationResult & REST API
```

---

## 2. Key Architectural Guarantees & Safeguards

### Read-Only Repository Immutability
- The target repository is treated as strictly read-only.
- All code proposals are generated in memory and converted to unified diffs (`a/path` vs `b/path`).
- No files inside the target repository are created, overwritten, or deleted during code generation.
- Patch artifacts are written to `agentforge-artifacts/generation/{generation_id}.patch` outside the target repository tree.

### Plan Authorization Boundaries
- Code proposals must strictly adhere to the files and operations authorized by the `ImplementationPlan`.
- Any proposal targeting a file outside `plan.files_to_modify`, `plan.files_to_create`, or `plan.tasks` is immediately flagged as a `plan_scope_violation` and rejected.
- File deletions are strictly prohibited.

### Path Security & Traversal Prevention
- Target paths are normalized and strictly validated.
- Any path containing `..`, drive letters (`:`), leading slashes, or resolving outside the repository root is rejected.
- Protected files (e.g. `.env`, `.git`, `.github`, credentials, secrets, Dockerfile, etc.) cannot be modified by code generation.

### Syntax & Output Quality Validation
- Proposed Python files (`.py`) are parsed using Python's native `ast.parse` before approval.
- Files with syntax errors are rejected with detailed line numbers and error diagnostics.
- Empty content and oversized outputs (`MAX_GENERATED_OUTPUT`) are rejected.

---

## 3. Replaceable Provider Interface

AgentForge abstracts the generation model behind `CodeGenerationProvider`:

```python
class CodeGenerationProvider(ABC):
    @property
    @abstractmethod
    def provider_type(self) -> str:
        ...

    @abstractmethod
    async def generate(
        self,
        request: GenerationRequest,
        context_payload: dict[str, Any],
    ) -> list[GeneratedFileChange]:
        ...
```

### Available Providers
1. **`MockCodeGenerationProvider`** (Default):
   - Fully deterministic, offline provider.
   - Generates exact, test-passing mock implementations matching the plan's requirements.
   - Supports failure simulation, timeout injection, and custom proposals for QA testing.
2. **`GeminiCodeGenerationProvider`** (Optional):
   - Direct asynchronous REST integration via `httpx` to the Gemini API (`gemini-2.5-flash`).
   - Requires `GEMINI_API_KEY` in environment; safely falls back to mock provider if key is absent.

---

## 4. REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/generation/create` | Generates proposed code changes and patch artifact for an issue or plan. |
| `GET` | `/api/v1/generation/{generation_id}` | Retrieves structured generation result, validation status, and changes. |
| `GET` | `/api/v1/generation/{generation_id}/patch` | Retrieves raw unified diff patch as `text/plain` for review and download. |

---

## 5. Verification & Testing

The code generation subsystem is tested across unit and integration suites:
- **Unit Tests**:
  - `tests/unit/test_generation_models.py`: Path traversal validation, change type restrictions, Pydantic bounds.
  - `tests/unit/test_generation_provider.py`: Mock generation, failure injection, empty responses, provider factory.
  - `tests/unit/test_generation_context.py`: Authorized file gathering, SHA256 hashes, context size limits, symlink escapes, protected paths.
  - `tests/unit/test_generator.py`: Versioned prompt construction, task/requirement mapping, provider orchestration.
  - `tests/unit/test_patch_validator.py`: Plan scope violations, path safety, Python syntax parsing, empty content.
  - `tests/unit/test_patch_builder.py`: Unified diff generation (`a/path` vs `b/path`, `/dev/null` for creations), patch header, artifact saving.
- **Integration Tests**:
  - `tests/integration/test_generation_pipeline.py`: End-to-end on-the-fly generation, generation from pre-built plan, full REST API lifecycle, and repository SHA256 immutability verification.
