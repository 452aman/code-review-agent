"""Parse unified diffs (GitHub PRs) into SourceFiles that remember which lines changed."""
import re

from app.core.types import SourceFile, detect_language

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def split_diff(diff: str) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            current = path[2:] if path.startswith("b/") else path
            if path == "/dev/null":
                current = None
            else:
                files[current] = []
        elif current is not None:
            files[current].append(line)
    return files


def parse_diff(diff: str) -> list[SourceFile]:
    """Rebuild the *new* side of each file from hunks only (context + added lines).

    We don't have full files from a bare diff, so line numbers refer to the post-change file
    and `content` is padded so that line N of `content` is line N of the real file.
    """
    result: list[SourceFile] = []
    for path, lines in split_diff(diff).items():
        new_lines: dict[int, str] = {}
        changed: list[int] = []
        n = 0
        for line in lines:
            m = _HUNK.match(line)
            if m:
                n = int(m.group(1)) - 1
                continue
            if line.startswith("\\"):
                continue
            if line.startswith("-"):
                continue
            n += 1
            if line.startswith("+"):
                new_lines[n] = line[1:]
                changed.append(n)
            else:
                new_lines[n] = line[1:] if line.startswith(" ") else line
        if not changed:
            continue
        top = max(new_lines)
        content = "\n".join(new_lines.get(i, "") for i in range(1, top + 1))
        result.append(SourceFile(path=path, content=content, language=detect_language(path), changed_lines=changed))
    return result
