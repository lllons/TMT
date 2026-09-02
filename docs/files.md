# Files and apps

## Files

| Action | Purpose |
|---|---|
| `write_file` / `write_files` | Create a file, or several at once |
| `patch_file` | Search-and-replace — the default for edits |
| `replace_lines` | Replace an exact line range |
| `append_file` | Add to the end of a file |
| `read_file` / `read_lines` | Read a whole file, or a line range |
| `glob` | Find files and directories by path pattern |
| `grep` | Search file contents and report path, line number and the line |
| `copy_file` / `rename_file` / `delete_file` | Move, rename, remove |
| `create_folder` / `delete_folder` | Folders (recursive delete is opt-in) |
| `list_files` | List the workspace |

Paths are interpreted relative to the project directory, and anything that resolves
outside it is refused. Only that directory is listed, read or written.

Editing an existing file uses `patch_file`, not a rewrite, so untouched lines stay
untouched. Python files are syntax-checked before they are written; a broken edit is
rejected rather than saved.

## Apps

`open_app` launches Notepad, or Explorer with a file selected. Nothing else. TMT
supplies only a path, never a program name, and the registry is a closed list of two
— it is not a way to start something.

---

[← Back to the README](../README.md)
