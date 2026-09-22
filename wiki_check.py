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
lines, and exits 0. Usage errors and an unreadable root exit 2. Reads nothing outside the root,
follows no symlinks, and never prints the text a restricted pattern matched.

The wiki's shape, which this file is the one reader of:
  README.md            the department table: lines starting with `|` that link departments/*.md
  departments/*.md     one page per department
  proposals/<ID>.md    first non-blank line `# <ID> ...`; every later line `- [kind] text (source: ...)`
  restricted.txt       one case-insensitive regular expression per line; `#` comments
A declaration is `**Term**:` at the start of a line; an alias is `**Term** → owner/repo: path`
(`->` also accepted). Lines inside fenced code blocks are examples: only the restricted check
reads them. The README-to-page direction is the link check; the page-to-README direction is the
departments check.
"""
import os
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

KINDS = ("term", "rule", "skill", "fact")
FENCE = re.compile(r"^\s{0,3}(```|~~~)")
LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
ALIAS = re.compile(r"^\*\*([^*]+)\*\*\s*(?:→|->)(.*)$")
ALIAS_TARGET = re.compile(r"^\s*[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\s*:\s*\S")
DECLARATION = re.compile(r"^\*\*([^*]+)\*\*\s*:")
ITEM = re.compile(r"^\s*[-*]\s+\[([^\]]*)\]\s*(.*)$")
SOURCE = re.compile(r"\(\s*source\s*:[^)]*[^\s)][^)]*\)\s*$", re.IGNORECASE)
SECRETS = [  # (fault message, pattern); always on, whatever restricted.txt says
    ("looks like an OpenAI key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("looks like a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("looks like an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("looks like a private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("looks like a bearer token", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    # TypeSafe publishes no key prefix, so its keys (TYPESAFE_API_KEY=...) are caught here.
    ("looks like a password or key assignment", re.compile(
        r"(?:pass(?:word|wd)?|secret|token|api[_-]?key)[\"']?\s*[:=]\s*[\"']?[^\s\"'<>{}]{8,}", re.IGNORECASE)),
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
            continue
        check_page(root, rel, lines, patterns, faults, counts)
    check_departments(root, pages, faults)
    check_terms(pages, faults, counts)
    return report(sorted(faults, key=lambda f: (f[0], f[1], f[2])), len(pages), counts)


def usage(why):
    print(f"wiki_check.py: {why}\nusage: python3 wiki_check.py <wiki-root>", file=sys.stderr)
    return 2


def read_pages(root):
    """Every Markdown file under root, `.git` and symlinks skipped: relative posix path -> a list of
    (line number, text, is_example), where an example is a line inside or on a code fence; None
    when the file is not UTF-8 text."""
    pages = {}
    for folder, dirs, files in os.walk(root, onerror=raise_error):
        dirs[:] = sorted(d for d in dirs if d != ".git" and not os.path.islink(os.path.join(folder, d)))
        for name in sorted(files):
            path = Path(folder, name)
            if path.suffix.lower() != ".md" or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (UnicodeDecodeError, OSError):
                pages[rel] = None
                continue
            lines, fenced = [], False
            for number, line in enumerate(text.splitlines(), 1):
                fence = bool(FENCE.match(line))
                lines.append((number, line, fenced or fence))
                fenced ^= fence
            pages[rel] = lines
    return dict(sorted(pages.items()))


def raise_error(error):
    raise error


def read_restricted(root, faults):
    """(fault message, compiled pattern) for each line of restricted.txt; a pattern that does
    not compile is a fault at its line."""
    patterns, path = [], root / "restricted.txt"
    if not path.is_file() or path.is_symlink():
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


def check_page(root, rel, lines, patterns, faults, counts):
    """Restricted patterns on every line; links and aliases outside examples; the inbox format for
    a file in proposals/."""
    for number, line, example in lines:
        for message, pattern in SECRETS + patterns:
            if pattern.search(line):  # one restricted fault per line; the line needs fixing either way
                faults.append((rel, number, "restricted", message))
                break
        if example:
            continue
        for target in LINK.findall(line):
            path = link_path(root, rel, target)
            if path is not None and not (path.is_relative_to(root) and path.exists()):
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
    `- [kind] text (source: ...)`."""
    body = [(number, line) for number, line, example in lines if line.strip() and not example]
    if not body:
        return
    (number, header), items = body[0], body[1:]
    stem, words = PurePosixPath(rel).stem, header.lstrip("#").split()
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
        if not SOURCE.search(text):
            faults.append((rel, number, "proposal", 'item has no source (expected "(source: who, when, where)" at the end)'))
        elif not SOURCE.sub("", text).strip():
            faults.append((rel, number, "proposal", "item has a source but no text"))


def link_path(root, rel, target):
    """The absolute path a link in page `rel` names (anchor and query dropped, symlinks resolved),
    or None for an external link or a same-page anchor."""
    if SCHEME.match(target) or target.startswith(("#", "//")):
        return None
    path = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
    base = root if path.startswith("/") else (root / rel).parent
    return (base / path.lstrip("/")).resolve()


def check_departments(root, pages, faults):
    """Every page in departments/ is linked from a README table row. (A row whose page is missing
    is a dead link, which the link check reports.)"""
    linked = {link_path(root, "README.md", target)
              for _, line, example in pages.get("README.md") or [] if not example and line.lstrip().startswith("|")
              for target in LINK.findall(line)}
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
