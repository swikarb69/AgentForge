"""Script to parse pytest output and surface failures as GitHub Actions annotations."""

import os
from pathlib import Path


def main() -> None:
    output_path = Path("pytest_output.txt")
    if not output_path.exists():
        print("No pytest_output.txt found.")
        return

    content = output_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    failures: list[str] = []
    for line in lines:
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            failures.append(line)
            # Emit GitHub Actions error annotation
            print(f"::error title=Pytest Failure::{line}")

    # Emit failure trace snippet if present
    if "=== FAILURES ===" in content or "=== ERRORS ===" in content:
        for line in lines[-100:]:
            if line.strip():
                print(f"::warning title=Pytest Trace::{line}")

    # Write markdown summary to GITHUB_STEP_SUMMARY if available
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file and Path(summary_file).parent.exists():
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write("### Pytest Failure Details\n\n")
            if failures:
                f.write(f"**Total Failures:** {len(failures)}\n\n")
                f.write("```\n")
                for fail in failures:
                    f.write(f"{fail}\n")
                f.write("```\n\n")
            f.write("#### Output Summary (Tail 100 lines)\n\n```\n")
            for line in lines[-100:]:
                f.write(f"{line}\n")
            f.write("```\n")


if __name__ == "__main__":
    main()
