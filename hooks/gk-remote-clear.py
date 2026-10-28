"""PostToolUse hook for the Claude Docs tools | Artifact | SendUserFile: clear pending specs.

Standard library only, Python 3.9+. A publish clears this session's pending specs and records
each one's link in <repo>/docs/superpowers/REMOTE-LINKS.tsv, which is created (with its
folders) on the first publish in that repo. Never emits context. Always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

import gk_common

LINK_RE = re.compile(r"https://claude\.ai/(code/)?artifact/[0-9A-Za-z-]{20,}")
DOC_ID_RE = re.compile(r"[0-9A-Za-z-]{20,}")
DOCS_TOOL_RE = re.compile(r"mcp__.+__(batch|update|create)")
HEADER = "path\turl\tpublished\n"
# Claude Docs create records whose object is one of these (or absent) write doc content;
# utterances are comments, and blob/file/enum records are not a reading copy.
DOCS_CREATE_OBJECTS = (None, "node")


def is_docs_publish(tool_name: str, tool_input: dict) -> bool:
    """Whether a tool call writes a Claude Doc, under whatever server name the surface uses.

    The name must end in __batch, __update or __create, and the input must have that
    tool's docs shape:
    - batch: a `container` object;
    - update: a `ref` object and a `payload`;
    - create: a `payload`, an `object` of "node" or none (not a comment utterance or a
      blob/file/enum record), and the doc named by a `container` object or a non-empty
      `artifact` string.
    That keeps other servers' tools (create_branch, create_draft, ...) out.
    """
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return False
    m = DOCS_TOOL_RE.fullmatch(tool_name)
    if not m:
        return False
    verb = m.group(1)
    if verb == "batch":
        return isinstance(tool_input.get("container"), dict)
    if verb == "update":
        return isinstance(tool_input.get("ref"), dict) and "payload" in tool_input
    artifact = tool_input.get("artifact")
    return ("payload" in tool_input
            and tool_input.get("object") in DOCS_CREATE_OBJECTS
            and (isinstance(tool_input.get("container"), dict)
                 or (isinstance(artifact, str) and artifact != "")))


def clean(s: Any) -> str:
    return re.sub(r"[\t\r\n]+", " ", str(s))


def append_ledger(root: str, rows: list[tuple[str, str, str]]) -> None:
    """Append rows to the repo's ledger, writing the header first when the file is new.

    Callers hold the gk-remote-pending folder lock, which every gate hook (old and new)
    takes before touching a ledger, so appends never interleave.
    """
    tsv = os.path.join(root, *gk_common.REMOTE_LEDGER)
    os.makedirs(os.path.dirname(tsv), exist_ok=True)
    need_header = (not os.path.exists(tsv)) or os.path.getsize(tsv) == 0
    need_nl = False
    if not need_header:
        with open(tsv, "rb") as f:
            f.seek(-1, os.SEEK_END)
            need_nl = f.read(1) != b"\n"
    with open(tsv, "a", encoding="utf-8", newline="") as f:
        if need_header:
            f.write(HEADER)
        elif need_nl:
            f.write("\n")
        for path, url, date in rows:
            f.write("%s\t%s\t%s\n" % (clean(path), clean(url), date))


def write_ledgers(rows: dict[str, list[tuple[str, str, str]]]) -> None:
    for root, r in rows.items():
        try:
            append_ledger(root, r)
        except Exception as ex:
            sys.stderr.write("gk-remote-clear: ledger append failed: %r\n" % (ex,))


def save(pfile: Any, st: dict) -> None:
    if not gk_common.write_json(pfile, st):
        sys.stderr.write("gk-remote-clear: cannot store %s\n" % pfile)


def sent_paths(ti: dict) -> list[str]:
    out = []
    files = ti.get("files")
    if isinstance(files, str):
        files = [files]
    if not isinstance(files, list):
        return out
    for x in files:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for k in ("path", "file_path", "file"):
                if isinstance(x.get(k), str):
                    out.append(x[k])
                    break
    return out


def clear_sent(pfile: Any, sid: str, ti: dict, cwd: str, date: str) -> None:
    """SendUserFile clears only the pending specs it sent."""
    sent = sent_paths(ti)
    if not sent:
        return
    sent_abs = set(gk_common.canon_path(s, cwd) for s in sent)
    sent_raw = set(sent)
    with gk_common.locked(pfile.parent):
        st = gk_common.load_pending(pfile, sid)
        keep, rows = [], {}
        for e in st["pending"]:
            root = e.get("root") if isinstance(e.get("root"), str) else None
            eabs = gk_common.canon_path(e["path"], root) if root else None
            if (eabs and eabs in sent_abs) or e["path"] in sent_raw:
                if root:
                    rows.setdefault(root, []).append((e["path"], "sent:SendUserFile", date))
            else:
                keep.append(e)
        if len(keep) == len(st["pending"]):
            return
        write_ledgers(rows)
        st["pending"] = keep
        save(pfile, st)


def clear_published(pfile: Any, sid: str, data: dict, ti: dict, date: str) -> None:
    """A doc or artifact publish clears every pending spec and records its link."""
    with gk_common.locked(pfile.parent):
        st = gk_common.load_pending(pfile, sid)
        if not st["pending"]:
            return
        try:
            blob = json.dumps(data.get("tool_response"))
        except Exception:
            blob = ""
        m = LINK_RE.search(blob)
        url = m.group(0) if m else None
        if url is None:
            # Docs update/batch responses carry no artifact link; the container id of the doc
            # being revised is the artifact id.
            cont = ti.get("container")
            cid = cont.get("id") if isinstance(cont, dict) else None
            if isinstance(cid, str) and DOC_ID_RE.fullmatch(cid):
                url = "https://claude.ai/code/artifact/" + cid
        if url is None:
            url = "doc:unknown-url"
        rows = {}
        for e in st["pending"]:
            if isinstance(e.get("root"), str):
                rows.setdefault(e["root"], []).append((e["path"], url, date))
        write_ledgers(rows)
        st["pending"] = []
        save(pfile, st)


def handle(data: Any) -> None:
    if not isinstance(data, dict):
        return
    sid = gk_common.safe_sid(data.get("session_id"))
    if not sid:
        return
    tool = data.get("tool_name")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else os.getcwd()
    pfile = gk_common.pending_file(sid)
    if not pfile.exists():
        return
    date = time.strftime("%Y-%m-%d", time.gmtime())

    if tool == "SendUserFile":
        clear_sent(pfile, sid, ti, cwd, date)
    elif tool == "Artifact":
        if ti.get("action") in (None, "", "publish") and not ti.get("asset"):
            clear_published(pfile, sid, data, ti, date)
    elif is_docs_publish(tool, ti):
        clear_published(pfile, sid, data, ti, date)


if __name__ == "__main__":
    gk_common.run_hook("gk-remote-clear", handle)
