#!/usr/bin/env python3
"""Validate one wiki from its own files alone: every relative link resolves; every department in
the README has a page and every page is in the README; a term identity is declared at most once
across every Markdown file; every alias line has a target; every proposal line has a kind, a
source, and a capture ID that matches its file name; no proposal or page line matches a
restricted pattern. Python 3 only, no dependencies, runs on Windows. Exit nonzero with every
fault listed.

Usage: python3 wiki_check.py <wiki-root>

Prints one fault per line, `path:line: check: message`, sorted by path and line, then `<n> faults`,
and exits 1. A clean wiki prints one line counting files, declarations, aliases, and proposal
lines, and exits 0. Usage errors and an unreadable root exit 2. Reads nothing outside the root
and follows no links out of it. A line that matches a restricted pattern gets that one fault and
is withheld from every other check, so no message ever repeats its text.

The wiki's shape, which this file is the one reader of:
  README.md            the department table: the table whose first header cell is "Department";
                       each row links its page in departments/
  departments/*.md     one page per department
  proposals/<ID>.md    first non-blank line `# <ID> ...`; every later line
                       `- [kind] text (source: who, YYYY-MM-DD, where)`
  restricted.txt       one case-insensitive regular expression per line; `#` comments
A declaration is `**Term**:` at the start of a line; an alias is `**Term** → owner/repo: path`
(`->` also accepted). Lines in fenced code blocks, and inline code, are examples: only the
restricted check reads them. A department row whose page is missing is a dead link, reported by
the link check.
"""
import os
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

KINDS = ("term", "rule", "skill", "fact")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
CODE_SPAN = re.compile(r"(`+).+?\1")
DESTINATION = r"(<[^<>\n]*>|(?:[^\s()]|\([^\s()]*\))+)"  # <any text> or a path with balanced ( )
INLINE_LINK = re.compile(r"\]\(\s*" + DESTINATION + r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?\s*\)")
REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*" + DESTINATION)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
ALIAS = re.compile(r"^\*\*([^*]+)\*\*\s*(?:→|->)(.*)$")
ALIAS_TARGET = re.compile(r"^\s*[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\s*:\s*[^\s#]")
DECLARATION = re.compile(r"^\*\*([^*]+)\*\*\s*:")
ITEM = re.compile(r"^\s*[-*]\s+\[([^\]]*)\]\s*(.*)$")
SOURCE = re.compile(r"\(\s*source\s*:(.*)\)\s*$", re.IGNORECASE)  # greedy: to the line's last ")"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SECRETS = [  # (fault message, pattern); always on, whatever restricted.txt says
    ("looks like an OpenAI key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("looks like a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("looks like an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("looks like a private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("looks like a bearer token", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    # `name=value`, a quoted value, or a `name: value` holding a digit; prose such as
    # "Password: requirements are..." passes. TypeSafe publishes no key prefix, so its keys
    # (TYPESAFE_API_KEY=...) are caught here.
    ("looks like a password or key assignment", re.compile(
        r"(?:pass(?:word|wd)?|secret|token|api[_-]?key)[\"']?\s*"
        r"(?:=\s*[\"']?[^\s\"'<>{}]{8,}|:\s*(?:[\"'][^\s\"']{8,}|(?=[^\s\"'<>{}]*\d)[^\s\"'<>{}]{8,}))",
        re.IGNORECASE)),
    ("looks like a connection string with credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE)),
]


def main(argv):
    if len(argv) != 2:
        return usage("expected one argument, the wiki root")
    root = Path(argv[1])
    try:
        root = root.resolve(strict=True)
        if not root.is_dir():
            return usage(f"{argv[1]} is not a folder")
        pages = read_pages(root)
    except OSError as e:
        return usage(f"cannot read {argv[1]}: {e}")
    faults, counts = [], {"declarations": 0, "aliases": 0, "proposal lines": 0}
    patterns = read_restricted(root, faults)
    for rel, lines in pages.items():
        if lines is None:
            faults.append((rel, 1, "read", "cannot read the file as UTF-8 text"))
        else:
            pages[rel] = withhold_restricted(rel, lines, patterns, faults)
    for rel, lines in pages.items():
        if lines is not None:
            check_page(root, rel, lines, faults, counts)
    check_departments(root, pages, faults)
    check_terms(pages, faults, counts)
    return report(sorted(faults, key=lambda f: (f[0], f[1], f[2])), len(pages), counts)


def usage(why):
    print(f"wiki_check.py: {why}\nusage: python3 wiki_check.py <wiki-root>", file=sys.stderr)
    return 2


def read_pages(root):
    """Every Markdown file under root, `.git` and anything linked in from elsewhere skipped:
    relative posix path -> a list of (line number, text, is_example), where an example is a line
    of a fenced code block, fences included; None when the file is not UTF-8 text."""
    pages = {}
    for folder, dirs, files in os.walk(root, onerror=raise_error):
        dirs[:] = sorted(d for d in dirs if d != ".git" and is_own(root, Path(folder, d)))
        for name in sorted(files):
            path = Path(folder, name)
            if path.suffix.lower() != ".md" or not is_own(root, path):
                continue
            rel = path.relative_to(root).as_posix()
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (UnicodeDecodeError, OSError):
                pages[rel] = None
                continue
            lines, fence = [], None
            for number, line in enumerate(text.splitlines(), 1):
                marker = FENCE.match(line)
                if fence is None and marker:
                    fence = marker.group(1)
                elif fence and marker and marker.group(1)[0] == fence[0] \
                        and len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                    lines.append((number, line, True))
                    fence = None
                    continue
                lines.append((number, line, fence is not None))
            pages[rel] = lines
    return dict(sorted(pages.items()))


def is_own(root, path):
    """True for a real file or folder of the wiki: not a symlink, and (which also catches Windows
    junctions) still inside the root once resolved."""
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(root)
    except (ValueError, OSError):
        return False


def raise_error(error):
    raise error


def read_restricted(root, faults):
    """(fault message, compiled pattern) for each line of restricted.txt; a pattern that does
    not compile is a fault at its line."""
    patterns, path = [], root / "restricted.txt"
    if not path.is_file() or not is_own(root, path):
        return patterns
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (UnicodeDecodeError, OSError):
        faults.append(("restricted.txt", 1, "read", "cannot read the file as UTF-8 text"))
        return patterns
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            patterns.append((f"matches restricted.txt line {number}", re.compile(text, re.IGNORECASE)))
        except re.error as e:
            faults.append(("restricted.txt", number, "restricted", f"not a valid regular expression ({e})"))
    return patterns


def withhold_restricted(rel, lines, patterns, faults):
    """One restricted fault for each line that matches a secret shape or a restricted.txt
    pattern, examples included; returns the other lines, the only ones later checks see."""
    kept = []
    for number, line, example in lines:
        message = next((message for message, pattern in SECRETS + patterns if pattern.search(line)), None)
        if message:
            faults.append((rel, number, "restricted", message))
        else:
            kept.append((number, line, example))
    return kept


def check_page(root, rel, lines, faults, counts):
    """Links and aliases outside examples; the inbox format for a file in proposals/."""
    for number, line, example in lines:
        if example:
            continue
        for target in links(line):
            path = link_path(root, rel, target)
            if path is not None and not exists_inside(root, path):
                faults.append((rel, number, "link", f'"{target}" does not resolve to a file in the wiki'))
        alias = ALIAS.match(line)
        if alias:
            counts["aliases"] += 1
            if not ALIAS_TARGET.match(alias.group(2)):
                faults.append((rel, number, "alias", 'no target (expected "**Term** → owner/repo: path")'))
    if PurePosixPath(rel).parent.as_posix() == "proposals":
        check_proposal(rel, lines, faults, counts)


def check_proposal(rel, lines, faults, counts):
    """The first non-blank line is `# <ID>` with the ID equal to the file name; every later line is
    `- [kind] text (source: who, YYYY-MM-DD, where)`. A proposal holds no examples: every line counts."""
    stem = PurePosixPath(rel).stem
    body = [(number, line) for number, line, _ in lines if line.strip()]
    if not body:
        faults.append((rel, 1, "proposal", f'first line must be the header "# {stem}"'))
        return
    (number, header), items = body[0], body[1:]
    words = header.lstrip("#").split()
    if not header.startswith("#") or not words:
        faults.append((rel, number, "proposal", f'first line must be the header "# {stem}"'))
    elif words[0] != stem:
        faults.append((rel, number, "proposal", f'header ID "{words[0]}" does not match the file name'))
    for number, line in items:
        counts["proposal lines"] += 1
        item = ITEM.match(line)
        if not item:
            faults.append((rel, number, "proposal", 'not an item line (expected "- [kind] text (source: who, when, where)")'))
            continue
        kind, text = item.group(1).strip(), item.group(2)
        if kind not in KINDS:
            faults.append((rel, number, "proposal", f'unknown kind "{kind}" (expected term, rule, skill, or fact)'))
        source = SOURCE.search(text)
        if not source:
            faults.append((rel, number, "proposal", 'item has no source (expected "(source: who, when, where)" at the end)'))
            continue
        parts = [part.strip() for part in source.group(1).split(",") if part.strip()]
        if len(parts) < 3 or not any(DATE.fullmatch(part) for part in parts):
            faults.append((rel, number, "proposal", "source must name who, the date (YYYY-MM-DD), and where"))
        if not text[:source.start()].strip():
            faults.append((rel, number, "proposal", "item has a source but no text"))


def links(line):
    """The destination of every inline link and reference definition on a line, inline code
    skipped."""
    prose = CODE_SPAN.sub(" ", line)
    targets = INLINE_LINK.findall(prose)
    reference = REFERENCE.match(prose)
    if reference:
        targets.append(reference.group(1))
    return [t[1:-1] if t.startswith("<") else t for t in targets]


def link_path(root, rel, target):
    """The absolute path a link in page `rel` names (anchor and query dropped, symlinks resolved),
    or None for an external link or a same-page anchor."""
    if SCHEME.match(target) or target.startswith(("#", "//")):
        return None
    path = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
    full = (root if path.startswith("/") else (root / rel).parent) / path.lstrip("/")
    try:
        return full.resolve()
    except (ValueError, OSError):
        return full  # an impossible path, such as one holding a NUL byte; it will not exist


def exists_inside(root, path):
    try:
        return path.is_relative_to(root) and path.exists()
    except (ValueError, OSError):
        return False


def department_rows(lines):
    """(line number, first cell, row) for each row of the first README table whose first header
    cell is "Department"."""
    rows, state = [], "seek"
    for number, line, example in lines:
        is_row = not example and line.lstrip().startswith("|")
        cells = [cell.strip().strip("*").strip() for cell in line.strip().strip("|").split("|")]
        if state == "seek":
            state = "separator" if is_row and cells[0].lower() == "department" else "seek"
        elif state == "separator":
            state = "rows"
        elif is_row:
            rows.append((number, cells[0], line))
        else:
            break
    return rows


def check_departments(root, pages, faults):
    """Each department row links a page in departments/, and each page there is linked from a row."""
    folder, linked = (root / "departments").resolve(), set()
    for number, name, row in department_rows(pages.get("README.md") or []):
        row_pages = {path for path in (link_path(root, "README.md", t) for t in links(row))
                     if path is not None and path.parent == folder}
        if not row_pages:
            faults.append(("README.md", number, "departments", f'row "{name}" links no page in departments/'))
        linked |= row_pages
    for rel in pages:
        if PurePosixPath(rel).parent.as_posix() == "departments" and (root / rel).resolve() not in linked:
            faults.append((rel, 1, "departments", "page is not linked from the README department table"))


def check_terms(pages, faults, counts):
    """A term identity (lowercased, whitespace-normalized) is declared once across the wiki; every
    later declaration is a fault naming the first. Alias lines and examples never count."""
    first = {}
    for rel, lines in pages.items():
        for number, line, example in lines or []:
            declared = None if example else DECLARATION.match(line)
            if not declared:
                continue
            counts["declarations"] += 1
            term = " ".join(declared.group(1).lower().split())
            if term in first:
                faults.append((rel, number, "term", f'"{term}" is already declared at {first[term]}'))
            else:
                first[term] = f"{rel}:{number}"


def report(faults, file_count, counts):
    if faults:
        for path, line, check, message in faults:
            print(f"{path}:{line}: {check}: {message}")
        print(f"{len(faults)} fault{'' if len(faults) == 1 else 's'}")
        return 1
    print(f"ok: {file_count} files, {counts['declarations']} declarations, {counts['aliases']} aliases, "
          f"{counts['proposal lines']} proposal lines")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
