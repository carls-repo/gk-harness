"""PreToolUse hook on the Workflow tool: run gk-* workflows under the seat's profile.

Standard library only, Python 3.9+. For a gk-* call it fills in what the seat left out of
`args`: the seat's profile, the project file (`.claude/gk-project.json` at the session's git
top level, plus `root`), and for gk-diagnose the session scratchpad. An explicit value is
always kept; a profile above the seat without `escalate: true` draws a warning. It never
blocks: there is no permissionDecision, only updatedInput and warnings.

A repo whose own `.claude/workflows/<name>.js` exists (at the git top level or in `cwd`) runs
that copy, which may predate gk-harness and reject keys it does not know. For such a call
the hook injects nothing and only adds a note to the context saying so.

The name also comes from the script the runtime saves for each run
(`gk-review-wf_<id>.js`), so a call that re-runs or resumes by `scriptPath` is a gk call too.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import gk_common
import gk_model

_NAME_RE = re.compile(r"gk-[a-z0-9-]+")
_SCRIPT_RE = re.compile(r"(gk-[a-z0-9-]+?)(?:-wf_[0-9a-z]+(?:-[0-9a-z]+)*)?\.js")

HIGHER = ("gk-harness: this Workflow call asks for profile '{p}' above the seat ('{seat}') "
          "without escalate: true. The policy allows that only on Carl's word.")
UNREADABLE = ("gk-harness: .claude/gk-project.json at {root} is unreadable ({reason}); "
              "the workflow runs without the project rules.")
PROJECT_COPY = ("gk-harness: this repo's own .claude/workflows/{name}.js runs (it predates "
                "gk-harness), so nothing was injected; pass profile yourself only if that copy "
                "accepts it, and merge the shared branch to pick up the user-level workflows.")


def gk_name(tool_input: dict) -> str | None:
    """The gk workflow this call runs, from `name` or the basename of `scriptPath`.

    A saved-run script (`gk-review-wf_33244bcf-5bd.js`) gives the bare name (`gk-review`).
    """
    name = tool_input.get("name")
    if isinstance(name, str) and _NAME_RE.fullmatch(name):
        return name
    path = tool_input.get("scriptPath")
    if isinstance(path, str):
        m = _SCRIPT_RE.fullmatch(re.split(r"[\\/]", path)[-1])
        if m:
            return m.group(1)
    return None


def parse_args(raw: Any) -> dict | None:
    """`args` as a new dict, or None for a shape the hook leaves alone."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _absent(args: dict, key: str) -> bool:
    return args.get(key) is None or args.get(key) == ""


def _project_copy(name: str, top: Path | None, cwd: Any) -> bool:
    """True when the repo has its own .claude/workflows/<name>.js, which then runs instead."""
    rel = (".claude", "workflows", name + ".js")
    bases = [top] if top is not None else []
    if isinstance(cwd, str) and cwd:
        bases.append(Path(cwd))
    for base in bases:
        try:
            if base.joinpath(*rel).is_file():
                return True
        except (OSError, ValueError):
            pass
    return False


def handle(data: Any) -> None:
    if not isinstance(data, dict) or data.get("tool_name") != "Workflow":
        return
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    script = tool_input.get("script")
    if isinstance(script, str) and script:
        return  # an inline script is never a gk call, whatever its name says
    name = gk_name(tool_input)
    if name is None:
        return
    cwd = data.get("cwd")
    top = gk_common.git_toplevel(cwd) if isinstance(cwd, str) and cwd else None
    if _project_copy(name, top, cwd):
        # The repo's own (older) copy wins and may reject keys it does not know.
        gk_common.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                               "additionalContext": PROJECT_COPY.format(
                                                   name=name)}})
        return
    args = parse_args(tool_input.get("args"))
    if args is None:
        return

    changed = False
    warnings = []

    seat = gk_model.resolve_profile(data)
    if _absent(args, "profile"):
        if seat is not None:
            args["profile"] = seat
            changed = True
    else:
        p = args["profile"]
        if (isinstance(p, str) and p in gk_model.RANK and seat is not None
                and gk_model.RANK[p] > gk_model.RANK[seat] and args.get("escalate") is not True):
            warnings.append(HIGHER.format(p=p, seat=seat))

    if "project" not in args and top is not None:
        root = top.as_posix()
        project, reason = gk_common.load_project(top)
        args["project"] = dict(project or {}, root=root)
        changed = True
        if reason is not None:
            warnings.append(UNREADABLE.format(root=root, reason=reason))

    scratch = data.get("scratchpad_dir")
    if (name == "gk-diagnose" and _absent(args, "scratchpad") and isinstance(scratch, str)
            and scratch):
        args["scratchpad"] = scratch
        changed = True

    if not changed and not warnings:
        return
    hso: dict = {"hookEventName": "PreToolUse"}
    if changed:
        hso["updatedInput"] = dict(tool_input, args=args)
    out: dict = {"hookSpecificOutput": hso}
    if warnings:
        text = "\n".join(warnings)
        hso["additionalContext"] = text
        out["systemMessage"] = text
    gk_common.emit(out)


if __name__ == "__main__":
    gk_common.run_hook("gk-profile", handle)
