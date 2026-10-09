# AgentForge — Issue Intelligence (Sprint 2)

The **Issue Intelligence Engine** is the second major cognitive layer of AgentForge. It parses unstructured software issues (GitHub issues, bug reports, feature requests) into structured engineering requirements and deterministically maps them to Sprint 1 Repository Intelligence (`RepositoryIndex`, `Symbol`, `CodeChunk`).

---

## Architecture Overview

```text
Software Issue (Text / Markdown)
      ↓
Issue Parser (`IssueParser`)
  ├── Section Normalization
  ├── Bullet Point Extraction
  └── Code Block Extraction
      ↓
Requirement Extractor (`RequirementExtractor`)
  ├── Functional & Non-Functional Requirements
  └── Explicit Acceptance Criteria
      ↓
Reference Extractor (`ReferenceExtractor`)
  ├── File References (`backend/app/auth.py`)
  ├── Symbol References (`AuthService.verify_token`)
  ├── HTTP Endpoints (`POST /api/v1/auth`)
  ├── Engineering Constraints (`must not`, `do not`)
  └── Technical Keywords
      ↓
Repository Matcher (`IssueMatcher`)
  ├── Explicit Reference Matching (+10 pts)
  ├── Endpoint Route Matching (+8 pts)
  ├── Symbol Name Keyword Matching (+5 pts)
  ├── File Path Keyword Matching (+4 pts)
  ├── Docstring Keyword Matching (+3 pts)
  ├── Chunk Content Keyword Matching (+1 pt)
  └── Test File Boost (+2 pts)
      ↓
Issue Context Pipeline (`IssueContextBuilder`)
  ├── Structured `IssueAnalysis`
  ├── Ranked `IssueMatch` breakdown
  ├── Relevant Files, Symbols, Chunks
  └── Unresolved Reference Detection
```

---

## Key Components

### 1. Data Models (`backend/app/issue/models.py`)
- **`Issue`**: Normalized issue payload with title, description, and source origin.
- **`IssueRequirement`**: Categorized functional and non-functional requirements.
- **`AcceptanceCriterion`**: Stated or derived acceptance criteria.
- **`IssueConstraint`**: Development or architectural constraints.
- **`IssueReference`**: Extracted entity references (file, symbol, endpoint).
- **`MatchReason`**: Heuristic scoring breakdown explaining relevance.
- **`IssueMatch`**: Ranked repository candidate with cumulative score and match reasons.
- **`IssueAnalysis`**: Complete structured understanding of an issue.
- **`IssueContext`**: Complete issue-to-repository context package.

### 2. Issue Parser (`backend/app/issue/parser.py`)
Parses raw markdown/text into normalized sections, headers, bullet items, and code blocks without relying on external LLMs.

### 3. Requirement Extractor (`backend/app/issue/extractor.py`)
Extracts engineering requirements and explicit acceptance criteria using deterministic pattern heuristics.

### 4. Reference Extractor (`backend/app/issue/references.py`)
Identifies file paths, symbols (functions, methods, classes, dotted references), HTTP API routes, constraints, and keywords.

### 5. Repository-Aware Matcher (`backend/app/issue/matcher.py`)
Maps extracted requirements and references against Sprint 1 code chunks and symbols, computing explainable match scores and flagging unresolved references.

### 6. Service & API (`backend/app/issue/service.py`, `backend/app/api/routes/issues.py`)
Provides high-level pipeline orchestration and REST endpoints:
- `POST /api/v1/issues/analyze`: Analyzes raw issue text into structured requirements.
- `POST /api/v1/issues/context`: Maps issue text to repository intelligence.

---

## API Usage Examples

### Analyze Issue Endpoint
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/issues/analyze" \
     -H "Content-Type: application/json" \
     -d '{
           "title": "Add authentication in app/auth.py",
           "description": "### Requirements\n- Implement POST /api/v1/auth/login endpoint in `app/auth.py`.\n- Support `verify_token` function."
         }'
```

### Issue Context Mapping Endpoint
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/issues/context" \
     -H "Content-Type: application/json" \
     -d '{
           "repository_path": "agentforge-fixtures/sample-project",
           "title": "Fix AuthService.verify_token in app/auth.py",
           "description": "Refactor `verify_token()` in `app/auth.py`."
         }'
```
