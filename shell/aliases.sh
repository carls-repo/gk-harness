# gk-harness launch shortcuts, sourced from your shell startup file by the installer, which sets
# GK_HARNESS_ROOT to the harness checkout first. gk is Opus at medium effort, gk-fable is Fable,
# gk-ultra is Opus with ultracode on, and gkh runs the installer (gkh update, gkh fable off).
# Edit them in the gk-harness repo; nothing depends on them.
# A shell that loaded an older version holds these names as aliases, which bash would expand
# inside the function definitions below; drop them first, one name at a time, so a shell that
# stops at a missing name (zsh) still drops the rest.
for _gk_n in gk gk-opus gk-sonnet gk-fable gk-ultra gkh; do unalias "$_gk_n" 2>/dev/null; done; unset _gk_n
gk() { claude --model opus --effort medium "$@"; }
gk-fable() { claude --model fable --effort medium "$@"; }
gk-ultra() { claude --model opus --effort medium --settings '{"ultracode":true}' "$@"; }
# Without GK_HARNESS_ROOT gkh says why and fails, rather than run python3 "/install.py".
gkh() { python3 "${GK_HARNESS_ROOT:?gk-harness: open a new shell, or run install.py install from the gk-harness checkout}/install.py" "$@"; }
