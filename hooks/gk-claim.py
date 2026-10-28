"""PostToolUse hook for Edit|Write|MultiEdit|NotebookEdit: record the edit as a claim.

Standard library only, Python 3.9+. Records this session's claim on (git common dir,
repo-relative path) in state/gk-claims/<session_id>.json and warns about live peers that
have edited the same file. Warns, never blocks. Always exits 0.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

import gk_common

SKIP_SEGMENTS = ("node_modules", ".next", ".git")
OVERLAP = ("Overlap (gk-claim): %s was edited by session \"%s\" on branch %s at %s. "
           "Message that session (SendMessage) in this turn if your change may conflict; "
           "agree who merges to %s first.")


def _inside(path: str, root: str) -> bool:
    return (os.path.normcase(path) == os.path.normcase(root)
            or gk_common.rel_posix(path, root) is not None)


def handle(data: Any) -> None:
    if not isinstance(data, dict):
        return
    sid = gk_common.safe_sid(data.get("session_id"))
    ti = data.get("tool_input")
    if not sid or not isinstance(ti, dict):
        return
    p = ti.get("file_path") if ti.get("file_path") else ti.get("notebook_path")
    if not isinstance(p, str) or not p:
        return
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else os.getcwd()
    base = gk_common.claude_dir()
    full = os.path.realpath(os.path.join(cwd, os.path.expanduser(p)))
    if _inside(full, os.path.realpath(str(base))):
        return
    d = os.path.dirname(full)
    while d and not os.path.isdir(d) and d != os.path.dirname(d):
        d = os.path.dirname(d)
    if not os.path.isdir(d):
        return
    ident = gk_common.git_identity(d)
    if not ident:
        return
    common, top, branch = ident
    rel = gk_common.rel_posix(full, top)
    if rel is None or any(seg in SKIP_SEGMENTS for seg in rel.split("/")):
        return

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cpath = base / "state" / "gk-claims" / (sid + ".json")
    doc = gk_common.read_json(cpath)
    claims = doc.get("claims") if isinstance(doc, dict) and isinstance(doc.get("claims"), list) else []
    claims = [c for c in claims if isinstance(c, dict)]
    for c in claims:
        if c.get("common") == common and c.get("path") == rel:
            c.update({"branch": branch, "root": top, "at": now})
            break
    else:
        claims.append({"common": common, "root": top, "branch": branch, "path": rel, "at": now})
    if not gk_common.write_json(cpath, {"session_id": sid, "updated_at": now, "claims": claims}):
        sys.stderr.write("gk-claim: cannot store claims %s\n" % cpath)

    # overlap scan: live peers only
    live = {}
    for f in sorted((base / "sessions").glob("*.json")):
        r = gk_common.read_json(f)
        if (isinstance(r, dict) and gk_common.safe_sid(r.get("sessionId"))
                and gk_common.pid_alive(r.get("pid")) and r["sessionId"] != sid):
            name = r.get("name") if isinstance(r.get("name"), str) and r.get("name") else r["sessionId"][:8]
            live.setdefault(r["sessionId"], name)
    hits = []
    for peer, name in live.items():
        pd = gk_common.read_json(base / "state" / "gk-claims" / (peer + ".json"))
        peer_claims = pd.get("claims") if isinstance(pd, dict) else None
        for c in peer_claims if isinstance(peer_claims, list) else []:
            if isinstance(c, dict) and c.get("common") == common and c.get("path") == rel:
                hits.append((name, c.get("branch", "?"), c.get("at", "?")))
                break
    if hits:
        phrase = gk_common.shared_branch_phrase(top)
        lines = [OVERLAP % (rel, name, b, at, phrase) for name, b, at in hits]
        gk_common.emit({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                               "additionalContext": "\n".join(lines)}})


if __name__ == "__main__":
    gk_common.run_hook("gk-claim", handle)
