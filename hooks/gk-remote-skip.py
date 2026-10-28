"""Usage: gk-remote-skip.py <session_id> <path>

Standard library only, Python 3.9+. Removes a pending spec (repo-relative or absolute path)
from the publish gate, for when Carl says not to publish it. Exits 0 even when nothing matches.
"""
from __future__ import annotations

import os
import sys

import gk_common


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        print("usage: gk-remote-skip.py <session_id> <path>")
        return
    sid, target = gk_common.safe_sid(argv[0]), argv[1]
    if not sid:
        print("invalid session id: %s" % argv[0])
        return
    pfile = gk_common.pending_file(sid)
    if not pfile.exists():
        print("No pending specs for session %s." % sid)
        return
    t_abs = gk_common.canon_path(target, os.getcwd())
    with gk_common.locked(pfile.parent):
        raw = gk_common.read_json(pfile)
        if not (isinstance(raw, dict) and isinstance(raw.get("pending"), list)):
            # missing (removed since the check above) or malformed: today's wording
            print("No pending specs for session %s." % sid)
            return
        st = gk_common.load_pending(pfile, sid)
        keep, removed = [], []
        for e in st["pending"]:
            root = e.get("root") if isinstance(e.get("root"), str) else None
            if e["path"] == target or (root and gk_common.canon_path(e["path"], root) == t_abs):
                removed.append(e)
            else:
                keep.append(e)
        if not removed:
            print("No pending entry matches %s." % target)
            return
        st["pending"] = keep
        if not gk_common.write_json(pfile, st):
            print("Could not store %s; nothing was removed." % pfile)
            return
    for e in removed:
        print("Removed pending spec: %s" % e["path"])


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as e:
        try:
            sys.stderr.write("gk-remote-skip: %r\n" % (e,))
        except Exception:
            pass
    sys.exit(0)
