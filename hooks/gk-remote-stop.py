"""Stop hook: hold the turn while specs written this turn are unpublished.

Standard library only, Python 3.9+. Blocks at most once per turn: when stop_hook_active is
true it flags the entries yielded and exits silently. Always exits 0.
"""
from __future__ import annotations

import sys
from typing import Any

import gk_common


def handle(data: Any) -> None:
    if not isinstance(data, dict):
        return
    sid = gk_common.safe_sid(data.get("session_id"))
    if not sid:
        return
    pfile = gk_common.pending_file(sid)
    if not pfile.exists():
        return
    active = data.get("stop_hook_active") is True
    with gk_common.locked(pfile.parent):
        st = gk_common.load_pending(pfile, sid)
        counting = [e for e in st["pending"] if e.get("yielded") is not True]
        if not counting:
            return
        if active:
            for e in counting:
                e["yielded"] = True
            if not gk_common.write_json(pfile, st):
                sys.stderr.write("gk-remote-stop: cannot store %s\n" % pfile)
            return
    lines = ["Spec(s) written this turn have not been put in front of Carl (gk-remote):"]
    for e in counting:
        channel = ("Claude Doc (Markdown)" if e.get("kind") != "html"
                   else 'SendUserFile with display "render" (HTML)')
        lines.append("- %s: %s. If Carl says not to publish: %s"
                     % (e["path"], channel, gk_common.skip_command(sid, e["path"])))
    lines.append("Publish now, then end the turn again. If you genuinely cannot (connector down), "
                 "end the turn once more and say so.")
    gk_common.emit({"decision": "block", "reason": "\n".join(lines)})


if __name__ == "__main__":
    gk_common.run_hook("gk-remote-stop", handle)
