from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from gkh import cli, gen  # noqa: E402


class GenTests(unittest.TestCase):
    def test_generated_agents_equal_committed(self):
        self.assertEqual(gen.stale(ROOT), [])

    def test_stale_lists_a_hand_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(str(ROOT / "routes.json"), str(root / "routes.json"))
            shutil.copytree(str(ROOT / "agents"), str(root / "agents"))
            with open(str(root / "agents" / "worker-opus.md"), "a", encoding="utf-8", newline="") as f:
                f.write("hand edit\n")
            self.assertEqual(gen.stale(root), ["agents/worker-opus.md"])
            self.assertEqual(gen.write(root), ["agents/worker-opus.md"])
            self.assertEqual(gen.stale(root), [])
            self.assertEqual(gen.write(root), [])

    def test_gen_check_exit_code(self):
        self.assertEqual(cli.main(["gen", "--check"]), 0)


if __name__ == "__main__":
    unittest.main()
