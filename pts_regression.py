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
#
# 2. <testname>の決定
# 有効なテストの"items"配下の"pts/<testname>"で開始するフィールドを
# <testname>とします。
#
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
#
# 4. 有効なrunnerスクリプトが存在するかの確認   
# test_suite.jsonでは有効なテストがpts_runner/pts_runner_<testname>.py 
# として存在しない場合は、警告を出してください。
# ただし、コマンドの生成は続行してください。
#
# 5. 実行コマンドの生成
# test_suite.jsonで1-4までが終了しすべての実行コマンドが生成されたら
# 一度デバッグの為に標準出力に出力します。この出力はそのままターミナルに
# コピー＆ペーストして実行できる形式にしてください。
#
# 6. 実行コマンドの実行（実行オプション--all の場合のみ実施）
# 5.で生成したコマンドを実際に実行します。
#
# 7. 実行オプションの追加
# --max : 3.で<number>が決定されますが、それをすべて288と変更します。
#         <number>が空白の場合でも、288と変更します。
#　　　　　ただし<number>=1が指定されている場合は、１のまま変更しません。
# --help: オプション一覧を表示します。
# --quick: 実行コマンドの生成で、すべてのコマンドの末尾に --quickを追加します。
#          それ以外の機能は持たせません。
# --dry-run: 実行コマンドの生成のみを行い、実行しません。
# --no-execute: 実行コマンドの生成のみを行い、実行しません。
# --split-1st: 実行コマンドを総数の半分に分割し、前半分のみを生成・実行します。
# --split-2nd: 実行コマンドを総数の半分に分割し、後半分のみを生成・実行します。  
# 
# 例外処理：
# - もし実行オプションが不正な場合は、それを無視して動作を続行する。  
# - test_suite.jsonが存在しない場合は、エラーメッセージを出力して終了する。
# - pts_runner_<testname>.pyが存在しない場合は、警告メッセージを出力して
#   動作を続行する。
# 

import json
import os
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


def generate_test_commands(test_suite, max_threads=None, quick_mode=False):
    """
    Generate test commands from test_suite.json.

    Args:
        test_suite: Parsed test_suite.json content
        max_threads: If specified, override thread count to this value (except for single-threaded tests)
        quick_mode: If True, append --quick to all commands

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

            # Apply --max override if specified
            # Override to 288 for all cases except single-threaded (number_arg="1")
            if max_threads is not None:
                if number_arg == "1":
                    # Single-threaded: keep as 1
                    print(f"  [INFO] --max: Single-threaded test, keeping 1 thread")
                elif number_arg is None:
                    # Scaling mode: override to 288
                    number_arg = str(max_threads)
                    print(f"  [INFO] --max override: scaling mode -> {number_arg} threads")
                else:
                    # Fixed thread count: override to 288
                    original_arg = number_arg
                    number_arg = str(max_threads)
                    print(f"  [INFO] --max override: {original_arg} -> {number_arg} threads")

            # Build command
            runner_script = f"./pts_runner/pts_runner_{testname}.py"

            # Check if runner script exists (requirement #4)
            runner_path = Path(runner_script)
            if not runner_path.exists():
                print(f"  [WARN] Runner script not found: {runner_script}")
                print(f"  [INFO] Command will be generated anyway, but execution may fail")

            if number_arg is None:
                cmd = runner_script
            else:
                cmd = f"{runner_script} {number_arg}"
            
            # Append --quick flag if quick mode enabled
            if quick_mode:
                cmd = f"{cmd} --quick"

            commands.append(cmd)
            print(f"  [OK] Generated command: {cmd}")

    return commands



def print_commands(commands):
    """
    Print generated commands to stdout in copy-pasteable format.

    Args:
        commands: List of command strings
    """
    if not commands:
        print("\n[WARN] No commands to generate")
        return

    print(f"\n{'='*80}")
    print(f"Generated {len(commands)} test command(s) - Copy & Paste Ready")
    print(f"{'='*80}")
    for cmd in commands:
        print(cmd)
    print(f"{'='*80}\n")


def execute_commands(commands):
    """
    Execute the generated commands sequentially.

    Args:
        commands: List of command strings to execute

    Returns:
        int: Number of failed commands
    """
    import subprocess

    if not commands:
        print("[WARN] No commands to execute")
        return 0

    print(f"\n{'='*80}")
    print(f"Executing {len(commands)} test command(s)")
    print(f"{'='*80}\n")

    failed_count = 0
    for i, cmd in enumerate(commands, 1):
        print(f"[{i}/{len(commands)}] Executing: {cmd}")
        print(f"{'-'*80}")

        try:
            # Execute command and capture output
            result = subprocess.run(
                cmd,
                shell=True,
                check=False,
                text=True,
                capture_output=False  # Show output in real-time
            )

            if result.returncode == 0:
                print(f"[OK] Command completed successfully")
            else:
                print(f"[ERROR] Command failed with exit code {result.returncode}")
                failed_count += 1

        except Exception as e:
            print(f"[ERROR] Exception occurred: {e}")
            failed_count += 1

        print(f"{'-'*80}\n")

    # Summary
    print(f"{'='*80}")
    print(f"Execution Summary")
    print(f"{'='*80}")
    print(f"Total commands: {len(commands)}")
    print(f"Successful: {len(commands) - failed_count}")
    print(f"Failed: {failed_count}")
    print(f"{'='*80}\n")

    return failed_count


def main():
    """Main execution flow."""
    import argparse

    parser = argparse.ArgumentParser(
        description='PTS Regression Test Command Generator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                         # Generate commands (dry-run mode)
  %(prog)s --all                   # Generate and execute all commands
  %(prog)s --max                   # Override thread count to 288 (except single-threaded)
  %(prog)s --max --all             # Override to 288 threads and execute
  %(prog)s --suite custom.json     # Use custom test suite file
  %(prog)s --dry-run               # Generate commands but don't execute (default)
  %(prog)s --split-1st --all       # Execute first half of tests only
  %(prog)s --split-2nd --all       # Execute second half of tests only
        """
    )

    parser.add_argument(
        '--suite',
        default='test_suite.json',
        help='Path to test suite JSON file (default: test_suite.json)'
    )

    parser.add_argument(
        '--all',
        action='store_true',
        help='Execute generated commands (default: dry-run mode)'
    )

    parser.add_argument(
        '--max',
        action='store_true',
        help='Override thread count to 288 (except for single-threaded tests)'
    )

    parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick mode: append --quick to all commands for development'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Generate commands but do not execute them (default behavior)'
    )

    parser.add_argument(
        '--no-execute',
        action='store_true',
        help='Generate commands but do not execute them (alias for --dry-run)'
    )

    parser.add_argument(
        '--split-1st',
        action='store_true',
        help='Execute only the first half of tests (for splitting long runs)'
    )

    parser.add_argument(
        '--split-2nd',
        action='store_true',
        help='Execute only the second half of tests (for splitting long runs)'
    )

    # Exception handling: Ignore invalid options and continue execution
    try:
        args = parser.parse_args()
    except SystemExit as e:
        # If argparse fails due to invalid options, print warning and use defaults
        if e.code != 0:
            print("[WARN] Invalid command line options detected. Using default settings.")
            print("[INFO] Run with --help to see available options.\n")
            # Create default args namespace
            args = argparse.Namespace(
                suite='test_suite.json',
                all=False,
                max=False,
                quick=False,
                dry_run=True,
                no_execute=False,
                split_1st=False,
                split_2nd=False
            )
        else:
            # Normal exit (--help was called)
            sys.exit(0)

    # Determine if we should execute
    # Execution requires --all flag explicitly
    should_execute = args.all and not (args.dry_run or args.no_execute)

    # Determine max_threads
    max_threads = 288 if args.max else None

    print(f"{'='*80}")
    print(f"PTS Regression Test Command Generator")
    print(f"{'='*80}")
    print(f"Test suite: {args.suite}")
    print(f"CPU count: {get_cpu_count()}")
    if max_threads:
        print(f"Thread override: {max_threads} (--max enabled)")
    print(f"Execution mode: {'Execute' if should_execute else 'Dry-run (no execution)'}")
    print(f"{'='*80}\n")

    # Load test suite
    test_suite = load_test_suite(args.suite)

    # Generate commands
    commands = generate_test_commands(test_suite, max_threads=max_threads, quick_mode=args.quick)

    # Apply split filter if requested
    if args.split_1st and args.split_2nd:
        print("[ERROR] Cannot use both --split-1st and --split-2nd at the same time")
        sys.exit(1)

    if args.split_1st or args.split_2nd:
        total_commands = len(commands)
        split_point = (total_commands + 1) // 2  # Round up for first half

        if args.split_1st:
            commands = commands[:split_point]
            print(f"[INFO] Split mode: Executing first half (1-{split_point} of {total_commands})")
        else:  # args.split_2nd
            commands = commands[split_point:]
            print(f"[INFO] Split mode: Executing second half ({split_point+1}-{total_commands} of {total_commands})")
        print()

    # Print commands
    print_commands(commands)

    # Execute commands (step 6)
    if should_execute:
        failed_count = execute_commands(commands)
        sys.exit(1 if failed_count > 0 else 0)
    else:
        print("[INFO] Dry-run mode: Commands not executed")
        print("[INFO] To execute commands, run with --all flag\n")



if __name__ == "__main__":
    main()
