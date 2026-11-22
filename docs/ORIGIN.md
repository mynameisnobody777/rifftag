# Where RiffTag came from

A record of how the project was specified, kept because the question
comes up and because the method matters.

## Why this was not a reverse-engineering job

The obvious plan was to point a decompiler at `TagRename.exe`. That was the
wrong call, and the reasoning is worth keeping because it will look obvious
again next time:

**The complete specification was already on disk**, inside
`~/.wine-tagrename/drive_c/Program Files/TagRename/`:

| Source | What it gave us |
|---|---|
| `TagRename.chm` | 30 help topics + 60 UI screenshots. Extracted to `docs/` |
| the help's own tables | the entire rename-variable grammar, verbatim |
| 18 `.lng` files | every UI string id — a free feature inventory |
| Delphi RTTI left in the PE32 | every form name (`UnitFormEditFileName`, `UnitFormFreedbGetServers`, …) — a free architecture map |

Extract the help again with:

```bash
7z x -y "$HOME/.wine-tagrename/drive_c/Program Files/TagRename/TagRename.chm"
```

A clean-room spec from published documentation is faster, more complete and
legally uncomplicated. That extracted spec lives at
`docs/tagrename-help-extracted.txt` and `docs/refpics/` on the development
machine, and is **deliberately excluded from this repository**: it is the
vendor's own copyrighted text and screenshots. Reading someone's published
documentation to build a clean-room implementation is fine; redistributing
that documentation is not.

---
