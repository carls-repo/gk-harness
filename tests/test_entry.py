from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gkh  # noqa: E402


class EntryTests(unittest.TestCase):
    def test_version_format(self):
        self.assertRegex(gkh.VERSION, r"\d+\.\d+\.\d+")
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", gkh.VERSION))

    def test_install_py_runs_status(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "install.py"), "status"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX only")
    def test_install_sh_rejects_old_python(self):
        env = dict(os.environ, PYTHON="/bin/false")
        proc = subprocess.run(
            [os.path.join(ROOT, "install.sh"), "status"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Python 3.9 or newer is required", proc.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_install_ps1_status(self):
        import shutil
        shells = [s for s in ("powershell", "pwsh") if shutil.which(s)]
        self.assertTrue(shells, "no PowerShell found")
        for shell in shells:
            with self.subTest(shell=shell):
                proc = subprocess.run(
                    [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                     os.path.join(ROOT, "install.ps1"), "status"],
                    cwd=ROOT, capture_output=True, text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("status: not implemented", proc.stdout)


if __name__ == "__main__":
    unittest.main()
