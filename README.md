# wiki-tools

`wiki_check.py` validates one company wiki (`kbf-wiki`, later `datum-wiki`) from the wiki's own files alone. A person or a CI job runs it on the wiki's root and gets every fault listed, or a clean exit. It needs Python 3 and nothing else: no packages, no network, no other repo, no home-folder settings. It runs on macOS, Linux, and Windows.

## Run it

```bash
python3 scripts/wiki_check.py .
```

Run it from a wiki's root, on `.`, before you push. The output is one of:

- **Clean:** one line, `ok: 5 files, 2 declarations, 2 aliases, 2 proposal lines`. Exit 0.
- **Faults:** one line per fault, `path:line: check: message`, sorted by path and line, then `<n> faults`. Exit 1. Every fault is listed, never only the first.
- **Usage error or unreadable root:** a message on standard error. Exit 2.

A restricted fault names the pattern that fired, never the text it matched. Other faults on that same line still print, with `"[withheld]"` where they would quote the line's text (a link target, a kind, a term). A pasted key never reaches a CI log.

## What it checks

| Check | A fault means |
|---|---|
| `link` | A relative Markdown link does not name a file or folder inside the wiki. Links are Markdown's single-line forms: inline `[text](path "title")` (an `<angle-bracketed>` path, or one with nested balanced parentheses), full, collapsed, and shortcut references (`[text][label]`, `[label][]`, `[label]`) whose label the page defines, and the definition `[label]: path` itself; with or without `#anchor`. External links are skipped. This also catches a README department row whose page is missing. |
| `departments` | A page in `departments/` is not linked from a row of the README's department table, or a row of that table links no page in `departments/`. The department table is the first table whose first header cell is `Department`; other tables, such as the workflow table, do not count. |
| `term` | A term is declared a second time. A declaration is `**Term**:` at the start of a line. Identity is the term lowercased with its spaces normalized, across every Markdown file; `.git` is skipped. The fault names the first declaration. |
| `alias` | An alias line has no target. An alias is `**Term** → owner/repo: path` (optional `#anchor` after the path, never instead of it; `->` also accepted), then a one-line gloss. Alias lines never count as declarations, so a word declared once and aliased to another repo's different sense is allowed. |
| `proposal` | A file in `proposals/` breaks the inbox format: the first non-blank line must be `# <ID>` with the ID equal to the file name (an empty file fails); every later line must be `- [kind] text (source: who, YYYY-MM-DD, where)` with a kind of `term`, `rule`, `skill`, or `fact`. The source is who, then the date, then where, each non-empty; it runs to the line's last `)`, so it may hold parentheses of its own. |
| `restricted` | A line in any Markdown file matches a built-in secret shape or a pattern in the wiki's `restricted.txt`. |
| `read` | A file is not UTF-8 text. |

Fenced code blocks and code spans (matching backtick runs) are examples. Only the `restricted` check reads them, because a secret in a code block is still a secret. A fence closes only on the same character, at least as long as it opened. Proposal files hold no examples: every line is checked.

**Built-in secret shapes**, always on: OpenAI keys, GitHub tokens, AWS access keys, private-key blocks, bearer tokens, password or key assignments (`password: ...`, `API_KEY=...`), and connection strings with a user and password. An assignment is `name=value`, or `name: value` where the value is quoted, holds a digit, or is the line's last word without sentence punctuation. So `password: correcthorsebatterystaple` is caught, and prose such as `Password: required.` or `Token: expiration is one hour.` passes. TypeSafe publishes no key prefix, so a TypeSafe key is caught by the assignment shape (`TYPESAFE_API_KEY=...`).

**`restricted.txt`** at the wiki root: one case-insensitive Python regular expression per line; blank lines and `#` comments are ignored. A pattern that does not compile is itself a fault at its line. The file is optional.

**Not checked:** a paraphrased duplicate of a meaning. Two differently worded declarations of the same idea pass; the daily sweep flags likely ones for a person.

## How a wiki vendors it

Each wiki carries its own copy at `scripts/wiki_check.py`, so a fresh clone validates alone. The copy is this repo's `wiki_check.py` at one commit, with one pin line added as line 2 (after the `#!` line):

```text
# vendored from fitziboy3-source/wiki-tools@<40-character SHA>; edit upstream, never here
```

That pin line is the single record of which version a wiki runs. To vendor or upgrade, from the wiki's root:

```bash
SHA=$(git -C ~/Projects/wiki-tools rev-parse origin/main)
mkdir -p scripts
git -C ~/Projects/wiki-tools show "$SHA:wiki_check.py" \
  | awk -v pin="# vendored from fitziboy3-source/wiki-tools@$SHA; edit upstream, never here" 'NR==2{print pin} {print}' \
  > scripts/wiki_check.py
```

Vendor only a SHA that is pushed to `main` here, because CI fetches that SHA from GitHub.

## How CI compares the two copies

The wiki's `wiki-check` workflow reads the SHA from the pin line, checks out this repo at that SHA beside the wiki (never inside it, or the fixture pages here would be checked as wiki pages), fails if the vendored copy minus its pin line differs from the file here, then runs this repo's copy on the wiki. This repo is public, so the checkout needs no credential. The job id and name `wiki-check` are the status-check context the wiki's ruleset requires.

```yaml
name: wiki-check
on:
  pull_request:
  push:
    branches: [main]
jobs:
  wiki-check:
    name: wiki-check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          path: wiki
      - name: Read the pinned SHA
        id: pin
        run: |
          sha=$(sed -n 's|^# vendored from fitziboy3-source/wiki-tools@\([0-9a-f]\{40\}\);.*|\1|p' wiki/scripts/wiki_check.py)
          [ -n "$sha" ] || { echo "wiki/scripts/wiki_check.py has no pin line"; exit 1; }
          echo "sha=$sha" >> "$GITHUB_OUTPUT"
      - uses: actions/checkout@v4
        with:
          repository: fitziboy3-source/wiki-tools
          ref: ${{ steps.pin.outputs.sha }}
          path: wiki-tools
      - name: The vendored copy matches wiki-tools at the pin
        run: |
          grep -v '^# vendored from fitziboy3-source/wiki-tools@' wiki/scripts/wiki_check.py \
            | cmp -s - wiki-tools/wiki_check.py \
            || { echo "wiki/scripts/wiki_check.py differs from wiki-tools at ${{ steps.pin.outputs.sha }}"; exit 1; }
      - name: Check the wiki
        run: python3 wiki-tools/wiki_check.py wiki
```

A green run on a bare runner is also the standalone proof: the runner has no other wiki, no personal harness, and no Skill Library.

## Tests

```bash
python3 wiki_check_test.py
```

Standard library only. The test runs the checker the way a person does and compares everything it prints with hand-written expectations:

- `fixtures/faults/` is a mini-wiki with nine seeded faults, one of each kind the spec names. Its `expected.txt` is the exact output, written by hand, never generated by the checker.
- `fixtures/clean/` is a mini-wiki that must pass. It holds the allowed case (the word "Review" declared once and aliased to ATLAS's different sense), a valid proposal, a declaration example inside a code fence, an alias written with `->`, and one page with Windows line endings. Its `expected.txt` is the one-line count.
- The clean fixture is also copied to a temporary folder with the checker at `scripts/wiki_check.py` and run on `.` under an empty home folder, with nothing else in its environment: the standalone-clone case.
- Edge cases: each built-in secret shape is caught without echoing the secret, a link outside the root fails, a symlinked page is never read, and usage errors exit 2.
- Review findings: the faults and valid forms Codex's cross-review found (reference links, parentheses in paths, inline code, the department table, empty proposals, source parts, anchor-only aliases, nested fences, a NUL-byte link, prose that names a password), and in its second round (nested parentheses, unequal backtick runs, a stray `](`, reference-style department rows, an empty author, an alphabetic password, other faults on a restricted line), each with a hand-written expectation.

To change a check, edit the fixture and its `expected.txt` by hand first, watch the test fail, then change `wiki_check.py`.
