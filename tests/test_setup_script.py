"""Static checks for scripts/setup.sh.

The install script only ever runs on a fresh Pi, so a syntax error or a dropped
verification step would otherwise surface at install time and nowhere else.
These are content and parse assertions rather than behaviour tests -- running
setup.sh for real would install packages and write to /etc.
"""

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

SETUP = Path(__file__).resolve().parent.parent / "scripts" / "setup.sh"


class SetupScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.src = SETUP.read_text()

    def test_parses(self):
        """bash -n catches a malformed script without executing it."""
        if shutil.which("bash") is None:  # pragma: no cover - POSIX only
            self.skipTest("bash not available")
        proc = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_verification_is_defined_and_called(self):
        # Inline (?m): assertRegex's third positional arg is the failure
        # message, not compile flags, so re.M there would be silently ignored.
        self.assertRegex(self.src, r"(?m)^function verify_install \{")
        # It has to run at the end of the install, not merely exist.
        self.assertRegex(self.src, r"(?m)^verify_install$")

    def test_verification_covers_the_schedule_commands(self):
        body = re.search(r"^function verify_install \{.*?^\}", self.src, re.S | re.M).group(0)
        for cmd in ("/usr/local/bin/light", "/usr/local/bin/water"):
            self.assertIn(cmd, body, f"verification does not check {cmd}")
        # Without these the scheduled jobs fail silently, so it must say so.
        self.assertIn("schedule will NOT run", body)

    def test_verification_fails_loudly(self):
        body = re.search(r"^function verify_install \{.*?^\}", self.src, re.S | re.M).group(0)
        self.assertIn("return 1", body)
        self.assertIn("return 0", body)

    def test_plan_mentions_the_verification_step(self):
        plan = re.search(r"=== Garden of Eden setup.*?====", self.src, re.S).group(0)
        self.assertIn("Verify", plan)

    def test_no_stale_bin_path_references(self):
        """bin/ became scripts/; a stale reference silently breaks installs.

        The docs and agent instructions hardcode these paths, and nothing in
        the suite would notice one drifting, so assert it here. Genuine `bin`
        paths -- the venv and /usr/local/bin, where setup.sh installs the
        symlinks -- are not affected and must stay.
        """
        root = SETUP.parent.parent
        skip_dirs = {"venv", ".git", "__pycache__", "timelapse", "node_modules"}
        this_file = Path(__file__).resolve()
        offenders = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".venv")]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix not in (".sh", ".md", ".py", ".yml", ".html"):
                    continue
                if path.resolve() == this_file:
                    continue  # this file names bin/ on purpose, in prose
                for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                    scrubbed = re.sub(r"(\S*/)?venv/bin/", "", line)
                    scrubbed = re.sub(r"/usr/local/bin/", "", scrubbed)
                    if re.search(r"(?<![\w/])bin/", scrubbed):
                        rel = path.relative_to(root)
                        offenders.append(f"{rel}:{n}: {line.strip()[:70]}")
        self.assertEqual(offenders, [], "stale bin/ reference(s):\n" + "\n".join(offenders))

    def test_scripts_dir_exists_and_bin_dir_does_not(self):
        root = SETUP.parent.parent
        self.assertTrue((root / "scripts" / "setup.sh").exists())
        self.assertFalse((root / "bin").exists(), "bin/ should have been renamed to scripts/")

    def test_no_stale_context_documentation_reference(self):
        """context_documentation/ was merged into docs/.

        The agent instructions and README hardcode these paths, so a stale
        reference leaves future sessions reading files that no longer exist.
        """
        root = SETUP.parent.parent
        offenders = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in {"venv", ".git", "__pycache__", "timelapse"}
                and not d.startswith(".venv")
            ]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix not in (".sh", ".md", ".py", ".yml", ".html"):
                    continue
                if path.resolve() == Path(__file__).resolve():
                    continue
                for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                    if "context_documentation" in line:
                        rel = path.relative_to(root)
                        offenders.append(f"{rel}:{n}")
        self.assertEqual(
            offenders, [], "stale context_documentation reference(s):\n" + "\n".join(offenders)
        )

    def test_docs_relative_links_resolve(self):
        """Every relative link in docs/ must point at a file that exists."""
        docs = SETUP.parent.parent / "docs"
        broken = []
        for md in sorted(docs.rglob("*.md")):
            body = md.read_text(errors="ignore")
            for _, target in re.findall(r"\[([^\]]+)\]\(([^)#]+)\)", body):
                if target.startswith(("http", "mailto")):
                    continue
                if not (md.parent / target).exists():
                    broken.append(f"{md.relative_to(docs)}: {target}")
        self.assertEqual(broken, [], "broken link(s) in docs/:\n" + "\n".join(broken))


if __name__ == "__main__":
    unittest.main()
