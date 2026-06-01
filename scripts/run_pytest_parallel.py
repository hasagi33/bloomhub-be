from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def discover_test_files(repo_root: Path) -> list[str]:
    patterns = ("test_*.py", "*_test.py")
    files: list[str] = []
    for pattern in patterns:
        files.extend(
            str(path.relative_to(repo_root)) for path in repo_root.rglob(pattern)
        )
    return sorted(set(files))


def collect_test_counts(
    python_bin: str,
    repo_root: Path,
    pytest_args: list[str],
    targets: list[str],
) -> dict[str, int]:
    command = [
        python_bin,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        *pytest_args,
        *targets,
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)

    counts: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        nodeid = line.strip()
        if "::" not in nodeid:
            continue
        file_name = nodeid.split("::", 1)[0]
        counts[file_name] = counts.get(file_name, 0) + 1

    return counts


def shard_files(
    files: list[str], counts: dict[str, int], workers: int
) -> list[list[str]]:
    buckets: list[list[str]] = [[] for _ in range(workers)]
    bucket_loads = [0] * workers

    for file_name in sorted(files, key=lambda item: counts.get(item, 0), reverse=True):
        idx = min(range(workers), key=lambda i: bucket_loads[i])
        buckets[idx].append(file_name)
        bucket_loads[idx] += max(1, counts.get(file_name, 0))

    return [bucket for bucket in buckets if bucket]


def run_shard(
    *,
    python_bin: str,
    repo_root: Path,
    pytest_args: list[str],
    shard_index: int,
    shard_files: list[str],
) -> tuple[int, str, str]:
    command = [python_bin, "-m", "pytest", *pytest_args, *shard_files]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    header = (
        f"\n=== shard {shard_index + 1}: {len(shard_files)} file(s), "
        f"exit={completed.returncode} ===\n"
    )
    return completed.returncode, header + completed.stdout, completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run pytest shards in parallel by test file."
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=max(1, min(4, os.cpu_count() or 1)),
        help="Number of parallel workers to use.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Extra pytest arguments. Use -- before pytest selectors/options.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    python_bin = get_repo_python()
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]

    path_like_args = [
        arg
        for arg in pytest_args
        if not arg.startswith("-") and (Path(arg).exists() or "::" in arg)
    ]
    if path_like_args:
        command = [python_bin, "-m", "pytest", *pytest_args]
        completed = subprocess.run(command, cwd=repo_root)
        return completed.returncode

    test_files = discover_test_files(repo_root)
    if not test_files:
        print("No test files found.")
        return 0

    workers = max(1, min(args.workers, len(test_files)))
    counts = collect_test_counts(python_bin, repo_root, pytest_args, test_files)
    selected_files = [file_name for file_name in test_files if counts.get(file_name, 0)]
    if not selected_files:
        command = [python_bin, "-m", "pytest", *pytest_args, *test_files]
        completed = subprocess.run(command, cwd=repo_root)
        return completed.returncode

    shards = shard_files(selected_files, counts, workers)
    if len(shards) == 1:
        command = [python_bin, "-m", "pytest", *pytest_args, *shards[0]]
        completed = subprocess.run(command, cwd=repo_root)
        return completed.returncode

    exit_codes: list[int] = []
    with ThreadPoolExecutor(max_workers=len(shards)) as executor:
        futures = {
            executor.submit(
                run_shard,
                python_bin=python_bin,
                repo_root=repo_root,
                pytest_args=pytest_args,
                shard_index=index,
                shard_files=shard,
            ): index
            for index, shard in enumerate(shards)
        }

        for future in as_completed(futures):
            code, stdout, stderr = future.result()
            exit_codes.append(code)
            sys.stdout.write(stdout)
            if stderr:
                sys.stderr.write(stderr)

    if any(code != 0 for code in exit_codes):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
