"""Deterministic issue text parsing and Markdown structure normalization."""

import re


class IssueParser:
    """Parser for normalizing issue text, headings, bullet lists, and code blocks."""

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize line breaks and collapse redundant blank lines."""
        if not text:
            return ""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in normalized.splitlines()]
        result: list[str] = []
        blank_count = 0
        for line in lines:
            if not line.strip():
                blank_count += 1
                if blank_count <= 2:
                    result.append("")
            else:
                blank_count = 0
                result.append(line)
        return "\n".join(result).strip()

    @staticmethod
    def extract_sections(text: str) -> dict[str, str]:
        """Extract Markdown heading sections into a dictionary of title -> body."""
        sections: dict[str, str] = {}
        if not text:
            return sections

        normalized = IssueParser.normalize_text(text)
        current_heading = "Overview"
        current_body: list[str] = []

        heading_regex = re.compile(r"^(#{1,6})\s+(.+)$")

        for line in normalized.splitlines():
            match = heading_regex.match(line)
            if match:
                if current_body:
                    sections[current_heading] = "\n".join(current_body).strip()
                    current_body = []
                current_heading = match.group(2).strip()
            else:
                current_body.append(line)

        if current_body:
            sections[current_heading] = "\n".join(current_body).strip()

        return sections

    @staticmethod
    def extract_bullet_items(text: str) -> list[str]:
        """Extract bullet point items and numbered list items from text."""
        items: list[str] = []
        if not text:
            return items

        item_regex = re.compile(r"^\s*(?:[-*+]|(?:\d+\.))\s+(.+)$")

        for line in text.splitlines():
            match = item_regex.match(line)
            if match:
                clean_item = match.group(1).strip()
                if clean_item:
                    items.append(clean_item)

        return items

    @staticmethod
    def extract_code_blocks(text: str) -> list[str]:
        """Extract triple-backtick code blocks from Markdown text."""
        if not text:
            return []
        pattern = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
        return [match.strip() for match in pattern.findall(text)]
