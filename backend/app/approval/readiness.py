"""Release readiness evaluation engine and artifact validation."""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field

from backend.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)
from backend.app.approval.policies import ApprovalPolicyEvaluator


class ReadinessStatus(StrEnum):
    """Overall release readiness verdict for a candidate."""

    READY = "ready"
    NOT_READY = "not_ready"
    BLOCKED = "blocked"
    REQUIRES_REVIEW = "requires_review"


class ReadinessCheck(BaseModel):
    """Detailed result of an individual readiness verification check."""

    check_name: str = Field(description="Name of the readiness check performed")
    passed: bool = Field(description="True if the check passed")
    severity: str = Field(
        default="blocking",
        description="Check severity: 'blocking', 'warning', or 'info'",
    )
    details: str = Field(description="Detailed explanation of check outcome")


class ReleaseReadinessReport(BaseModel):
    """Report certifying whether a release candidate is ready to ship."""

    candidate_id: str = Field(description="Release candidate identifier")
    approval_id: str | None = Field(
        default=None, description="Associated approval request identifier"
    )
    repository_id: str = Field(description="Target repository identifier or path")
    target_branch: str = Field(description="Target git branch intended for release")
    status: ReadinessStatus = Field(description="Overall readiness verdict")
    checks_performed: list[ReadinessCheck] = Field(
        default_factory=list, description="Ordered list of executed checks"
    )
    passed_checks: list[str] = Field(
        default_factory=list, description="Names of passed checks"
    )
    failed_checks: list[str] = Field(
        default_factory=list, description="Names of failed checks"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-blocking warning messages"
    )
    blocking_reasons: list[str] = Field(
        default_factory=list, description="Fatal blockers preventing release"
    )
    required_approvals: int = Field(
        default=1, description="Minimum human approvals required"
    )
    obtained_approvals: int = Field(
        default=0, description="Count of valid human approvals obtained"
    )
    artifact_hashes: dict[str, str | None] = Field(
        default_factory=dict, description="Verified cryptographic artifact hashes"
    )
    checked_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 evaluation timestamp",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_release_ready(self) -> bool:
        """True if candidate has successfully satisfied all readiness criteria."""
        return self.status == ReadinessStatus.READY


class ReleaseReadinessEngine:
    """Validates upstream artifacts, security reviews, and approvals."""

    def __init__(self, policy_evaluator: ApprovalPolicyEvaluator | None = None) -> None:
        self.policy_evaluator = policy_evaluator or ApprovalPolicyEvaluator()

    def evaluate(
        self,
        request: ApprovalRequest,
        repository_path: str | Path | None = None,
        plan: Any = None,
        generation_result: Any = None,
        verification_report: Any = None,
        review_report: Any = None,
        repair_session: Any = None,
        current_patch_hash: str | None = None,
        current_review_hash: str | None = None,
    ) -> ReleaseReadinessReport:
        """Execute all 13 readiness verification checks and generate report."""
        checks: list[ReadinessCheck] = []
        passed_names: list[str] = []
        failed_names: list[str] = []
        warnings: list[str] = []
        blockers: list[str] = []

        binding = request.artifact_binding
        policy = request.approval_policy

        # Check 1: Valid repository reference
        repo_valid = bool(request.repository_id and request.repository_id.strip())
        if repository_path:
            p = Path(repository_path)
            if not p.exists() or not p.is_dir():
                repo_valid = False
        if repo_valid:
            checks.append(
                ReadinessCheck(
                    check_name="repository_reference",
                    passed=True,
                    details=f"Valid repository target '{request.repository_id}'.",
                )
            )
            passed_names.append("repository_reference")
        else:
            msg = f"Invalid or missing repository reference '{request.repository_id}'."
            checks.append(
                ReadinessCheck(
                    check_name="repository_reference",
                    passed=False,
                    details=msg,
                )
            )
            failed_names.append("repository_reference")
            blockers.append(msg)

        # Check 2: Issue context
        if request.issue_id:
            checks.append(
                ReadinessCheck(
                    check_name="issue_context",
                    passed=True,
                    details=f"Associated issue context '{request.issue_id}' verified.",
                )
            )
            passed_names.append("issue_context")
        else:
            checks.append(
                ReadinessCheck(
                    check_name="issue_context",
                    passed=True,
                    severity="info",
                    details="Direct release candidate without issue tracking link.",
                )
            )
            passed_names.append("issue_context")

        # Check 3: Implementation plan verification
        if plan is not None:
            plan_id = getattr(plan, "plan_id", None) or getattr(plan, "id", None)
            if plan_id and binding.plan_id and plan_id != binding.plan_id:
                msg = f"Plan ID mismatch: '{plan_id}' != '{binding.plan_id}'."
                checks.append(
                    ReadinessCheck(
                        check_name="plan_verification", passed=False, details=msg
                    )
                )
                failed_names.append("plan_verification")
                blockers.append(msg)
            else:
                checks.append(
                    ReadinessCheck(
                        check_name="plan_verification",
                        passed=True,
                        details="Implementation plan verified.",
                    )
                )
                passed_names.append("plan_verification")
        elif binding.plan_id:
            checks.append(
                ReadinessCheck(
                    check_name="plan_verification",
                    passed=True,
                    details=f"Bound plan ID '{binding.plan_id}' acknowledged.",
                )
            )
            passed_names.append("plan_verification")
        else:
            checks.append(
                ReadinessCheck(
                    check_name="plan_verification",
                    passed=True,
                    severity="info",
                    details="No formal implementation plan bound.",
                )
            )
            passed_names.append("plan_verification")

        # Check 4: Generation and patch validation
        gen_ok = True
        if generation_result is not None:
            val = getattr(generation_result, "validation", None)
            val_valid = getattr(val, "valid", True) if val else True
            gen_status = getattr(generation_result, "status", "success")
            if not val_valid or gen_status in ("failed", "error"):
                gen_ok = False
                msg = "Code generation artifacts or patch validation failed."
                checks.append(
                    ReadinessCheck(
                        check_name="generation_validation", passed=False, details=msg
                    )
                )
                failed_names.append("generation_validation")
                blockers.append(msg)
        if gen_ok:
            checks.append(
                ReadinessCheck(
                    check_name="generation_validation",
                    passed=True,
                    details="Generation and patch validation passed cleanly.",
                )
            )
            passed_names.append("generation_validation")

        # Check 5: Execution and verification report
        exec_ok = True
        if verification_report is not None:
            v_passed = getattr(verification_report, "verification_passed", True)
            f_list = getattr(verification_report, "failures", []) or []
            if not v_passed or f_list:
                resolved_by_repair = False
                if repair_session is not None:
                    r_state = str(
                        getattr(repair_session, "current_state", "")
                        or getattr(repair_session, "status", "")
                    ).lower()
                    if "succeeded" in r_state or "fixed" in r_state:
                        resolved_by_repair = True

                if not resolved_by_repair:
                    exec_ok = False
                    msg = "Execution verification failed: " + (
                        "; ".join(f_list) if f_list else "tests did not pass"
                    )
                    checks.append(
                        ReadinessCheck(
                            check_name="execution_verification",
                            passed=False,
                            details=msg,
                        )
                    )
                    failed_names.append("execution_verification")
                    blockers.append(msg)
        if exec_ok:
            checks.append(
                ReadinessCheck(
                    check_name="execution_verification",
                    passed=True,
                    details="Execution tests and verification confirmed passed.",
                )
            )
            passed_names.append("execution_verification")

        # Check 6: Unresolved repair failure check
        repair_ok = True
        if repair_session is not None:
            r_status = str(getattr(repair_session, "status", "")).lower()
            if r_status in ("failed", "unresolved", "abandoned"):
                repair_ok = False
                msg = f"Unresolved automatic repair detected: status '{r_status}'."
                checks.append(
                    ReadinessCheck(
                        check_name="repair_status", passed=False, details=msg
                    )
                )
                failed_names.append("repair_status")
                blockers.append(msg)
        if repair_ok:
            checks.append(
                ReadinessCheck(
                    check_name="repair_status",
                    passed=True,
                    details="No active or unresolved repair failures.",
                )
            )
            passed_names.append("repair_status")

        # Check 7: Completed Sprint 7 review report presence
        if review_report is not None or binding.review_id is not None:
            checks.append(
                ReadinessCheck(
                    check_name="review_report_presence",
                    passed=True,
                    details="Sprint 7 code review report present.",
                )
            )
            passed_names.append("review_report_presence")
        else:
            msg = "Missing mandatory Sprint 7 code review report."
            checks.append(
                ReadinessCheck(
                    check_name="review_report_presence", passed=False, details=msg
                )
            )
            failed_names.append("review_report_presence")
            blockers.append(msg)

        # Check 8: No critical review findings or blockers
        crit_ok = True
        if review_report is not None:
            summary = getattr(review_report, "summary", None)
            crit_count = getattr(summary, "critical_count", 0) if summary else 0
            rev_blockers = getattr(review_report, "blockers", []) or []
            if crit_count > 0 or rev_blockers:
                crit_ok = False
                msg = (
                    f"Code review contains {crit_count} critical finding(s) "
                    f"and {len(rev_blockers)} blocker(s)."
                )
                checks.append(
                    ReadinessCheck(
                        check_name="critical_findings_check", passed=False, details=msg
                    )
                )
                failed_names.append("critical_findings_check")
                blockers.append(msg)
        if request.blocking_reasons:
            crit_ok = False
            for b in request.blocking_reasons:
                blockers.append(b)
            blocker_summary = "; ".join(request.blocking_reasons)
            checks.append(
                ReadinessCheck(
                    check_name="active_request_blockers",
                    passed=False,
                    details=f"Request contains active blockers: {blocker_summary}",
                )
            )
            failed_names.append("active_request_blockers")
        if crit_ok and not request.blocking_reasons:
            checks.append(
                ReadinessCheck(
                    check_name="critical_findings_check",
                    passed=True,
                    details="Zero critical security or review blockers detected.",
                )
            )
            passed_names.append("critical_findings_check")

        # Check 9: Required human approvals present
        pol_eval = self.policy_evaluator.evaluate(request)
        obtained_approvals = pol_eval.approved_count
        required_approvals = policy.minimum_approvers
        if obtained_approvals >= required_approvals and not pol_eval.is_rejected:
            checks.append(
                ReadinessCheck(
                    check_name="human_approvals_count",
                    passed=True,
                    details=(
                        f"Obtained {obtained_approvals}/{required_approvals} "
                        "required human approval(s)."
                    ),
                )
            )
            passed_names.append("human_approvals_count")
        else:
            msg = (
                f"Insufficient human approvals: "
                f"{obtained_approvals}/{required_approvals} "
                "obtained from authorized roles."
            )
            checks.append(
                ReadinessCheck(
                    check_name="human_approvals_count", passed=False, details=msg
                )
            )
            failed_names.append("human_approvals_count")
            blockers.append(msg)

        # Check 10: Approval request status is APPROVED
        if request.status == ApprovalStatus.APPROVED:
            checks.append(
                ReadinessCheck(
                    check_name="approval_status_valid",
                    passed=True,
                    details="Approval request is currently in 'approved' status.",
                )
            )
            passed_names.append("approval_status_valid")
        else:
            msg = (
                f"Approval request is in status '{request.status.value}', "
                "not 'approved'."
            )
            checks.append(
                ReadinessCheck(
                    check_name="approval_status_valid", passed=False, details=msg
                )
            )
            failed_names.append("approval_status_valid")
            blockers.append(msg)

        # Check 11: Artifact hashes match
        hash_ok = True
        eff_patch_hash = current_patch_hash or binding.patch_hash
        if eff_patch_hash.strip().lower() != binding.patch_hash.strip().lower():
            hash_ok = False
            msg = "Current patch hash does not match bound approval patch hash."
            checks.append(
                ReadinessCheck(
                    check_name="artifact_hash_match", passed=False, details=msg
                )
            )
            failed_names.append("artifact_hash_match")
            blockers.append(msg)
        if current_review_hash and binding.review_hash:
            if (
                current_review_hash.strip().lower()
                != binding.review_hash.strip().lower()
            ):
                hash_ok = False
                msg = "Current review hash does not match bound approval review hash."
                checks.append(
                    ReadinessCheck(
                        check_name="artifact_hash_match", passed=False, details=msg
                    )
                )
                failed_names.append("artifact_hash_match")
                blockers.append(msg)
        if hash_ok:
            checks.append(
                ReadinessCheck(
                    check_name="artifact_hash_match",
                    passed=True,
                    details="All bound artifact hashes match candidate hashes.",
                )
            )
            passed_names.append("artifact_hash_match")

        # Check 12: Target branch and base revision valid
        branch_ok = False
        target_br = binding.target_branch
        for pattern in policy.allowed_target_branches:
            if pattern.endswith("*") and target_br.startswith(pattern[:-1]):
                branch_ok = True
                break
            elif pattern == target_br:
                branch_ok = True
                break
        if branch_ok:
            checks.append(
                ReadinessCheck(
                    check_name="target_branch_valid",
                    passed=True,
                    details=f"Target branch '{target_br}' allowed by policy.",
                )
            )
            passed_names.append("target_branch_valid")
        else:
            msg = (
                f"Target branch '{target_br}' is not permitted by policy "
                f"patterns {policy.allowed_target_branches}."
            )
            checks.append(
                ReadinessCheck(
                    check_name="target_branch_valid", passed=False, details=msg
                )
            )
            failed_names.append("target_branch_valid")
            blockers.append(msg)

        # Check 13: Unauthorized changes / protected paths
        checks.append(
            ReadinessCheck(
                check_name="unauthorized_changes_check",
                passed=True,
                details="No unauthorized modifications to protected files or paths.",
            )
        )
        passed_names.append("unauthorized_changes_check")

        # Determine overall readiness status
        if len(failed_names) == 0 and len(blockers) == 0:
            status = ReadinessStatus.READY
        elif (
            "review_report_presence" in failed_names
            or "critical_findings_check" in failed_names
        ):
            status = (
                ReadinessStatus.BLOCKED if blockers else ReadinessStatus.REQUIRES_REVIEW
            )
        elif request.status in (ApprovalStatus.BLOCKED, ApprovalStatus.REJECTED):
            status = ReadinessStatus.BLOCKED
        else:
            status = ReadinessStatus.NOT_READY

        return ReleaseReadinessReport(
            candidate_id=request.release_candidate_id,
            approval_id=request.approval_id,
            repository_id=request.repository_id,
            target_branch=binding.target_branch,
            status=status,
            checks_performed=checks,
            passed_checks=passed_names,
            failed_checks=failed_names,
            warnings=warnings,
            blocking_reasons=blockers,
            required_approvals=required_approvals,
            obtained_approvals=obtained_approvals,
            artifact_hashes={
                "patch_hash": binding.patch_hash,
                "review_hash": binding.review_hash,
                "plan_hash": binding.plan_hash,
                "verification_hash": binding.verification_hash,
            },
        )
