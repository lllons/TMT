"""Code runners and approved desktop application launching."""

import platform
import subprocess
from pathlib import Path
from agent_config import ROOT_DIR
from agent_file_ops import safe_path

RUNNERS = {
    ".py": ["python", "{file}"], ".js": ["node", "{file}"], ".rb": ["ruby", "{file}"],
    ".php": ["php", "{file}"], ".lua": ["lua", "{file}"], ".pl": ["perl", "{file}"],
    ".r": ["Rscript", "{file}"], ".go": ["go", "run", "{file}"],
    ".ts": ["npx", "--yes", "ts-node", "{file}"],
}

def _run_cmd(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=ROOT_DIR)
        output = (result.stdout + result.stderr).strip()
        return output[:2000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 10 seconds"
    except FileNotFoundError:
        return f"Error: '{cmd[0]}' not found — is it installed and on PATH?"

def run_file(path):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    ext = p.suffix.lower()
    if ext in RUNNERS:
        return _run_cmd([c.replace("{file}", str(p)) for c in RUNNERS[ext]])
    if ext in {".c", ".cpp"}:
        compiler = "gcc" if ext == ".c" else "g++"
        out = p.with_suffix(".exe" if platform.system() == "Windows" else "")
        compile_out = _run_cmd([compiler, str(p), "-o", str(out)])
        if not out.exists():
            return f"Compile error:\n{compile_out}"
        run_out = _run_cmd([str(out)])
        try:
            out.unlink()
        except OSError:
            pass
        return run_out
    if ext == ".java":
        compile_out = _run_cmd(["javac", str(p)])
        if "error" in compile_out.lower():
            return f"Compile error:\n{compile_out}"
        run_out = _run_cmd(["java", "-cp", str(p.parent), p.stem])
        try:
            p.with_suffix(".class").unlink()
        except OSError:
            pass
        return run_out
    supported = ", ".join(list(RUNNERS) + [".c", ".cpp", ".java"])
    return f"Unsupported file type: '{ext}'. Supported: {supported}"

run_python = run_file

APP_REGISTRY = {
    "notepad": {"exe": "notepad.exe", "description": "Windows Notepad — opens .txt and other text files", "accepts_path": True, "accepts_url": False},
    "explorer": {"exe": "explorer.exe", "description": "Windows File Explorer — opens folder with the file selected and highlighted", "accepts_path": True, "accepts_url": False, "path_prefix": "/select,"},
}

def open_app(app_name, file_path=None, url=None):
    key = app_name.lower().strip()
    if key not in APP_REGISTRY:
        return f"App '{app_name}' is not permitted. Permitted apps: {', '.join(APP_REGISTRY)}"
    cfg = APP_REGISTRY[key]
    cmd = [cfg["exe"]]
    if file_path:
        if not cfg["accepts_path"]:
            return f"'{key}' does not accept file paths."
        p = safe_path(file_path)
        if not p.exists():
            return f"File not found: {file_path}"
        prefix = cfg.get("path_prefix", "")
        cmd.append(f"{prefix}{p}" if prefix else str(p))
    elif url:
        if not cfg["accepts_url"]:
            return f"'{key}' does not accept URLs."
        cmd.append(url)
    try:
        subprocess.Popen(cmd)
        target = file_path or url or ""
        return f"Opened {target} in {key}" if target else f"Launched {key}"
    except FileNotFoundError:
        return f"Could not find '{cfg['exe']}' — is it installed and on PATH?"
    except OSError as error:
        return f"Error launching {key}: {error}"
