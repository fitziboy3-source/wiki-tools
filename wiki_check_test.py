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
    def wiki(self, tmp, files):
        root = os.path.join(tmp, "wiki")
        for rel, text in files.items():
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        return root

    def test_every_built_in_secret_shape_is_caught_and_never_echoed(self):
        # Assembled at run time so this public file never holds a literal key shape.
        secrets = ["sk-" + "a" * 24, "ghp_" + "A" * 36, "AKIA" + "ABCDEFGHIJKLMNOP",
                   "-----BEGIN " + "RSA PRIVATE KEY-----", "Authorization: Bearer " + "x" * 20,
                   "TYPESAFE_API_KEY=" + "q" * 24, "postgres://" + "admin:hunter22@db.example.com/jobs"]
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(self.wiki(tmp, {"notes.md": "".join(f"{s}\n" for s in secrets)}))
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
            root = self.wiki(tmp, {"README.md": "[outside](../outside.md)\n"})
            with open(os.path.join(tmp, "outside.md"), "w", encoding="utf-8") as f:
                f.write("password: " + "hunter2" * 3 + "\n**Review**:\n")
            try:
                os.symlink(os.path.join(tmp, "outside.md"), os.path.join(root, "linked.md"))
            except OSError:
                pass  # Windows without the symlink privilege: the link case still runs
            code, out, _ = run(root)
        self.assertEqual(out, 'README.md:1: link: "../outside.md" does not resolve to a file in the wiki\n1 fault\n')
        self.assertEqual(code, 1)


class Usage(unittest.TestCase):
    def test_no_root_two_roots_a_missing_root_and_a_file_all_exit_2(self):
        for args in ((), ("a", "b"), (os.path.join(FIXTURES, "no-such-wiki"),), (CHECKER,)):
            code, out, err = run(*args)
            self.assertEqual(code, 2, args)
            self.assertEqual(out, "", args)
            self.assertTrue(err, args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
