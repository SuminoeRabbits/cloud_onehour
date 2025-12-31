#!/usr/bin/env python3
#
# pts_regression.py
#
# pts_regression.pyはtest_suite.jsonを入力としてPTS テストコマンドである
#
# ./pts_runner/pts_runner_<testname>.py <N>
#
# を生成します。Python3.10で動作します。
#
# 1. Test 生成について
# test_suite.jsonで "enabled": true, のテストのみを有効とします。
# "test_category"の層で"enabled": false, の場合、
# その配下のテストは全て無効となります。
# 2. <testname>の決定
# 有効なテストの"items"配下の"pts/<testname>"で開始するフィールドを
# <testname>とします。
# 3. <number>の決定
# <number>はtest_suite.jsonの有効なテストの"items"配下の
# "pts/<testname>"配下の
# "THFix_in_compile", "THChange_at_runtime", "TH_scaling"
# で決定されます。決定方法は以下の順番です。
#  if  "THFix_in_compile"==true
#       <number>=HardwareのCPU数、ie `nproc`
#  else
#    if "THChange_at_runtime"==true
#       <number> は何も指定してはいけない。
#    else
#       <number>=1
# 4. 実行コマンドの生成
# test_suite.jsonで1-3までが終了しすべての実行コマンドが生成されたら
# 一度デバッグの為に標準出力に出力します。
#

import json
import os
import subprocess
import sys
from pathlib import Path


def load_test_suite(suite_file="test_suite.json"):
    """
    Load and parse test_suite.json.

    Args:
        suite_file: Path to test_suite.json

    Returns:
        dict: Parsed JSON content
    """
    suite_path = Path(suite_file)
    if not suite_path.exists():
        print(f"[ERROR] Test suite file not found: {suite_file}")
        sys.exit(1)

    with open(suite_path, 'r') as f:
        return json.load(f)


def get_cpu_count():
    """
    Get the number of CPUs (equivalent to `nproc`).

    Returns:
        int: Number of CPUs
    """
    return os.cpu_count() or 1


def generate_test_commands(test_suite):
    """
    Generate test commands from test_suite.json.

    Args:
        test_suite: Parsed test_suite.json content

    Returns:
        list: List of command strings to execute
    """
    commands = []
    nproc = get_cpu_count()

    # Iterate through test categories
    test_categories = test_suite.get("test_category", {})

    for category_name, category_data in test_categories.items():
        # Check if category is enabled
        if not category_data.get("enabled", False):
            print(f"[INFO] Skipping disabled category: {category_name}")
            continue

        print(f"[INFO] Processing category: {category_name}")

        # Get items in this category
        items = category_data.get("items", {})

        for test_id, test_config in items.items():
            # Check if test is enabled
            if not test_config.get("enabled", False):
                print(f"  [INFO] Skipping disabled test: {test_id}")
                continue

            # Extract testname from "pts/<testname>" format
            if not test_id.startswith("pts/"):
                print(f"  [WARN] Invalid test ID format (expected pts/<testname>): {test_id}")
                continue

            testname = test_id[4:]  # Remove "pts/" prefix

            # Determine <number> argument based on thread configuration
            th_fix_in_compile = test_config.get("THFix_in_compile", False)
            th_change_at_runtime = test_config.get("THChange_at_runtime", False)

            if th_fix_in_compile:
                # Thread count fixed at compile time - use nproc
                number_arg = str(nproc)
                print(f"  [INFO] {testname}: THFix_in_compile=true, using {number_arg} threads")
            else:
                if th_change_at_runtime:
                    # Runtime thread configuration - no number argument
                    number_arg = None
                    print(f"  [INFO] {testname}: THChange_at_runtime=true, no thread argument (scaling mode)")
                else:
                    # Single thread mode
                    number_arg = "1"
                    print(f"  [INFO] {testname}: Single thread mode, using 1 thread")

            # Build command
            runner_script = f"./pts_runner/pts_runner_{testname}.py"

            if number_arg is None:
                cmd = runner_script
            else:
                cmd = f"{runner_script} {number_arg}"

            commands.append(cmd)
            print(f"  [OK] Generated command: {cmd}")

    return commands


def execute_commands(commands, dry_run=False):
    """
    Execute generated commands.

    Args:
        commands: List of command strings
        dry_run: If True, only print commands without executing

    Returns:
        int: Number of failed commands
    """
    if not commands:
        print("\n[WARN] No commands to execute")
        return 0

    print(f"\n{'='*80}")
    print(f"Generated {len(commands)} test command(s)")
    print(f"{'='*80}")
    for i, cmd in enumerate(commands, 1):
        print(f"{i}. {cmd}")
    print(f"{'='*80}\n")

    if dry_run:
        print("[INFO] Dry run mode - commands not executed")
        return 0

    failed = []

    for i, cmd in enumerate(commands, 1):
        print(f"\n{'='*80}")
        print(f"Executing command {i}/{len(commands)}: {cmd}")
        print(f"{'='*80}\n")

        result = subprocess.run(cmd, shell=True)

        if result.returncode != 0:
            print(f"\n[ERROR] Command failed with exit code {result.returncode}: {cmd}")
            failed.append(cmd)
        else:
            print(f"\n[OK] Command completed successfully: {cmd}")

    # Summary
    print(f"\n{'='*80}")
    print(f"Execution Summary")
    print(f"{'='*80}")
    print(f"Total commands: {len(commands)}")
    print(f"Successful: {len(commands) - len(failed)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print(f"\nFailed commands:")
        for cmd in failed:
            print(f"  - {cmd}")

    print(f"{'='*80}\n")

    return len(failed)


def main():
    """Main execution flow."""
    import argparse

    parser = argparse.ArgumentParser(
        description='PTS Regression Test Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Generate and execute all enabled tests
  %(prog)s --dry-run          # Show commands without executing
  %(prog)s --suite custom.json # Use custom test suite file
        """
    )

    parser.add_argument(
        '--suite',
        default='test_suite.json',
        help='Path to test suite JSON file (default: test_suite.json)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print commands without executing them'
    )

    args = parser.parse_args()

    print(f"{'='*80}")
    print(f"PTS Regression Test Runner")
    print(f"{'='*80}")
    print(f"Test suite: {args.suite}")
    print(f"CPU count: {get_cpu_count()}")
    print(f"Dry run: {args.dry_run}")
    print(f"{'='*80}\n")

    # Load test suite
    test_suite = load_test_suite(args.suite)

    # Generate commands
    commands = generate_test_commands(test_suite)

    # Execute commands
    failed_count = execute_commands(commands, dry_run=args.dry_run)

    # Exit with appropriate code
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
