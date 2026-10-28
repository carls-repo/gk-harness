"""PostToolUse hook for Edit|Write|MultiEdit|NotebookEdit: note a written spec as pending.

Standard library only, Python 3.9+. A spec, plan or report written under docs/superpowers/,
docs/reports/ or docs/plans/ of any git work tree is recorded in
state/gk-remote-pending/<session_id>.json, and the seat is told how to put it in front of
Carl. Never blocks. Always exits 0.
"""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Any

import gk_common

SPEC_RE = re.compile(r"docs/(superpowers|reports|plans)/.*\.(md|html)")


def toplevel(file_abs: str) -> str | None:
    """The git top level of the nearest existing folder above file_abs, or None."""
    d = os.path.dirname(file_abs)
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    top = gk_common.git_toplevel(d) if d else None
    return str(top) if top else None


def ledger_last(root: str, rel: str) -> tuple[str, str, str] | None:
    """Ledger state for rel -> (kind, url, date) or None.

    kind "link": the last line for the path whose url is a claude.ai link.
    kind "lost": no link line, but a doc was published (doc:unknown-url); url is "".
    A trailing doc:unknown-url (an update whose response carried no link) does not hide an
    earlier link: an update revises the same doc.
    """
    try:
        with open(os.path.join(root, *gk_common.REMOTE_LEDGER), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    lost = None
    for line in reversed(lines):
        parts = line.split("\t")
        if len(parts) < 2 or parts[0] != rel:
            continue
        date = parts[2] if len(parts) > 2 else ""
        if parts[1].startswith("https://claude.ai/"):
            return ("link", parts[1], date)
        if parts[1] == "doc:unknown-url" and lost is None:
            lost = ("lost", "", date)
    return lost


def handle(data: Any) -> None:
    if not isinstance(data, dict):
        return
    sid = gk_common.safe_sid(data.get("session_id"))
    ti = data.get("tool_input")
    if not sid or not isinstance(ti, dict):
        return
    p = ti.get("file_path") or ti.get("notebook_path")
    if not isinstance(p, str) or not p:
        return
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else os.getcwd()
    p = os.path.normpath(os.path.join(cwd, p))
    file_abs = os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))
    root = toplevel(file_abs)
    if not root:
        return
    rel = gk_common.rel_posix(file_abs, root)
    if rel is None:
        return
    base = rel.rsplit("/", 1)[-1]
    if not SPEC_RE.fullmatch(rel) or base.startswith("_") or base == "README.md":
        return
    kind = "html" if rel.endswith(".html") else "md"

    pfile = gk_common.pending_file(sid)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with gk_common.locked(pfile.parent):
        st = gk_common.load_pending(pfile, sid)
        for e in st["pending"]:
            if e.get("path") == rel and e.get("root") == root:
                e.update({"last_at": ts, "yielded": False, "kind": kind})
                break
        else:
            st["pending"].append({"path": rel, "root": root, "kind": kind,
                                  "first_at": ts, "last_at": ts, "yielded": False})
        if not gk_common.write_json(pfile, st):
            # No state, no Stop gate: say nothing rather than promise a gate that won't fire.
            sys.stderr.write("gk-remote-pending: cannot store %s\n" % pfile)
            return
        n_pending = len(st["pending"])

    channel = "a Claude Doc" if kind == "md" else 'SendUserFile with display "render"'
    prior = ledger_last(root, rel)
    if prior and prior[0] == "link":
        reading = ("A reading copy already exists at %s (published %s): revise that doc, do not create another."
                   % (prior[1], prior[2] or "date unknown"))
    elif prior:
        reading = ("A reading copy was published on %s but its link was not captured: find that doc "
                   "(Claude Docs query) and revise it, do not create another." % (prior[2] or "an unknown date"))
    else:
        reading = "No reading copy yet."
    msg = ("Spec written (gk-remote): %s. Before this turn ends, put it in front of Carl: %s. %s "
           "The Stop gate holds the turn until this is done; if Carl says not to publish, run: %s"
           % (rel, channel, reading, gk_common.skip_command(sid, rel)))
    if n_pending >= 2:
        msg += (" Note: %d specs pending; a Claude Docs call clears all of them, so publish one doc "
                "per spec and check the ledger lines." % n_pending)
    event = data.get("hook_event_name") if isinstance(data.get("hook_event_name"), str) else "PostToolUse"
    gk_common.emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": msg}})


if __name__ == "__main__":
    gk_common.run_hook("gk-remote-pending", handle)
