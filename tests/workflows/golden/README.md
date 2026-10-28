# Golden Goodricke workflows

Byte-for-byte copies of the five Goodricke workflows as they stood on `develop` at commit
`0ba966af0` (repo `/home/carls/apps/mike-dev`). They are the reference the project-neutral
rewrites are compared against: every stage must keep the same (model, effort) under every
profile and flag.

Regenerate or verify (read-only, no checkout):

```bash
git -C /home/carls/apps/mike-dev show 0ba966af0:.claude/workflows/<file> | cmp - tests/workflows/golden/<file>
```

Files: `gk-review.js`, `gk-build.js`, `gk-design.js`, `gk-research.js`, `gk-diagnose.js`.
