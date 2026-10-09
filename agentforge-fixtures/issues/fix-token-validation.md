# Fix Token Validation Exception Handling

### Requirements
- Fix token expiry parsing in `RepositoryIndex.search` and `PythonASTParser.parse_source`.
- Handle `ExpiredSignatureError` gracefully in `backend/app/issue/parser.py`.

### Acceptance Criteria
- Expired tokens must return explicit 401 response instead of 500 error.
- Unit tests must be added to `tests/unit/test_references.py`.

### Constraints
- Keep existing error format unchanged.
