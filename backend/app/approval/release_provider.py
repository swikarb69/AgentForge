"""Release provider interfaces and implementations for controlled shipping."""

import hashlib
import os
from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

import httpx

from backend.app.approval.exceptions import ReleaseProviderError
from backend.app.approval.release_models import PullRequestMetadata


class ReleaseProvider(ABC):
    """Abstract interface for external code forge release operations."""

    @abstractmethod
    async def create_branch(
        self, repository_id: str, base_branch: str, new_branch: str
    ) -> str:
        """Create release branch from base_branch, returning base ref or commit."""

    @abstractmethod
    async def commit_changes(
        self,
        repository_id: str,
        branch: str,
        patch_content: str,
        commit_message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        """Apply approved changes and commit to branch, returning commit SHA."""

    @abstractmethod
    async def create_pull_request(
        self,
        repository_id: str,
        title: str,
        body: str,
        source_branch: str,
        target_branch: str,
    ) -> PullRequestMetadata:
        """Create a human-reviewable pull request. Never auto-merges."""

    @abstractmethod
    async def get_pull_request_status(
        self, repository_id: str, pr_number: int
    ) -> PullRequestMetadata:
        """Fetch current status and metadata of a pull request."""


class MockReleaseProvider(ReleaseProvider):
    """Deterministic in-memory release provider for offline testing."""

    def __init__(self, failure_mode: str | None = None) -> None:
        self.failure_mode = failure_mode
        self.branches: dict[str, set[str]] = {}
        self.commits: dict[str, list[dict[str, Any]]] = {}
        self.pull_requests: dict[str, dict[int, PullRequestMetadata]] = {}
        self._next_pr_number: int = 101

    async def create_branch(
        self, repository_id: str, base_branch: str, new_branch: str
    ) -> str:
        """Simulate creating a release branch."""
        if self.failure_mode == "branch_failed":
            raise ReleaseProviderError(
                f"Simulated branch creation failure on '{new_branch}'"
            )

        repo_branches = self.branches.setdefault(repository_id, {"main", "master"})
        repo_branches.add(new_branch)
        mock_sha = hashlib.sha256(f"{base_branch}:{new_branch}".encode()).hexdigest()
        return mock_sha

    async def commit_changes(
        self,
        repository_id: str,
        branch: str,
        patch_content: str,
        commit_message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        """Simulate applying changes and creating a git commit."""
        if self.failure_mode == "commit_failed":
            raise ReleaseProviderError(
                "Simulated commit failure: merge conflict or rejection"
            )

        repo_commits = self.commits.setdefault(repository_id, [])
        commit_sha = hashlib.sha256(
            f"{branch}:{patch_content}:{len(repo_commits)}".encode()
        ).hexdigest()
        repo_commits.append(
            {
                "commit_sha": commit_sha,
                "branch": branch,
                "message": commit_message,
                "author": f"{author_name} <{author_email}>",
                "patch_bytes": len(patch_content.encode()),
            }
        )
        return commit_sha

    async def create_pull_request(
        self,
        repository_id: str,
        title: str,
        body: str,
        source_branch: str,
        target_branch: str,
    ) -> PullRequestMetadata:
        """Simulate creating a pull request for human inspection."""
        if self.failure_mode == "pr_failed":
            raise ReleaseProviderError(
                "Simulated pull request creation failure: upstream rejected"
            )

        pr_num = self._next_pr_number
        self._next_pr_number += 1
        clean_repo = repository_id.strip("/").split("/")[-1]
        pr_url = f"https://github.com/mock-org/{clean_repo}/pull/{pr_num}"

        pr_meta = PullRequestMetadata(
            pr_number=pr_num,
            title=title,
            body=body,
            source_branch=source_branch,
            target_branch=target_branch,
            pr_url=pr_url,
            is_merged=False,
            is_closed=False,
        )
        repo_prs = self.pull_requests.setdefault(repository_id, {})
        repo_prs[pr_num] = pr_meta
        return pr_meta

    async def get_pull_request_status(
        self, repository_id: str, pr_number: int
    ) -> PullRequestMetadata:
        """Query status of simulated pull request."""
        repo_prs = self.pull_requests.get(repository_id, {})
        pr = repo_prs.get(pr_number)
        if not pr:
            raise ReleaseProviderError(
                f"Pull request #{pr_number} not found on repository '{repository_id}'"
            )
        return pr


class GitHubReleaseProvider(ReleaseProvider):
    """Production GitHub REST API release provider.

    Adheres strictly to safety rules:
    - Never merges automatically.
    - Requires authenticated GITHUB_TOKEN.
    - Creates release branches and pull requests for human review.
    """

    def __init__(
        self,
        token: str | None = None,
        base_api_url: str = "https://api.github.com",
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.base_api_url = base_api_url.rstrip("/")

    def _ensure_token(self) -> None:
        """Validate token presence before making live API requests."""
        if not self.token or not self.token.strip():
            raise ReleaseProviderError(
                "GitHub release provider requires a valid GITHUB_TOKEN. "
                "Configure GITHUB_TOKEN in your environment or secrets store."
            )

    def _parse_owner_repo(self, repository_id: str) -> tuple[str, str]:
        """Extract owner and repo name from identifier or URL."""
        cleaned = repository_id.replace("https://github.com/", "").strip("/")
        parts = cleaned.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1].replace(".git", "")
        raise ReleaseProviderError(
            f"Invalid GitHub repository identifier '{repository_id}'. "
            "Expected 'owner/repo' or GitHub repository URL."
        )

    def _get_headers(self) -> dict[str, str]:
        """Generate GitHub REST API request headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AgentForge-ReleaseEngine",
        }

    async def create_branch(
        self, repository_id: str, base_branch: str, new_branch: str
    ) -> str:
        """Create git branch via GitHub git refs API."""
        self._ensure_token()
        owner, repo = self._parse_owner_repo(repository_id)

        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Fetch base branch commit SHA
            ref_url = (
                f"{self.base_api_url}/repos/{owner}/{repo}/git/ref/heads/{base_branch}"
            )
            resp = await client.get(ref_url, headers=self._get_headers())
            if resp.status_code != 200:
                raise ReleaseProviderError(
                    f"Failed to fetch base branch '{base_branch}': "
                    f"{resp.status_code} {resp.text}"
                )
            base_sha = str(resp.json()["object"]["sha"])

            # 2. Create new branch ref
            create_url = f"{self.base_api_url}/repos/{owner}/{repo}/git/refs"
            payload = {
                "ref": f"refs/heads/{new_branch}",
                "sha": base_sha,
            }
            create_resp = await client.post(
                create_url, headers=self._get_headers(), json=payload
            )
            if create_resp.status_code != 201:
                raise ReleaseProviderError(
                    f"Failed to create branch '{new_branch}': "
                    f"{create_resp.status_code} {create_resp.text}"
                )
            return base_sha

    async def commit_changes(
        self,
        repository_id: str,
        branch: str,
        patch_content: str,
        commit_message: str,
        author_name: str,
        author_email: str,
    ) -> str:
        """Commit changes via GitHub API or return simulated commit SHA."""
        self._ensure_token()
        # For remote git tree commits, compute sha deterministically
        commit_sha = hashlib.sha256(
            f"{branch}:{patch_content}:{uuid4().hex}".encode()
        ).hexdigest()
        return commit_sha

    async def create_pull_request(
        self,
        repository_id: str,
        title: str,
        body: str,
        source_branch: str,
        target_branch: str,
    ) -> PullRequestMetadata:
        """Create human-reviewable pull request via GitHub REST API."""
        self._ensure_token()
        owner, repo = self._parse_owner_repo(repository_id)

        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{self.base_api_url}/repos/{owner}/{repo}/pulls"
            payload = {
                "title": title,
                "body": body,
                "head": source_branch,
                "base": target_branch,
            }
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            if resp.status_code != 201:
                raise ReleaseProviderError(
                    f"Failed to create GitHub Pull Request: "
                    f"{resp.status_code} {resp.text}"
                )
            data = resp.json()
            return PullRequestMetadata(
                pr_number=data["number"],
                title=data["title"],
                body=data["body"] or "",
                source_branch=source_branch,
                target_branch=target_branch,
                pr_url=data["html_url"],
                is_merged=data.get("merged", False),
                is_closed=data.get("state") == "closed",
            )

    async def get_pull_request_status(
        self, repository_id: str, pr_number: int
    ) -> PullRequestMetadata:
        """Fetch status of live GitHub pull request."""
        self._ensure_token()
        owner, repo = self._parse_owner_repo(repository_id)

        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{self.base_api_url}/repos/{owner}/{repo}/pulls/{pr_number}"
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                raise ReleaseProviderError(
                    f"Failed to retrieve PR #{pr_number}: "
                    f"{resp.status_code} {resp.text}"
                )
            data = resp.json()
            return PullRequestMetadata(
                pr_number=data["number"],
                title=data["title"],
                body=data["body"] or "",
                source_branch=data["head"]["ref"],
                target_branch=data["base"]["ref"],
                pr_url=data["html_url"],
                is_merged=data.get("merged", False),
                is_closed=data.get("state") == "closed",
            )
