"""SessionEnd hook: delete this session's per-session state.

Standard library only, Python 3.9+. Removes state/<sub>/<session_id>.json for its claims,
peer snapshot, remote-pending list and seat record. Silent; always exits 0.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import gk_common


def handle(data: Any) -> None:
    if not isinstance(data, dict):
        return
    sid = gk_common.safe_sid(data.get("session_id"))
    if not sid:
        return
    base = gk_common.claude_dir()
    for sub in gk_common.SESSION_STATE_DIRS:
        try:
            os.unlink(base / "state" / sub / (sid + ".json"))
        except FileNotFoundError:
            pass
        except Exception as e:
            sys.stderr.write("gk-claims-end: %r\n" % (e,))


if __name__ == "__main__":
    gk_common.run_hook("gk-claims-end", handle)
