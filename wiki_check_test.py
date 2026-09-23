#!/usr/bin/env python3
"""Tests for `wiki_check.py` at its one seam: run it on a fixture wiki the way a person or CI
runs it, and compare its exit code and everything it prints with the fixture's hand-written
`expected.txt`. Standard library only. Run: python3 wiki_check_test.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(HERE, "wiki_check.py")
FIXTURES = os.path.join(HERE, "fixtures")


def run(*args, checker=CHECKER, cwd=None, env=None):
    done = subprocess.run([sys.executable, "-I", checker, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8")
    return done.returncode, done.stdout, done.stderr


def make_wiki(tmp, files):
    """A wiki folder at tmp/wiki holding `files` (relative path -> text); returns its path."""
    root = os.path.join(tmp, "wiki")
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return root


def expected(name):
    with open(os.path.join(FIXTURES, name, "expected.txt"), encoding="utf-8") as f:
        return f.read()


class Fixtures(unittest.TestCase):
    def test_the_faults_wiki_reports_exactly_the_nine_seeded_faults(self):
        want = expected("faults")
        self.assertEqual(len(want.splitlines()), 10, "expected.txt must hold nine faults and the summary")
        code, out, _ = run(os.path.join(FIXTURES, "faults"))
        self.assertEqual(out, want)
        self.assertEqual(code, 1)

    def test_the_clean_wiki_passes_with_the_one_line_count(self):
        with open(os.path.join(FIXTURES, "clean", "departments", "design.md"), "rb") as f:
            self.assertIn(b"\r\n", f.read(), "the clean fixture must keep one page with Windows line endings")
        code, out, _ = run(os.path.join(FIXTURES, "clean"))
        self.assertEqual(out, expected("clean"))
        self.assertEqual(code, 0)

    def test_a_fresh_copy_validates_alone_under_an_empty_home(self):
        # The standalone-clone case: the wiki copied somewhere new with its vendored checker,
        # an empty home, no environment, and the README's own instruction (run it on ".").
        with tempfile.TemporaryDirectory() as tmp:
            wiki, home = os.path.join(tmp, "wiki"), os.path.join(tmp, "home")
            shutil.copytree(os.path.join(FIXTURES, "clean"), wiki)
            os.makedirs(os.path.join(wiki, "scripts"))
            os.makedirs(home)
            shutil.copy(CHECKER, os.path.join(wiki, "scripts", "wiki_check.py"))
            env = {"HOME": home, "USERPROFILE": home}
            if "SYSTEMROOT" in os.environ:
                env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
            code, out, _ = run(".", checker=os.path.join("scripts", "wiki_check.py"), cwd=wiki, env=env)
            self.assertEqual(out, expected("clean"))
            self.assertEqual(code, 0)
            self.assertEqual(os.listdir(home), [])


class Edges(unittest.TestCase):
    def test_every_built_in_secret_shape_is_caught_and_never_echoed(self):
        # Assembled at run time so this public file never holds a literal key shape.
        secrets = ["sk-" + "a" * 24, "ghp_" + "A" * 36, "AKIA" + "ABCDEFGHIJKLMNOP",
                   "-----BEGIN " + "RSA PRIVATE KEY-----", "Authorization: Bearer " + "x" * 20,
                   "TYPESAFE_API_KEY=" + "q" * 24, "postgres://" + "admin:hunter22@db.example.com/jobs"]
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(make_wiki(tmp, {"notes.md": "".join(f"{s}\n" for s in secrets)}))
        self.assertEqual(out, "notes.md:1: restricted: looks like an OpenAI key\n"
                              "notes.md:2: restricted: looks like a GitHub token\n"
                              "notes.md:3: restricted: looks like an AWS access key\n"
                              "notes.md:4: restricted: looks like a private key block\n"
                              "notes.md:5: restricted: looks like a bearer token\n"
                              "notes.md:6: restricted: looks like a password or key assignment\n"
                              "notes.md:7: restricted: looks like a connection string with credentials\n"
                              "7 faults\n")
        self.assertEqual(code, 1)

    def test_nothing_outside_the_root_is_read_or_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_wiki(tmp, {"README.md": "[outside](../outside.md)\n"})
            with open(os.path.join(tmp, "outside.md"), "w", encoding="utf-8") as f:
                f.write("password: " + "hunter2" * 3 + "\n**Review**:\n")
            try:
                os.symlink(os.path.join(tmp, "outside.md"), os.path.join(root, "linked.md"))
            except OSError:
                pass  # Windows without the symlink privilege: the link case still runs
            code, out, _ = run(root)
        self.assertEqual(out, 'README.md:1: link: "../outside.md" does not resolve to a file in the wiki\n1 fault\n')
        self.assertEqual(code, 1)


class ReviewFindings(unittest.TestCase):
    """The cases Codex's cross-review of 26996b4 found, each with a hand-written expectation."""

    def test_the_faults_found_in_review_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_wiki(tmp, {
                "README.md": "| Phase | Page |\n|---|---|\n| Sales | [sales](departments/sales.md) |\n\n"
                             "| Department | Page |\n|---|---|\n| Design | none yet |\n"
                             "| Finance | [finance](departments/finance.md) |\n\n"
                             "[policy]: missing.md\n[bad](%00.md) is checked and the run goes on.\n"
                             "[secret link](missing.md?token=abc123def456ghi)\n",
                "CONTEXT.md": "**Review** → owner/repo: #review\nAn anchor alone is not a path.\n",
                "departments/sales.md": "# Sales\n",
                "departments/finance.md": "# Finance\n",
                "proposals/01M34QM0000000000000000EMP.md": "",
                "proposals/01M34QM0000000000000000SRC.md": "# 01M34QM0000000000000000SRC\n- [rule] Something. (source: A)\n",
            })
            code, out, _ = run(root)
        self.assertEqual(out, 'CONTEXT.md:1: alias: no target (expected "**Term** → owner/repo: path")\n'
                              'README.md:7: departments: row "Design" links no page in departments/\n'
                              'README.md:10: link: "missing.md" does not resolve to a file in the wiki\n'
                              'README.md:11: link: "%00.md" does not resolve to a file in the wiki\n'
                              'README.md:12: link: "[withheld]" does not resolve to a file in the wiki\n'
                              'README.md:12: restricted: looks like a password or key assignment\n'
                              'departments/sales.md:1: departments: page is not linked from the README department table\n'
                              'proposals/01M34QM0000000000000000EMP.md:1: proposal: first line must be the header "# 01M34QM0000000000000000EMP"\n'
                              'proposals/01M34QM0000000000000000SRC.md:2: proposal: source must name who, the date (YYYY-MM-DD), and where\n'
                              '9 faults\n')
        self.assertEqual(code, 1)

    def test_the_valid_forms_found_in_review_pass(self):
        fence = "````markdown\n```\n**Sales**:\n```\n````\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = make_wiki(tmp, {
                "README.md": "| Department | Page |\n|---|---|\n| Sales | [sales](departments/sales.md) |\n\n"
                             "See [the policy](<Leave policy (US).md>), [leave](Leave_(US).md), "
                             "and `[not a link](nowhere.md)`.\n\n[policy]: Leave_(US).md\n",
                "Leave policy (US).md": "# Leave\n",
                "Leave_(US).md": "# Leave\n",
                "departments/sales.md": "**Sales**:\nDeclared once.\n\nPassword: requirements are set by the owner.\n"
                                        "Token: expiration is one hour.\n\n" + fence,
                "proposals/01M34QM0000000000000000NST.md": "# 01M34QM0000000000000000NST\n\n"
                    "- [rule] Leave requests go to the owner. (source: Ashley Sheaffer, 2026-09-22, Meeting (notes))\n",
            })
            code, out, _ = run(root)
        self.assertEqual(out, "ok: 5 files, 1 declarations, 0 aliases, 1 proposal lines\n")
        self.assertEqual(code, 0)


class ReviewRound2(unittest.TestCase):
    """The cases Codex's second review (of bbc8845) found, each with a hand-written expectation."""

    def test_the_faults_found_in_round_2_are_reported_without_echoing_a_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_wiki(tmp, {
                "README.md": "| Department | Page |\n|---|---|\n| Sales | [sales][sales-page] |\n"
                             "password=supersecret123\n| Phase | Page |\n|---|---|\n| Sales | [sales][sales-page] |\n\n"
                             "[sales-page]: departments/sales.md\n[nested](docs/Leave_(US_(staff)).md)\n"
                             "`` [missing](missing.md) `\n[missing link with a secret](missing.md?token=abc123def456ghi)\n",
                "CONTEXT.md": "password: correcthorsebatterystaple\n",
                "departments/sales.md": "# Sales\n",
                "proposals/01M34QM0000000000000000R2A.md": "# 01M34QM0000000000000000R2A\n"
                    "- [unknown] password=supersecret123\n- [rule] No author. (source: , 2026-09-22, Meeting, agenda)\n",
            })
            code, out, _ = run(root)
        self.assertEqual(out, 'CONTEXT.md:1: restricted: looks like a password or key assignment\n'
                              'README.md:4: restricted: looks like a password or key assignment\n'
                              'README.md:10: link: "docs/Leave_(US_(staff)).md" does not resolve to a file in the wiki\n'
                              'README.md:11: link: "missing.md" does not resolve to a file in the wiki\n'
                              'README.md:12: link: "[withheld]" does not resolve to a file in the wiki\n'
                              'README.md:12: restricted: looks like a password or key assignment\n'
                              'proposals/01M34QM0000000000000000R2A.md:2: proposal: unknown kind "[withheld]" (expected term, rule, skill, or fact)\n'
                              'proposals/01M34QM0000000000000000R2A.md:2: proposal: item has no source (expected "(source: who, when, where)" at the end)\n'
                              'proposals/01M34QM0000000000000000R2A.md:2: restricted: looks like a password or key assignment\n'
                              'proposals/01M34QM0000000000000000R2A.md:3: proposal: source must name who, the date (YYYY-MM-DD), and where\n'
                              '10 faults\n')
        self.assertNotIn("supersecret", out)
        self.assertNotIn("abc123", out)
        self.assertEqual(code, 1)

    def test_the_valid_forms_found_in_round_2_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_wiki(tmp, {
                "README.md": "| Department | Page |\n|---|---|\n| Sales | [sales][] |\n\n[sales]: departments/sales.md\n"
                             "See [the leave page](docs/Leave_(US_(staff)).md), plain text](missing.md), "
                             "[a bracket] with no link, and [![logo](docs/logo.md)](departments/sales.md).\n"
                             "Password: required.\nToken: expiration is one hour.\n",
                "departments/sales.md": "# Sales\n",
                "docs/Leave_(US_(staff)).md": "# Leave\n",
                "docs/logo.md": "# Logo\n",
            })
            code, out, _ = run(root)
        self.assertEqual(out, "ok: 4 files, 0 declarations, 0 aliases, 0 proposal lines\n")
        self.assertEqual(code, 0)


class ReviewRound3(unittest.TestCase):
    """The cases Codex's third review (of 7e26b2b) found, each with a hand-written expectation."""

    def test_the_faults_found_in_round_3_are_reported_without_echoing_a_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_wiki(tmp, {
                "README.md": "[download][private]\n\n[private]: missing.md?token=syntheticsecret12345\n"
                             "[Press `]`](missing.md)\n[Policy](missing.md\t\"Leave policy\")\n"
                             "password: correcthorsebatterystaple # production\n**Password**: hunter2hunter2\n",
                "proposals/01M34QM0000000000000000R3A.md": "# 01M34QM0000000000000000R3A\n"
                    "- [fact] The password: correcthorsebatterystaple (source: Ashley, 2026-09-22, meeting)\n"
                    "- [rule] Two empty author fields. (source: , , 2026-09-22, Meeting)\n"
                    "- [rule] Empty where. (source: Ashley, 2026-09-22, , )\n",
            })
            code, out, _ = run(root)
        self.assertEqual(out, 'README.md:1: link: "[withheld]" does not resolve to a file in the wiki\n'
                              'README.md:3: link: "[withheld]" does not resolve to a file in the wiki\n'
                              'README.md:3: restricted: looks like a password or key assignment\n'
                              'README.md:4: link: "missing.md" does not resolve to a file in the wiki\n'
                              'README.md:5: link: "missing.md" does not resolve to a file in the wiki\n'
                              'README.md:6: restricted: looks like a password or key assignment\n'
                              'README.md:7: restricted: looks like a password or key assignment\n'
                              'proposals/01M34QM0000000000000000R3A.md:2: restricted: looks like a password or key assignment\n'
                              'proposals/01M34QM0000000000000000R3A.md:3: proposal: source must name who, the date (YYYY-MM-DD), and where\n'
                              'proposals/01M34QM0000000000000000R3A.md:4: proposal: source must name who, the date (YYYY-MM-DD), and where\n'
                              '10 faults\n')
        for secret in ("syntheticsecret", "correcthorse", "hunter2"):
            self.assertNotIn(secret, out)
        self.assertEqual(code, 1)

    def test_the_valid_forms_found_in_round_3_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_wiki(tmp, {
                "README.md": "[Policy](Leave.md\t\"Leave policy\") and [Press `]`](Leave.md).\n"
                             "Password: Required for all staff.\nThe **Secret**: sauce is butter.\n",
                "Leave.md": "# Leave\n",
            })
            code, out, _ = run(root)
        self.assertEqual(out, "ok: 2 files, 0 declarations, 0 aliases, 0 proposal lines\n")
        self.assertEqual(code, 0)


class Usage(unittest.TestCase):
    def test_no_root_two_roots_a_missing_root_and_a_file_all_exit_2(self):
        for args in ((), ("a", "b"), (os.path.join(FIXTURES, "no-such-wiki"),), (CHECKER,)):
            code, out, err = run(*args)
            self.assertEqual(code, 2, args)
            self.assertEqual(out, "", args)
            self.assertTrue(err, args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
