"""Check repository-relative Markdown file links without network access."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors = []
    documents = [
        *ROOT.glob("*.md"),
        *ROOT.glob("docs/**/*.md"),
        *ROOT.glob("examples/**/*.md"),
        *ROOT.glob("skills/**/*.md"),
        *ROOT.glob("dogfood/*.md"),
    ]
    for document in documents:
        for target in re.findall(r"\]\(([^\s)]+)(?:\s+[^)]*)?\)", document.read_text(encoding="utf-8")):
            url = urlsplit(target.strip("<>"))
            if url.scheme or url.netloc or not url.path:
                continue
            if not (document.parent / unquote(url.path)).exists():
                errors.append(f"{document.relative_to(ROOT)}: missing {target}")
    print("\n".join(errors) if errors else f"Local file links checked in {len(documents)} Markdown documents")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
