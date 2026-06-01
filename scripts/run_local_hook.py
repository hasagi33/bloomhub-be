from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def get_repo_python() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return sys.executable


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: run_local_hook.py <hook-name> [args...]")
        return 2

    hook_name = sys.argv[1]
    extra_args = sys.argv[2:]
    repo_root = Path(__file__).resolve().parents[1]
    python_bin = get_repo_python()

    if hook_name == "gitstream-cm-validate":
        command = [
            python_bin,
            str(repo_root / "scripts" / "validate_gitstream_cm.py"),
            *extra_args,
        ]
    elif hook_name == "spectacular-schema":
        command = [
            python_bin,
            str(repo_root / "manage.py"),
            "spectacular",
            "--file",
            "schema.yaml",
        ]
    elif hook_name == "pytest":
        command = [python_bin, "-m", "pytest"]
    elif hook_name == "pytest-parallel":
        command = [python_bin, str(repo_root / "scripts" / "run_pytest_parallel.py")]
    elif hook_name == "commit-message-bhb":
        if not extra_args:
            print("Commit message file is required.")
            return 2

        commit_msg_file = Path(extra_args[0])
        first_line = (
            commit_msg_file.read_text(encoding="utf-8").splitlines()[0]
            if commit_msg_file.exists()
            else ""
        )
        if not re.match(r"^\[BHB-[0-9]+\] ", first_line):
            print("Commit message must match [BHB-XX] description")
            return 1

        return 0
    else:
        print(f"Unknown hook: {hook_name}")
        return 2

    completed = subprocess.run(command, cwd=repo_root)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
