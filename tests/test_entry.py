from __future__ import annotations

import os
import re
import shutil
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
        false = shutil.which("false")
        if false is None:
            self.skipTest("no 'false' executable on PATH")
        env = dict(os.environ, PYTHON=false)
        proc = subprocess.run(
            [os.path.join(ROOT, "install.sh"), "status"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Python 3.9 or newer is required", proc.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX only")
    def test_install_sh_execs_install_py_with_args(self):
        env = dict(os.environ, PYTHON=sys.executable)
        proc = subprocess.run(
            [os.path.join(ROOT, "install.sh"), "status"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("status: not implemented", proc.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX only")
    def test_install_sh_survives_exported_cdpath(self):
        # CDPATH only applies to relative cd targets, so run the script by a
        # relative path whose directory name also exists as a decoy under CDPATH.
        import tempfile
        name = os.path.basename(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            decoy = os.path.join(tmp, name)
            os.makedirs(decoy)
            with open(os.path.join(decoy, "install.py"), "w") as fh:
                fh.write("import sys\nprint('decoy install.py')\n")
            env = dict(os.environ, PYTHON=sys.executable, CDPATH=tmp)
            proc = subprocess.run(
                ["sh", os.path.join(name, "install.sh"), "status"],
                cwd=os.path.dirname(ROOT), env=env, capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("status: not implemented", proc.stdout)
        self.assertNotIn("decoy", proc.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_install_ps1_status(self):
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
