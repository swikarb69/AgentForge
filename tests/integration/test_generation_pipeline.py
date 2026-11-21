"""Integration tests for code generation, REST API, and repo immutability."""

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.generation.models import (
    CreateGenerationRequest,
    GenerationStatus,
)
from backend.app.generation.service import GenerationService
from backend.app.main import app
from backend.app.planning.models import (
    ChangeType,
    FileChange,
    ImplementationPlan,
    PlanTask,
    TestPlan,
)

client = TestClient(app)
FIXTURE_PATH = Path("agentforge-fixtures/sample-project").resolve()


def _get_tree_hashes(root: Path) -> dict[str, str]:
    """Compute sha256 hashes of all files under root to verify immutability."""
    tree_hashes = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            tree_hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return tree_hashes


def test_scenario_1_end_to_end_on_the_fly_generation() -> None:
    """Scenario 1: End-to-end generation from issue without explicit plan."""
    service = GenerationService()
    before_hashes = _get_tree_hashes(FIXTURE_PATH)

    request = CreateGenerationRequest(
        repository_path=str(FIXTURE_PATH),
        title="Fix verify_token in app/auth.py",
        description=(
            "### Requirements\n"
            "- Reject expired JWT tokens in `verify_token`.\n"
            "- Return HTTP 401.\n"
            "### Acceptance Criteria\n"
            "- Expired tokens return 401."
        ),
        issue_id="ISSUE-GEN-1",
    )

    import anyio

    result = anyio.run(service.generate_code, request)

    assert result.status == GenerationStatus.VALIDATED
    assert result.validation.valid is True
    assert len(result.changes) > 0
    assert any(c.path == "app/auth.py" for c in result.changes)

    # Verify patch content and artifact storage
    assert "# AgentForge Unified Diff Patch" in result.patch_content
    assert "--- a/app/auth.py" in result.patch_content
    assert result.patch_file_path is not None

    artifact_file = Path(result.patch_file_path)
    assert artifact_file.exists()
    assert artifact_file.read_text(encoding="utf-8") == result.patch_content

    # Verify target repository was NEVER modified
    after_hashes = _get_tree_hashes(FIXTURE_PATH)
    assert before_hashes == after_hashes


def test_scenario_2_generation_with_explicit_plan() -> None:
    """Scenario 2: Code generation consuming an existing ImplementationPlan."""
    service = GenerationService()
    before_hashes = _get_tree_hashes(FIXTURE_PATH)

    plan = ImplementationPlan(
        id="PLAN-EXPLICIT-1",
        objective="Update authentication and add token module",
        summary="Modify auth.py and create tokens.py",
        files_to_modify=[
            FileChange(
                path="app/auth.py",
                change_type=ChangeType.MODIFY,
                reason="Add token expiration check",
                affected_symbols=["verify_token"],
            )
        ],
        files_to_create=[
            FileChange(
                path="app/tokens.py",
                change_type=ChangeType.CREATE,
                reason="Create tokens helper module",
            )
        ],
        tasks=[
            PlanTask(
                task_id="TASK-1",
                title="Update verify_token",
                description="Reject expired token",
                order=1,
                target_files=["app/auth.py"],
                target_symbols=["verify_token"],
                change_type=ChangeType.MODIFY,
                verification="Run pytest",
                addressed_requirements=["REQ-1"],
            ),
            PlanTask(
                task_id="TASK-2",
                title="Create token helper",
                description="Add token helper functions",
                order=2,
                target_files=["app/tokens.py"],
                target_symbols=[],
                change_type=ChangeType.CREATE,
                verification="Check import",
                addressed_requirements=["REQ-2"],
            ),
        ],
        test_plan=TestPlan(
            existing_tests=["tests/test_auth.py"],
            tests_to_modify=[],
            tests_to_add=[],
        ),
    )

    request = CreateGenerationRequest(
        repository_path=str(FIXTURE_PATH),
        plan=plan,
    )

    import anyio

    result = anyio.run(service.generate_code, request)

    assert result.status == GenerationStatus.VALIDATED
    assert len(result.changes) == 2
    paths = {c.path for c in result.changes}
    assert paths == {"app/auth.py", "app/tokens.py"}

    assert "--- a/app/auth.py" in result.patch_content
    assert "--- /dev/null" in result.patch_content

    # Target repository immutability check
    after_hashes = _get_tree_hashes(FIXTURE_PATH)
    assert before_hashes == after_hashes


def test_scenario_3_generation_rest_api_lifecycle() -> None:
    """Scenario 3: Test POST /create, GET /{id}, and GET /{id}/patch endpoints."""
    payload = {
        "repository_path": str(FIXTURE_PATH),
        "title": "Fix token validation in app/auth.py",
        "description": "Ensure expired tokens return False in verify_token",
        "issue_id": "ISSUE-API-1",
    }

    # 1. POST /api/v1/generation/create
    post_res = client.post("/api/v1/generation/create", json=payload)
    assert post_res.status_code == 200
    data = post_res.json()

    assert data["status"] in ("validated", "completed")
    generation_id = data["generation_id"]
    assert generation_id.startswith("GEN-")
    assert len(data["changes"]) > 0
    assert "# AgentForge Unified Diff Patch" in data["patch_content"]

    # 2. GET /api/v1/generation/{generation_id}
    get_res = client.get(f"/api/v1/generation/{generation_id}")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["generation_id"] == generation_id
    assert get_data["status"] == data["status"]

    # 3. GET /api/v1/generation/{generation_id}/patch
    patch_res = client.get(f"/api/v1/generation/{generation_id}/patch")
    assert patch_res.status_code == 200
    assert "text/plain" in patch_res.headers["content-type"]
    assert patch_res.text == data["patch_content"]

    # 4. GET 404 for non-existent generation
    bad_res = client.get("/api/v1/generation/GEN-NONEXISTENT")
    assert bad_res.status_code == 404


def test_scenario_4_generation_api_rejects_missing_title() -> None:
    """Scenario 4: Rejection when plan is missing and title is not provided."""
    payload = {
        "repository_path": str(FIXTURE_PATH),
        "title": None,
    }
    response = client.post("/api/v1/generation/create", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert any("title is required" in err for err in data["errors"])
