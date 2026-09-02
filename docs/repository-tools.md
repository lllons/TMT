# Understanding a repository

Nine actions for finding your way around a codebase without reading it all. Each
answers one question, and TMT is told to pick the narrowest one that fits.

| Action | Purpose | Reach for it when |
|---|---|---|
| `tree` | Directories, files, sizes, nesting. Reads no contents | You need the shape of the project |
| `glob` | Files and directories matching a path pattern. `*` stops at a `/`, `**/` means any depth, and a pattern with no `/` matches a name anywhere | You need to know which files exist, or where one is |
| `grep` | Search inside files, reporting path, line number and the line. Exact and case-sensitive by default; the query may span several lines | You know the text you are looking for |
| `find_symbol` | Where a function, class, method, constant or type is *defined* | You want a definition, not a mention |
| `code_map` | What defines this, what imports it, what it imports, where it is referenced | You need to know what a change would affect |
| `replace_across` | The same exact edit in many files | Renaming something the whole project uses |
| `related_tests` | Reads the git diff and names the tests worth running | You changed one thing and do not want to run everything |
| `remember` / `recall` | Durable notes about this project, kept between sessions | Something cost you time to work out |

```
Task> show me the project structure
Task> find every place that calls self.workspace_root
Task> where is calculate_total defined?
Task> what imports agent_file_ops?
Task> rename old_function_name to new_function_name across src
Task> which tests should I run for what I just changed?
```

**`glob` finds files by path or name; `grep` finds text inside files.** That is the
whole distinction, and it is the one worth getting right: the order that works is
`glob` to find the candidate files, `grep` to find the lines in them, `read_lines` to
read the region, then edit, then test — rather than reading a repository to find one
line.

```json
{"action": "glob", "pattern": "agent_*.py"}
{"action": "glob", "pattern": "testing/**/*.py"}
{"action": "grep", "query": "end_conversation"}
{"action": "grep", "query": "def safe_path", "glob": "agent_*.py"}
{"action": "grep", "query": "timeout", "path": "src", "ignore_case": true}
```

`grep` is exact and case-sensitive by default, like the tool it is named after.
`"ignore_case": true` makes it loose, `"regex": true` reads the query as a regular
expression, `"context"` adds lines either side of each match, and `"path"` or `"glob"`
restricts which files are read at all. It never returns a whole file: you get the path,
the line number and the line, and `read_lines` gets you the rest.

**`replace_across` previews by default.** It reports how many files and occurrences it
*would* change and writes nothing. Sending the same action again with `"apply": true`
performs it. Line endings and encoding are preserved, binary files are skipped, and a
replacement that would leave a Python file unparseable is refused rather than written.

**Facts and guesses are labelled differently.** Python symbols are found by parsing the
file, so those answers are exact; other languages are matched lexically and say so.
`related_tests` separates what the diff proves from what it is only guessing. Nothing
presents a heuristic as a measurement.

**Project memory** is stored beside TMT's own settings, keyed by project, never inside
your repository — the same rule as every other piece of TMT state. Notes are scanned
before they are written and anything shaped like a key, token or password is refused.

---

[← Back to the README](../README.md)
