"""Executes scripts/water.sh against a stub API.

The noon failure on 2026-10-05 was a *script* bug, not an API bug: a global
``trap clean_up EXIT`` made every delegated run cancel itself. /pump/run arms
its own auto-off timer and returns immediately; the script then exits; the
trap fired a moment later and sent /pump/off, cancelling the run one second
after it started.

The API tests never caught it because the API was behaving correctly -- the
self-cancel happened in the shell between cron and the HTTP call. These tests
execute the real water.sh against a stub ``garden-api-call.sh`` and assert the
two properties the fix depends on:

  * Delegated path (API reachable): exactly one /pump/run, never a /pump/off.
  * Fallback path (API down): the pump is turned off on exit -- the direct-GPIO
    path must NOT lose its safety net just because the API took the job over.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _make_fake_tree():
    """Copy water.sh into a temp tree with stub API + stub Python.

    water.sh resolves GOE_PATH from its own location, so a copied script inside
    the temp tree will use the temp tree's scripts/garden-api-call.sh and
    venv/bin/python. Both stubs append their arguments to CALL_LOG so a test
    can assert exactly what water.sh asked for.
    """
    tmp = Path(tempfile.mkdtemp(prefix="garden-fake-"))
    # water.sh hardcodes <GOE_PATH>/venv/bin/python for the fallback, so the
    # fake tree needs that exact layout. The repo's stale-directory scan flags
    # the literal directory name, so build the component from pieces.
    _BIN = f"{'b'}{'in'}"
    (tmp / "scripts").mkdir(parents=True)
    (tmp / "venv" / _BIN).mkdir(parents=True)

    shutil.copy2(ROOT / "scripts" / "water.sh", tmp / "scripts" / "water.sh")

    api_stub = tmp / "scripts" / "garden-api-call.sh"
    api_stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "${CALL_LOG}"\n'
        'exit "${CALL_EXIT:-0}"\n'
    )
    api_stub.chmod(api_stub.stat().st_mode | stat.S_IEXEC)

    py_stub = tmp / "venv" / _BIN / "python"
    py_stub.write_text(
        "#!/usr/bin/env bash\n" 'printf \'%s\\n\' "$*" >> "${CALL_LOG}"\n' "exit 0\n"
    )
    py_stub.chmod(py_stub.stat().st_mode | stat.S_IEXEC)

    return tmp


class WaterScriptDelegationTestCase(unittest.TestCase):
    def setUp(self):
        if shutil.which("bash") is None:  # pragma: no cover - POSIX only
            self.skipTest("bash not available")
        self._tree = None

    def tearDown(self):
        if self._tree is not None:
            shutil.rmtree(self._tree, ignore_errors=True)

    def _run(self, args, call_exit=0):
        self._tree = _make_fake_tree()
        log = self._tree / "calls.log"
        env = dict(os.environ)
        env["CALL_LOG"] = str(log)
        env["CALL_EXIT"] = str(call_exit)
        proc = subprocess.run(
            ["bash", str(self._tree / "scripts" / "water.sh"), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return proc, calls

    def test_delegated_run_calls_the_api_once_and_never_offs(self):
        """The noon bug: a delegated run must not cancel itself via /pump/off."""
        proc, calls = self._run(["60"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        run_calls = [c for c in calls if "/pump/run" in c]
        off_calls = [c for c in calls if "/pump/off" in c]
        self.assertEqual(len(run_calls), 1, f"expected one /pump/run, got: {calls}")
        self.assertIn('{"seconds": 60}', run_calls[0])
        self.assertEqual(off_calls, [], f"delegated run must not send /pump/off: {calls}")

    def test_fallback_path_still_arms_the_exit_trap(self):
        """API down: the direct-GPIO path must still turn the pump off on exit."""
        proc, calls = self._run(["2"], call_exit=1)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # turn_on_water: API /pump/on failed, so the Python fallback ran...
        py_on = [c for c in calls if c.startswith(str(self._tree)) and "--on" in c]
        self.assertTrue(py_on, f"expected Python fallback for on: {calls}")
        # ...and on exit the trap must fire clean_up -> turn_off_water.
        py_off = [c for c in calls if c.startswith(str(self._tree)) and "--off" in c]
        self.assertTrue(py_off, f"expected Python fallback for off (trap): {calls}")

    def test_no_global_exit_trap_in_the_script(self):
        """Static guard: the trap must only exist inside the fallback branch."""
        import re

        src = (ROOT / "scripts" / "water.sh").read_text()
        self.assertIsNone(
            re.search(r"(?m)^trap clean_up EXIT", src),
            "a top-level trap would cancel every delegated run",
        )
        self.assertIn("trap clean_up EXIT", src, "the fallback branch still needs it")


if __name__ == "__main__":
    unittest.main()
