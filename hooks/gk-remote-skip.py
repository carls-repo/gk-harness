"""Usage: gk-remote-skip.py <session_id> <path>

Standard library only, Python 3.9+. Removes a pending spec (repo-relative or absolute path)
from the publish gate, for when Carl says not to publish it. Exits 0 even when nothing matches.
"""
from __future__ import annotations

import os
import sys

import gk_common


def norm(p: str) -> str:
    p = os.path.normpath(os.path.abspath(p))
    return os.path.normcase(os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p)))


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
    t_abs = norm(target)
    with gk_common.locked(pfile.parent):
        st = gk_common.load_pending(pfile, sid)
        keep, removed = [], []
        for e in st["pending"]:
            root = e.get("root") if isinstance(e.get("root"), str) else None
            if e["path"] == target or (root and norm(os.path.join(root, e["path"])) == t_abs):
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
