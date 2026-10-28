"""The block in the shell startup file that loads the gk launch shortcuts, and the removal of the
pre-harness gk aliases. Pure text in, text out; gkh.paths.rc_file says which file."""
from __future__ import annotations

import re
from typing import Any

from . import claude_md, legacy
from .paths import quotable

_LINE = re.compile(r"[^\n]*\n|[^\n]+")  # each line with its "\n"; splits on "\n" only

BEGIN_MARKER = "# >>> gk-harness >>>"
END_MARKER = "# <<< gk-harness <<<"
LABEL = "the shell startup file"


def is_begin(line: str) -> bool:
    return line.strip() == BEGIN_MARKER


def is_end(line: str) -> bool:
    return line.strip() == END_MARKER


def render_block(root: Any, windows: bool) -> str:
    """The block that sources <root>/shell/aliases.sh, or dot-sources <root>/shell/aliases.ps1
    on Windows (PowerShell reads `#` as a comment too). The path is posix and double-quoted;
    raises ValueError (paths.quotable) when it holds '"', '$' or '`'."""
    name = "aliases.ps1" if windows else "aliases.sh"
    return '%s\n. "%s/shell/%s"\n%s\n' % (BEGIN_MARKER, quotable(root), name, END_MARKER)


def apply(existing: str | None, block: str, label: str = LABEL,
          powershell: bool = False) -> tuple:
    """(new_text, mode): mode is "created", "unchanged", "updated" or "appended".

    powershell: the file is a PowerShell profile read as UTF-8 (pass False for a UTF-16 or
    UTF-32 one, whose mark comes with its encoding). One this call creates (existing None), or
    one that exists but is empty (as New-Item $PROFILE -Force leaves it; mode "appended"),
    starts with a byte-order mark, so that written as UTF-8 it is read as UTF-8 by Windows
    PowerShell 5.1, which reads a .ps1 without a mark in the ANSI code page and would misread a
    non-ASCII root. Any other existing profile keeps its own encoding and mark
    (manifest.read_text_encoded).
    """
    if powershell and existing is None:
        return claude_md.BOM + block, "created"
    if powershell and existing == "":
        return claude_md.BOM + block, "appended"
    return claude_md.apply_block(existing, block, is_begin, is_end, label)


def strip(text: str, label: str = LABEL) -> str:
    return claude_md.strip_blocks(text, is_begin, is_end, label)


def has_block(text: str, label: str = LABEL) -> bool:
    return bool(claude_md.marker_spans(text, is_begin, is_end, label))


def strip_legacy_aliases(text: str) -> tuple:
    """(new_text, removed): text without the lines matching legacy.RC_ALIAS_RE, and
    [{"line": 1-based line number, "text": the line without its newline}] for each. A
    byte-order mark at the start is not part of line 1 and stays."""
    bom = claude_md.bom_of(text)
    kept, removed = [bom], []
    for number, line in enumerate(_LINE.findall(text[len(bom):]), 1):
        bare = line.rstrip("\r\n")
        if legacy.RC_ALIAS_RE.match(bare):
            removed.append({"line": number, "text": bare})
        else:
            kept.append(line)
    return "".join(kept), removed
