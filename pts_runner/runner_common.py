#!/usr/bin/env python3

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