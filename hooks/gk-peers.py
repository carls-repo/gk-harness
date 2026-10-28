"""SessionStart + UserPromptSubmit hook: brief the session on live peer sessions.

Standard library only, Python 3.9+. Reads <claude_dir>/sessions/*.json (read-only), prunes
the state of dead sessions, and keeps a per-session snapshot in
state/gk-peers/<session_id>.json so UserPromptSubmit reports only what changed. Never exits
non-zero.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import gk_common

POLICY = ("Policy: message a peer (SendMessage) before editing a file it has claimed, when a hook "
          "warns of an overlap, and before merging to %s.")
MAX_FILES = 15
# The SessionStart briefing's size limit: it shares the 10,000-character SessionStart budget
# with the seat card, so peers past this are summarised in one line.
BRIEFING_MAX = 8000


def load_registry(base: Path) -> list[dict]:
    out = []
    for f in sorted((base / "sessions").glob("*.json")):
        d = gk_common.read_json(f)
        if isinstance(d, dict) and gk_common.safe_sid(d.get("sessionId")) and _is_int(d.get("pid")):
            out.append(d)
    return out


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _started(d: dict) -> float:
    v = d.get("startedAt")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def rm(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        sys.stderr.write("gk-peers: cannot remove %s: %s\n" % (path, e))


def plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def peer_info(base: Path, d: dict) -> dict:
    sid = d["sessionId"]
    cl = gk_common.read_json(base / "state" / "gk-claims" / (sid + ".json"))
    claims = cl.get("claims") if isinstance(cl, dict) else None
    branches, files = [], []
    for c in claims if isinstance(claims, list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("path"), str):
            continue
        b = c.get("branch") if isinstance(c.get("branch"), str) else ""
        if b and b not in branches:
            branches.append(b)
        files.append((c["path"], b))
    name = d.get("name") if isinstance(d.get("name"), str) and d.get("name") else sid[:8]
    status = d.get("status") if isinstance(d.get("status"), str) and d.get("status") else "unknown"
    return {"name": name, "status": status, "branches": branches, "claims": len(files),
            "files": files}


def describe(p: dict, with_files: bool) -> str:
    parts = [p["name"], p["status"]]
    if p["branches"]:
        parts.append("branch " + ", ".join(p["branches"]))
    if p["claims"] == 0:
        parts.append("no edits yet")
    elif not with_files:
        parts.append(plural(p["claims"], "file"))
    else:
        multi = len(p["branches"]) > 1
        shown = ["%s@%s" % (f, b) if multi and b else f for f, b in p["files"][:MAX_FILES]]
        extra = p["claims"] - len(shown)
        tail = ", ".join(shown) + (", +%d more" % extra if extra > 0 else "")
        parts.append("%s: %s" % (plural(p["claims"], "file"), tail))
    return " — ".join(parts)


def policy_line(cwd: Any) -> str:
    """POLICY naming the session cwd's project shared branch, when it has one."""
    top = gk_common.git_toplevel(cwd) if isinstance(cwd, str) and cwd else None
    return POLICY % gk_common.shared_branch_phrase(top)


def _more(n: int) -> str:
    return "+%s not listed." % plural(n, "more session")


def briefing(peers: dict, cwd: Any) -> str:
    """The SessionStart briefing, at most BRIEFING_MAX characters, the policy line always last.

    Peers are listed in order while the text (with the "+N more" line it would need and the
    policy line) stays within the limit; the rest are counted in one "+N more" line.
    """
    if not peers:
        return "Live peer sessions (gk-peers): none."
    stamp = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    lines = ["Live peer sessions (gk-peers, %s): %s on this machine."
             % (stamp, plural(len(peers), "other session"))]
    policy = policy_line(cwd)
    body = ["- " + describe(p, True) for p in peers.values()]
    size = len(lines[0])
    shown = 0
    for line in body:
        left = len(body) - shown - 1
        tail = len(policy) + 1 + (len(_more(left)) + 1 if left else 0)
        if size + 1 + len(line) + tail > BRIEFING_MAX:
            break
        lines.append(line)
        size += 1 + len(line)
        shown += 1
    if shown < len(body):
        lines.append(_more(len(body) - shown))
    lines.append(policy)
    return "\n".join(lines)


def changes(peers: dict, prev: dict) -> str | None:
    out = []
    for sid, p in peers.items():
        o = prev.get(sid)
        if not isinstance(o, dict):
            out.append("- New: " + describe(p, False))
            continue
        if o.get("name") != p["name"]:
            out.append("- Renamed: \"%s\" is now \"%s\"" % (o.get("name"), p["name"]))
        ob = o.get("branches") if isinstance(o.get("branches"), list) else []
        if set(ob) != set(p["branches"]):
            out.append("- Branch moved: %s — was %s, now %s" % (
                p["name"], ", ".join(ob) or "none", ", ".join(p["branches"]) or "none"))
    for sid, o in prev.items():
        if sid not in peers and isinstance(o, dict):
            out.append("- Gone: %s (session ended)" % o.get("name", sid[:8]))
    return "Peer sessions changed:\n" + "\n".join(out) if out else None


def handle(data: Any) -> None:
    if not isinstance(data, dict):
        return
    ev = data.get("hook_event_name")
    if ev not in ("SessionStart", "UserPromptSubmit"):
        return
    own = gk_common.safe_sid(data.get("session_id"))
    base = gk_common.claude_dir()
    reg = load_registry(base)
    live_ids = {d["sessionId"] for d in reg if gk_common.pid_alive(d["pid"])}

    # prune state of dead sessions (never our own)
    for d in reg:
        sid = d["sessionId"]
        if sid == own or sid in live_ids:
            continue
        for sub in gk_common.SESSION_STATE_DIRS:
            rm(base / "state" / sub / (sid + ".json"))

    peers = {}
    for d in sorted((x for x in reg if gk_common.pid_alive(x["pid"]) and x["sessionId"] != own),
                    key=lambda x: (_started(x), x["sessionId"])):
        if d["sessionId"] not in peers:
            peers[d["sessionId"]] = peer_info(base, d)

    snap = {sid: {"name": p["name"], "status": p["status"], "branches": p["branches"],
                  "claims": p["claims"]} for sid, p in peers.items()}
    snap_path = base / "state" / "gk-peers" / (own + ".json") if own else None
    prev = gk_common.read_json(snap_path) if snap_path else None
    prev = prev if isinstance(prev, dict) else {}

    text = briefing(peers, data.get("cwd")) if ev == "SessionStart" else changes(peers, prev)

    if snap_path and not gk_common.write_json(snap_path, snap):
        sys.stderr.write("gk-peers: cannot store snapshot %s\n" % snap_path)
    if text:
        gk_common.emit({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": text}})


if __name__ == "__main__":
    gk_common.run_hook("gk-peers", handle)
