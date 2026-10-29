# Golden Goodricke workflows

Byte-for-byte copies of the five Goodricke workflows as they stood on `develop` at commit
`8302903ad` (repo `/home/carls/apps/mike-dev`). They are the reference the project-neutral
rewrites are compared against: every stage must keep the same (model, effort) under every
profile and flag.

The copies were first taken at `0ba966af0`. They were re-pinned to `8302903ad` (ruling R48)
because Carl's commit of 2026-10-28, "gk-review and gk-diagnose agents read other revisions
without touching the shared checkout", changed two of them after that: `gk-review.js` gained
the read-at-the-tip instruction and the never-change-the-checkout line, and `gk-diagnose.js`
gained the stronger never-change-the-checkout rule. `gk-build.js`, `gk-design.js` and
`gk-research.js` are identical at both commits.

Regenerate or verify (read-only, no checkout):

```bash
git -C /home/carls/apps/mike-dev show 8302903ad:.claude/workflows/<file> | cmp - tests/workflows/golden/<file>
```

Files: `gk-review.js`, `gk-build.js`, `gk-design.js`, `gk-research.js`, `gk-diagnose.js`.
