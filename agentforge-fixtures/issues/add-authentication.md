# Add Authentication and JWT Validation

### Requirements
- Implement JWT token validation middleware in `backend/app/core/security.py`.
- Add `POST /api/v1/auth/login` endpoint for authenticating users.
- Support `Bearer` token header verification using `verify_token()` function.

### Acceptance Criteria
- Return HTTP 401 Unauthorized for missing or invalid JWT tokens.
- Return HTTP 200 OK with valid access token on successful authentication.
- Preserve backward compatibility for `/api/v1/health` and `/api/v1/version`.

### Constraints
- Must not introduce external database dependencies.
- Do not store plain text passwords.
