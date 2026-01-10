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
# --split-1st: 実行コマンドを総数の1/5に分割し、1番目の1/5のみを生成・実行します。
# --split-2nd: 実行コマンドを総数の1/5に分割し、2番目の1/5のみを生成・実行します。
# --split-3rd: 実行コマンドを総数の1/5に分割し、3番目の1/5のみを生成・実行します。
# --split-4th: 実行コマンドを総数の1/5に分割し、4番目の1/5のみを生成・実行します。
# --split-5th: 実行コマンドを総数の1/5に分割し、5番目の1/5のみを生成・実行します。
# --regression: このオプションが付いた場合 "exe_time_v8cpu"値により "--quick"を上書きします。
#                もし　"exe_time_v8cpu"値が120以上の場合は、実行しません、生成しません、表示されません。 
# 　　　　　　　　もし　"exe_time_v8cpu"値が15.25以上の場合は 必ず実行オプションに"--quick"を追加します。
#                もし"exe_time_v8cpu"値が15.25未満の場合は 必ず実行オプションに"--quick"を追加しません。   
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


def generate_test_commands(test_suite, max_threads=None, quick_mode=False, regression_mode=False):
    """
    Generate test commands from test_suite.json.

    Args:
        test_suite: Parsed test_suite.json content
        max_threads: If specified, override thread count to this value (except for single_threaded tests)
        quick_mode: If True, append --quick to all commands
        regression_mode: If True, override quick_mode based on exe_time_v8cpu value
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

            # Determine -number- argument based on thread configuration
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
            # Override to 288 for all cases except single_threaded (number_arg="1")
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
            
            # Determine if --quick should be appended and if test should be skipped
            use_quick = quick_mode
            skip_test = False

            if regression_mode:
                # Override quick_mode based on exe_time_v8cpu
                try:
                    exe_time = float(test_config.get("exe_time_v8cpu", "0.0"))
                except ValueError:
                    exe_time = 0.0

                # Check if exe_time >= 120: skip this test entirely
                if exe_time >= 120:
                    skip_test = True
                    print(f"  [INFO] Regression mode: exe_time_v8cpu={exe_time} >= 120 -> Skipping test (too long)")
                elif exe_time >= 15.25:
                    use_quick = True
                    print(f"  [INFO] Regression mode: exe_time_v8cpu={exe_time} >= 15.25 -> Enforcing --quick")
                else:
                    use_quick = False
                    print(f"  [INFO] Regression mode: exe_time_v8cpu={exe_time} < 15.25 -> Disabling --quick")

            # Skip if regression mode marked it as too long
            if skip_test:
                continue

            # Append --quick flag if enabled
            if use_quick:
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
  %(prog)s --split-1st --all       # Execute first 1/4 of tests only
  %(prog)s --split-2nd --all       # Execute 2nd 1/4 of tests only
  %(prog)s --split-3rd --all       # Execute 3rd 1/4 of tests only
  %(prog)s --split-4th --all       # Execute last 1/4 of tests only
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
        help='Execute only the first 1/4 of tests (for splitting long runs)'
    )

    parser.add_argument(
        '--split-2nd',
        action='store_true',
        help='Execute only the 2nd 1/4 of tests (for splitting long runs)'
    )

    parser.add_argument(
        '--split-3rd',
        action='store_true',
        help='Execute only the 3rd 1/5 of tests (for splitting long runs)'
    )

    parser.add_argument(
        '--split-4th',
        action='store_true',
        help='Execute only the 4th 1/5 of tests (for splitting long runs)'
    )

    parser.add_argument(
        '--split-5th',
        action='store_true',
        help='Execute only the last 1/5 of tests (for splitting long runs)'
    )

    parser.add_argument(
        '--regression',
        action='store_true',
        help='Regression mode: overrides --quick based on exe_time_v8cpu value (>=15.25 adds --quick)'
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
                split_2nd=False,
                split_3rd=False,
                split_4th=False,
                split_5th=False,
                regression=False
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
    # Generate commands
    commands = generate_test_commands(
        test_suite, 
        max_threads=max_threads, 
        quick_mode=args.quick,
        regression_mode=args.regression
    )

    # Apply split filter if requested
    split_flags = [args.split_1st, args.split_2nd, args.split_3rd, args.split_4th, args.split_5th]
    if sum(split_flags) > 1:
        print("[ERROR] Cannot use multiple split options at the same time")
        sys.exit(1)

    if any(split_flags):
        total_commands = len(commands)

        # Calculate split sizes for 5 chunks
        base_size = total_commands // 5
        remainder = total_commands % 5

        # Distribute remainder to first chunks
        # If rem=1: size+1, size, size, size, size
        # If rem=2: size+1, size+1, size, size, size
        # ...
        size1 = base_size + (1 if remainder > 0 else 0)
        size2 = base_size + (1 if remainder > 1 else 0)
        size3 = base_size + (1 if remainder > 2 else 0)
        size4 = base_size + (1 if remainder > 3 else 0)
        # size5 = base_size

        p1 = size1
        p2 = size1 + size2
        p3 = size1 + size2 + size3
        p4 = size1 + size2 + size3 + size4

        if args.split_1st:
            commands = commands[:p1]
            print(f"[INFO] Split mode: Executing 1st part (1-{p1} of {total_commands})")
        elif args.split_2nd:
            commands = commands[p1:p2]
            print(f"[INFO] Split mode: Executing 2nd part ({p1+1}-{p2} of {total_commands})")
        elif args.split_3rd:
            commands = commands[p2:p3]
            print(f"[INFO] Split mode: Executing 3rd part ({p2+1}-{p3} of {total_commands})")
        elif args.split_4th:
            commands = commands[p3:p4]
            print(f"[INFO] Split mode: Executing 4th part ({p3+1}-{p4} of {total_commands})")
        elif args.split_5th:
            commands = commands[p4:]
            print(f"[INFO] Split mode: Executing 5th part ({p4+1}-{total_commands} of {total_commands})")
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
