"""PreToolUse hook on the Workflow tool: fill in what a gk-* workflow call left out of `args`.

Standard library only, Python 3.9+. The filename is historical: the hook no longer deals in
profiles for current scripts. It never blocks: there is no permissionDecision, only
updatedInput and warnings. An explicit value is always kept, except `project.legal` and
gk-review's `changedPaths` (git's paths are always in it).

What a gk call gets depends on the marker in the script it runs (the first MARKER_SCAN
bytes, on the line after `meta`):

- `gk-harness:args-v2` (the current scripts): `seat`, the seat's tier from routes.json
  (`opus-medium`; none when unknown); `dials`, the caller's list plus `nofable` while
  `gkh fable off` holds; `project`, the project file (`.claude/gk-project.json` at the
  session's git top level, plus `root`), whose `legal` always comes from that file, even into
  a project the caller passed; `scratchpad` for gk-diagnose; and `changedPaths` for gk-review,
  git's paths for a target that passes gk-review's own pattern, followed by any other paths
  the caller listed. No `profile`.
- `gk-harness:args-v1` (an older copy still on disk): exactly what it always got, `profile`
  from the seat's model, `project` and `scratchpad`, with the warning for a profile above the
  seat; nothing it might reject as unknown.
- no marker: nothing is injected, only a note in the context saying so.

The script is the one the call runs: for a `name`, the first regular file of
`<git top level>/.claude/workflows/<name>.js`, `<cwd>/.claude/workflows/<name>.js` and
`<claude dir>/workflows/<name>.js`; for a `scriptPath`, that path (against `cwd` when
relative). When no script is found the hook injects as for args-v2, the version the
installer puts in place.

The name also comes from the script the runtime saves for each run
(`gk-review-wf_<id>.js`), so a call that re-runs or resumes by `scriptPath` is a gk call too.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import gk_common
import gk_model

_NAME_RE = re.compile(r"gk-[a-z0-9-]+")
_SCRIPT_RE = re.compile(r"(gk-[a-z0-9-]+?)(?:-wf_[0-9a-z]+(?:-[0-9a-z]+)*)?\.js")
# args-v1 scripts take profile, project and scratchpad; args-v2 scripts take seat, dials,
# project, scratchpad and changedPaths instead of profile.
MARKERS = (b"gk-harness:args-v1", b"gk-harness:args-v2")
MARKER_SCAN = 16384  # bytes read from the head of a script when looking for the marker
WORKING_TREE = "working-tree"
# gk-review's own target check (workflows/gk-review.js), the same source text; a test keeps
# them equal. A target must also not contain "..-", so no range end reads as an option.
TARGET_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\/~^@{}-]*(\.\.\.?[A-Za-z0-9][A-Za-z0-9._\/~^@{}-]*)?$"
TARGET_RE = re.compile(TARGET_PATTERN)
CHANGED_MAX = 2000
_MISSING = object()

# args-v1 only: the profile an old script expects, as the retired profile_for_model gave it.
_V1_RANK = {"sonnet": 1, "opus": 2, "fable": 3}

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


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.stat(str(path)).st_mode)
    except (OSError, ValueError):
        return False


def _script_file(tool_input: dict, top: Path | None, cwd: Any) -> Path | None:
    """The script this call runs, or None when the hook finds no such regular file."""
    usable_cwd = isinstance(cwd, str) and cwd
    name = tool_input.get("name")
    if isinstance(name, str) and _NAME_RE.fullmatch(name):
        rel = (".claude", "workflows", name + ".js")
        candidates = [top.joinpath(*rel)] if top is not None else []
        if usable_cwd:
            candidates.append(Path(cwd).joinpath(*rel))
        candidates.append(gk_common.claude_dir() / "workflows" / (name + ".js"))
    else:
        path = Path(tool_input.get("scriptPath") or "")  # gk_name matched it, so a str
        if not path.is_absolute():
            if not usable_cwd:
                return None
            path = Path(cwd) / path
        candidates = [path]
    for c in candidates:
        if _regular(c):
            return c
    return None


def _marker_version(path: Path) -> int | None:
    """2 when the script's first MARKER_SCAN bytes carry the args-v2 marker, 1 when they carry
    only the args-v1 one, None when they carry neither.

    The file is opened without blocking and checked again once open, so a FIFO swapped in
    after the stat is never read; anything unreadable counts as unmarked.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(path), flags)
    except (OSError, ValueError):
        return None
    try:
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            head = f.read(MARKER_SCAN)
    except (OSError, ValueError):
        return None
    if MARKERS[1] in head:
        return 2
    return 1 if MARKERS[0] in head else None


def target_ok(target: Any) -> bool:
    """Whether gk-review would accept this target: "working-tree", or a ref-shaped range."""
    if not isinstance(target, str):
        return False
    if target == WORKING_TREE:
        return True
    return TARGET_RE.fullmatch(target) is not None and "..-" not in target


def changed_paths(top: Path, target: str) -> list[str] | None:
    """The paths gk-review's target changes, relative to the top level, at most CHANGED_MAX.

    A range is diffed as gk-review's finders diff it (`git diff <target>`, so `A...B` runs
    from the merge base); "working-tree" is `git diff HEAD` plus the untracked files git does
    not ignore. None when the target fails gk-review's pattern (and then no git runs at all)
    or when git fails.
    """
    if not target_ok(target):
        return None
    # No fsmonitor or external diff program runs; --no-renames: a renamed file counts at
    # both paths; "--" ends the revisions, so the target can never be read as a path.
    git = ["-c", "core.fsmonitor=false"]
    diff = git + ["diff", "--no-ext-diff", "--no-renames", "--name-only", "-z"]
    if target == WORKING_TREE:
        runs = [diff + ["HEAD", "--"], git + ["ls-files", "--others", "--exclude-standard", "-z"]]
    else:
        runs = [diff + [target, "--"]]
    paths: list = []
    seen: set = set()
    for git_args in runs:
        r = gk_common._git(top, git_args)
        if r is None or r.returncode != 0:
            return None
        for p in r.stdout.split("\0"):
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
                if len(paths) >= CHANGED_MAX:
                    return paths
    return paths


def _v1_map(model: Any) -> str | None:
    """args-v1 only, the retired profile_for_model: fable|opus|sonnet when the model string
    names one; sonnet for any other claude or haiku model; else None."""
    if not isinstance(model, str):
        return None
    m = model.lower()
    for p in ("fable", "opus", "sonnet"):
        if p in m:
            return p
    return "sonnet" if "claude" in m or "haiku" in m else None


def _v1_profile(data: dict) -> str | None:
    """args-v1 scripts only, as before: the seat record (its profile when it has one, else
    its model), then the transcript's model, then $GK_PROFILE."""
    sid = data.get("session_id")
    rec = gk_common.read_json(gk_model.seat_path(sid)) if gk_common.safe_sid(sid) else None
    if isinstance(rec, dict):
        p = rec.get("profile") if rec.get("profile") in _V1_RANK else _v1_map(rec.get("model"))
        if p:
            return p
    p = _v1_map(gk_model.model_from_transcript(data.get("transcript_path"), accept=_v1_map))
    if p:
        return p
    env = os.environ.get("GK_PROFILE")
    return env if env in _V1_RANK else None


def _inject_project(args: dict, top: Path | None, warnings: list) -> bool:
    """Add the project file (plus root) when args has no project key. True when added."""
    if "project" in args or top is None:
        return False
    root = top.as_posix()
    project, reason = gk_common.load_project(top)
    args["project"] = dict(project or {}, root=root)
    if reason is not None:
        warnings.append(UNREADABLE.format(root=root, reason=reason))
    return True


def _set_legal(args: dict, top: Path | None) -> bool:
    """Make `project.legal` the project file's: set it from the file, or remove it when the
    file has none (no repository, no file, an unreadable file). True when args changed."""
    project = args.get("project")
    if not isinstance(project, dict):
        return False  # absent, or malformed: the script rejects what it cannot use
    data = gk_common.load_project(top)[0] if top is not None else None
    if isinstance(data, dict) and "legal" in data:
        legal = data["legal"]
        cur = project.get("legal", _MISSING)
        if cur is not _MISSING and type(cur) is type(legal) and cur == legal:
            return False
        args["project"] = dict(project, legal=legal)
        return True
    if "legal" in project:
        args["project"] = {k: v for k, v in project.items() if k != "legal"}
        return True
    return False


def _inject_v2(name: str, args: dict, data: dict, top: Path | None, warnings: list) -> bool:
    changed = False
    if _absent(args, "seat"):
        seat = gk_model.resolve_seat(data)
        if seat is not None:
            args["seat"] = seat
            changed = True
    if gk_common.fable_off():
        dials = args.get("dials")
        if dials is None:
            args["dials"] = ["nofable"]
            changed = True
        elif isinstance(dials, list) and "nofable" not in dials:
            args["dials"] = dials + ["nofable"]
            changed = True
    changed = _inject_project(args, top, warnings) or changed
    changed = _set_legal(args, top) or changed
    if name == "gk-review" and top is not None:
        changed = _merge_changed_paths(args, changed_paths(top, args.get("target"))) or changed
    return changed


def _merge_changed_paths(args: dict, found: list | None) -> bool:
    """changedPaths becomes git's paths, then the caller's other string entries, at most
    CHANGED_MAX: a caller's list can add paths but never hide one (the judge's path floor
    comes from code). When git found nothing usable (None) the caller's value is kept."""
    if found is None:
        return False
    given = args.get("changedPaths")
    merged = list(found)
    seen = set(merged)
    for p in given if isinstance(given, list) else []:
        if len(merged) >= CHANGED_MAX:
            break
        if isinstance(p, str) and p not in seen:
            seen.add(p)
            merged.append(p)
    if merged == given:
        return False
    args["changedPaths"] = merged
    return True


def _inject_v1(args: dict, data: dict, top: Path | None, warnings: list) -> bool:
    """Today's behaviour for an old args-v1 copy: profile, project, nothing new."""
    changed = False
    seat = _v1_profile(data)
    if _absent(args, "profile"):
        if seat is not None:
            args["profile"] = seat
            changed = True
    else:
        p = args["profile"]
        if (isinstance(p, str) and p in _V1_RANK and seat is not None
                and _V1_RANK[p] > _V1_RANK[seat] and args.get("escalate") is not True):
            warnings.append(HIGHER.format(p=p, seat=seat))
    return _inject_project(args, top, warnings) or changed


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
    script_file = _script_file(tool_input, top, cwd)
    version = 2 if script_file is None else _marker_version(script_file)
    if version is None:
        # An older copy runs (the repo's own, as a rule) and may reject keys it does not know.
        gk_common.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                               "additionalContext": PROJECT_COPY.format(
                                                   name=name)}})
        return
    args = parse_args(tool_input.get("args"))
    if args is None:
        return

    warnings: list = []
    if version == 1:
        changed = _inject_v1(args, data, top, warnings)
    else:
        changed = _inject_v2(name, args, data, top, warnings)

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
