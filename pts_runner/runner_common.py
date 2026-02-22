#!/usr/bin/env python3

import shutil
import subprocess
from pathlib import Path


def detect_pts_failure_from_log(log_file: Path) -> tuple[bool, str]:
    patterns = {
        "multiple tests are not installed": "PTS test profile is not installed",
        "the following tests failed": "PTS reported test execution failure",
        "quit with a non-zero exit status": "PTS benchmark subprocess failed",
        "failed to properly run": "PTS benchmark did not run properly",
    }

    try:
        if not log_file.exists():
            return False, ""
        content = log_file.read_text(errors="ignore").lower()
    except Exception:
        return False, ""

    for pattern, reason in patterns.items():
        if pattern in content:
            return True, reason

    return False, ""


def get_install_status(benchmark_full: str, benchmark: str) -> dict:
    info_installed = False
    test_installed_ok = False

    try:
        verify_result = subprocess.run(
            ["phoronix-test-suite", "info", benchmark_full],
            capture_output=True,
            text=True,
            check=False,
        )
        info_installed = verify_result.returncode == 0 and "Test Installed: Yes" in verify_result.stdout
    except Exception:
        info_installed = False

    try:
        test_installed_result = subprocess.run(
            ["phoronix-test-suite", "test-installed", benchmark_full],
            capture_output=True,
            text=True,
            check=False,
        )
        combined_output = (
            f"{test_installed_result.stdout}\n{test_installed_result.stderr}"
        ).lower()
        indicates_not_installed = "not installed" in combined_output
        looks_like_help_text = (
            "usage:" in combined_output
            or "available commands" in combined_output
            or "command" in combined_output and "not found" in combined_output
        )
        has_positive_install_signal = (
            benchmark_full.lower() in combined_output
            or "is installed" in combined_output
            or "already installed" in combined_output
            or "test installed: yes" in combined_output
        )
        test_installed_ok = (
            test_installed_result.returncode == 0
            and not indicates_not_installed
            and not looks_like_help_text
            and has_positive_install_signal
        )
    except Exception:
        test_installed_ok = False

    installed_dir_exists = (Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / benchmark).exists()
    already_installed = info_installed or test_installed_ok

    return {
        "info_installed": info_installed,
        "test_installed_ok": test_installed_ok,
        "installed_dir_exists": installed_dir_exists,
        "already_installed": already_installed,
    }


def cleanup_pts_artifacts(benchmark: str) -> None:
    """Remove installed test environment to free disk space.

    Call at the end of run(), AFTER export_results() and generate_summary().
    This ensures results are collected before removal.

    Removes:
        - ~/.phoronix-test-suite/installed-tests/pts/{benchmark}/
          (binary, extracted video files, compiled test suite etc.)
        - ~/.phoronix-test-suite/test-results/{benchmark}*threads/ entries
          (PTS internal results already exported to cloud_onehour/results/)

    Preserves:
        - ~/.phoronix-test-suite/download-cache/
          (shared across workloads; re-download is expensive)

    Errors are logged as [WARN] and do not affect caller's return value.
    """
    pts_home = Path.home() / ".phoronix-test-suite"

    # 1. Remove installed test environment
    installed_dir = pts_home / "installed-tests" / "pts" / benchmark
    if installed_dir.exists():
        try:
            shutil.rmtree(installed_dir)
            print(f"  [CLEAN] Removed installed test: {installed_dir}")
        except Exception as e:
            print(f"  [WARN] Failed to remove installed test: {e}")
    else:
        print(f"  [CLEAN] Installed test already absent: {installed_dir}")

    # 2. Remove PTS internal test-results (already exported to results_dir)
    sanitized = benchmark.replace(".", "")
    test_results_dir = pts_home / "test-results"
    if test_results_dir.exists():
        for result_dir in sorted(test_results_dir.iterdir()):
            name = result_dir.name
            if result_dir.is_dir() and (
                name.startswith(sanitized + "-") or name.startswith(benchmark + "-")
            ):
                try:
                    shutil.rmtree(result_dir)
                    print(f"  [CLEAN] Removed PTS result: {name}")
                except Exception as e:
                    print(f"  [WARN] Failed to remove PTS result {name}: {e}")

    print("  [CLEAN] Cleanup done (download-cache preserved)")