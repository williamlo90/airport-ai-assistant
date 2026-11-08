"""Check public Markdown paths without network access or dependencies."""

from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "LICENSE",
    "guides/README.md",
    "guides/PRODUCT_TOUR.md",
    "guides/CASE_STUDY.md",
    "guides/GETTING_STARTED.md",
    "guides/VERIFICATION.md",
    ".github/workflows/quality-gate.yml",
)


def main() -> int:
    failures = [f"Missing review entry point: {name}" for name in REQUIRED if not (ROOT / name).is_file()]
    tracked = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    documents = [
        name for name in tracked
        if name == "README.md" or (name.startswith("guides/") and name.endswith(".md"))
        or name in (
            "backend/README.md", "frontend/README.md",
            "frontend/prototype/README.md", "reference_service/README.md",
        )
    ]
    for name in sorted(set(documents)):
        path = ROOT / name
        if not path.is_file():
            failures.append(f"Missing document: {name}")
            continue
        content = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for match in re.finditer(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", content):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (path.parent / unquote(parsed.path)).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.exists():
                failures.append(f"Broken local link in {name}: {target}")
    if failures:
        print("Repository documentation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Repository documentation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
