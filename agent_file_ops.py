"""Workspace-safe file operations exposed by the agent."""

import ast
import re
import shutil
from agent_config import ROOT_DIR

def safe_path(user_path):
    target = (ROOT_DIR / user_path).resolve()
    if ROOT_DIR != target and ROOT_DIR not in target.parents:
        raise ValueError(f"Blocked unsafe path: {user_path}")
    return target

def _decode_content(content):
    return content.replace("\\n", "\n").replace("\\t", "\t")

def write_file(path, content):
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = _decode_content(content)
    if p.suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as error:
            return f"SyntaxError in generated code: {error}. File NOT written. Please fix."
    p.write_text(content, encoding="utf-8")
    return f"Wrote file: {path}"

def append_file(path, content):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    existing = p.read_text(encoding="utf-8")
    separator = "\n" if existing and not existing.endswith("\n") else ""
    p.write_text(existing + separator + _decode_content(content), encoding="utf-8")
    return f"Appended to: {path}"

def write_files(files):
    if not isinstance(files, list):
        return "Error: 'files' must be a list of {path, content} objects"
    results = []
    for entry in files:
        path = entry.get("path", "")
        if path:
            results.append(write_file(path, entry.get("content", "")))
        else:
            results.append("Skipped entry with no path")
    return "\n".join(results)

def patch_file(path, search_text, replace_text):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    content = p.read_text(encoding="utf-8")
    search_text, replace_text = _decode_content(search_text), _decode_content(replace_text)
    if search_text not in content:
        return f"Search text not found in {path}"
    new_content = content.replace(search_text, replace_text, 1)
    if p.suffix == ".py":
        try:
            ast.parse(new_content)
        except SyntaxError as error:
            return f"SyntaxError introduced by patch: {error}. Patch aborted."
    p.write_text(new_content, encoding="utf-8")
    return f"Patched file: {path}"

def delete_file(path):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    if p.is_dir():
        return f"Refusing to delete directory: {path}"
    if input(f"Delete {path}? (y/N): ").strip().lower() != "y":
        return "Delete cancelled"
    p.unlink()
    return f"Deleted file: {path}"

def read_file(path):
    p = safe_path(path)
    return p.read_text(encoding="utf-8") if p.exists() else f"File not found: {path}"

def list_files():
    files = [str(p.relative_to(ROOT_DIR)) for p in ROOT_DIR.rglob("*") if p.is_file()]
    return "\n".join(files) if files else "(empty workspace)"

def create_folder(path):
    p = safe_path(path)
    if p.exists():
        return f"Already exists: {path}"
    p.mkdir(parents=True, exist_ok=True)
    return f"Created folder: {path}"

MAX_SEARCH_HITS = 100

def search_files(query, regex=False, path=None):
    try:
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    except re.error as error:
        return f"Invalid regex: {error}"
    root = safe_path(path) if path else ROOT_DIR
    if not root.exists():
        return f"Path not found: {path}"
    targets = [root] if root.is_file() else sorted(root.rglob("*"))
    hits = []
    for p in targets:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{p.relative_to(ROOT_DIR)}:{lineno}: {line.strip()[:200]}")
                if len(hits) >= MAX_SEARCH_HITS:
                    hits.append(f"... stopped at {MAX_SEARCH_HITS} matches — narrow the query.")
                    return "\n".join(hits)
    return "\n".join(hits) if hits else f"No matches for: {query}"

def read_lines(path, start=1, end=None):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        start = max(1, int(start))
        end = len(lines) if end in (None, "") else min(len(lines), int(end))
    except (TypeError, ValueError):
        return "start and end must be whole numbers"
    if start > len(lines):
        return f"{path} has only {len(lines)} lines"
    return "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(start, end + 1))

def replace_lines(path, start, end, content):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    lines = p.read_text(encoding="utf-8").splitlines()
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return "start and end must be whole numbers"
    if start < 1 or start > end or end > len(lines):
        return f"Invalid range {start}-{end}; {path} has {len(lines)} lines"
    new_content = "\n".join(lines[:start - 1] + _decode_content(content).splitlines() + lines[end:]) + "\n"
    if p.suffix == ".py":
        try:
            ast.parse(new_content)
        except SyntaxError as error:
            return f"SyntaxError introduced by replace_lines: {error}. Aborted."
    p.write_text(new_content, encoding="utf-8")
    return f"Replaced lines {start}-{end} in {path}"

def copy_file(path, dest):
    if not dest:
        return "copy_file needs a 'to' path"
    src, dst = safe_path(path), safe_path(dest)
    if not src.exists():
        return f"File not found: {path}"
    if src.is_dir():
        return f"Refusing to copy a directory: {path}"
    if dst.exists():
        return f"Refusing to overwrite existing file: {dest}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return f"Copied {path} to {dest}"

def delete_folder(path, recursive=False):
    p = safe_path(path)
    if p == ROOT_DIR:
        return "Refusing to delete the workspace root"
    if not p.exists():
        return f"Folder not found: {path}"
    if not p.is_dir():
        return f"Not a folder: {path} — use delete_file instead"
    contents = list(p.rglob("*"))
    if contents and not recursive:
        return f"{path} is not empty ({len(contents)} items). Retry with \"recursive\": true to delete everything inside."
    label = f"{path} and {len(contents)} items inside" if contents else path
    if input(f"Delete {label}? (y/N): ").strip().lower() != "y":
        return "Delete cancelled"
    shutil.rmtree(p)
    return f"Deleted folder: {path}"
