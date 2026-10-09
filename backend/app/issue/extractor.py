"""Requirement and acceptance criteria extraction engine."""

import re

from backend.app.issue.models import (
    AcceptanceCriterion,
    IssueRequirement,
    RequirementType,
)
from backend.app.issue.parser import IssueParser


class RequirementExtractor:
    """Extracts explicit requirements and acceptance criteria from issue text."""

    REQUIREMENT_KEYWORDS = {
        "must",
        "should",
        "shall",
        "need to",
        "needs to",
        "required",
        "add",
        "fix",
        "update",
        "remove",
        "implement",
        "support",
        "allow",
        "reject",
    }

    REQUIREMENT_SECTION_NAMES = {
        "requirements",
        "functional requirements",
        "tasks",
        "scope",
        "changes needed",
        "to do",
        "todo",
    }

    CRITERIA_SECTION_NAMES = {
        "acceptance criteria",
        "acceptance tests",
        "definition of done",
        "expected behavior",
        "success criteria",
    }

    def extract_requirements(
        self, title: str, description: str
    ) -> list[IssueRequirement]:
        """Extract explicit requirements from issue title and description text."""
        requirements: list[IssueRequirement] = []
        seen_descriptions: set[str] = set()

        sections = IssueParser.extract_sections(description)

        # 1. Extract requirements from explicit requirement sections
        for heading, body in sections.items():
            if heading.lower() in self.REQUIREMENT_SECTION_NAMES:
                bullet_items = IssueParser.extract_bullet_items(body)
                for item in bullet_items:
                    if item.lower() not in seen_descriptions:
                        seen_descriptions.add(item.lower())
                        req_id = f"REQ-{len(requirements) + 1}"
                        requirements.append(
                            IssueRequirement(
                                id=req_id,
                                description=item,
                                requirement_type=RequirementType.FUNCTIONAL,
                            )
                        )

        # 2. Extract heuristics requirements from title if none found in sections
        if not requirements and title.strip():
            seen_descriptions.add(title.strip().lower())
            requirements.append(
                IssueRequirement(
                    id="REQ-1",
                    description=title.strip(),
                    requirement_type=RequirementType.FUNCTIONAL,
                )
            )

        # 3. Extract sentence-based keyword requirements from description
        sentence_regex = re.compile(r"([^.!?\n]+[.!?]?)")
        for line in description.splitlines():
            # Skip code blocks and headings
            if line.startswith("#") or line.startswith("```"):
                continue
            for match in sentence_regex.finditer(line):
                sentence = match.group(1).strip()
                if not sentence or len(sentence) < 10:
                    continue

                sentence_lower = sentence.lower()
                if any(kw in sentence_lower for kw in self.REQUIREMENT_KEYWORDS):
                    if sentence_lower not in seen_descriptions:
                        seen_descriptions.add(sentence_lower)
                        req_id = f"REQ-{len(requirements) + 1}"
                        requirements.append(
                            IssueRequirement(
                                id=req_id,
                                description=sentence,
                                requirement_type=RequirementType.FUNCTIONAL,
                            )
                        )

        return requirements

    def extract_acceptance_criteria(
        self, description: str
    ) -> list[AcceptanceCriterion]:
        """Extract explicit acceptance criteria if an explicit section is present."""
        criteria: list[AcceptanceCriterion] = []
        sections = IssueParser.extract_sections(description)

        for heading, body in sections.items():
            if heading.lower() in self.CRITERIA_SECTION_NAMES:
                bullet_items = IssueParser.extract_bullet_items(body)
                for item in bullet_items:
                    ac_id = f"AC-{len(criteria) + 1}"
                    criteria.append(
                        AcceptanceCriterion(
                            id=ac_id,
                            description=item,
                            is_explicit=True,
                        )
                    )

        return criteria
