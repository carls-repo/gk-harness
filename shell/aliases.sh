# gk-harness launch shortcuts, sourced from your shell startup file by the installer, which sets
# GK_HARNESS_ROOT to the harness checkout first. gk is Opus at medium effort, gk-fable is Fable,
# gk-ultra is Opus with ultracode on, and gkh runs the installer (gkh update, gkh fable off).
# Edit them in the gk-harness repo; nothing depends on them.
# A shell that loaded an older version holds these names as aliases, which bash would expand
# inside the function definitions below; drop them first.
unalias gk gk-opus gk-sonnet gk-fable gk-ultra gkh 2>/dev/null || true
gk() { claude --model opus --effort medium "$@"; }
gk-fable() { claude --model fable --effort medium "$@"; }
gk-ultra() { claude --model opus --effort medium --settings '{"ultracode":true}' "$@"; }
gkh() { python3 "$GK_HARNESS_ROOT/install.py" "$@"; }
