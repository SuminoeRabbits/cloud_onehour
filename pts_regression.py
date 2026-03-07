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
# 1. 動作環境と参照ファイル
# Python 3.10以降で動作します。
# 実行時に自身のスクリプトのSyntax Errorをチェックする機能 (py_compile等での自己診断) を備えます。
# 以下の3つのファイルのみを参照し、それ以外は参照しません。
# - ${PWD}/test_suite.json
# - ${PWD}/pts_runner/pts_runner_<testname>.py
# - ${PWD}/results/json_parser/json_parser_<testname>.py
#
# 2. 対象とするテストの抽出
# ${PWD}/test_suite.jsonに記載された"test_category"内で作業を行います。
# 同一の"test_category"内で"pts/<testname>"を見つけ、処理対象とします。
# 
# 3. 属性の付与: <test_length>
# <testname> の "exe_time_v8cpu" の値に応じて、以下の属性を付与します。
#  - 10.0以下の場合は "short"
#  - 20.0以下の場合は "middle"
#  - 100.0以下の場合は "long"
#  - 100.0より大きい場合は "very_long"
# ※この閾値設定は後日変更される可能性があるため、Look-Up-Table (LUT) で
#   定義しチューニング可能とします。
#
# 4. 属性の付与: <scaling>
# <testname> のパラメータに応じて、以下の属性を付与します。
#  - "THFix_in_compile": false かつ "THChange_at_runtime": true → "full"
#  - "THFix_in_compile": false かつ "THChange_at_runtime": false 
#    かつ "TH_scaling" に "single-threaded" (大文字小文字問わず) を含む → "single_th"
#  - "THFix_in_compile": true かつ "THChange_at_runtime": false → "max_th"
# ※これらの条件で分類できない <testname> が発生した場合は、エラーを出力してスクリプトを停止します。
# ※この条件設定もLUTで定義しチューニング可能とします。
#
# 5. 実行オプション (exec_opt) の決定
# pts_runner_<testname>.py を実行する際のオプション "exec_opt" を定義します。
# 
# まず、scaling に応じてベースとなる文字列 (base_opt) を決定します。
# - "full" → "" (何も指定しない)
# - "single_th" → "1"
# - "max_th" → "288"
# 
# 次に、test_length との組み合わせで exec_opt を決定します。
# Host processorのvcpu数(nproc)を N とします。
# (1) test_length = "short" の場合
#     exec_opt = base_opt
# (2) test_length = "middle" の場合
#     exec_opt = base_opt に "--quick" を付加
# (3) test_length = "long" または "very_long" の場合
#     - scaling が "single_th" または "max_th" の場合 (例外ルール)
#       exec_opt = base_opt (このルールを優先)
#     - scaling が "full" の場合
#       以下の3通りの exec_opt を生成し、３つの実行コマンドを作成します。
#         - "N/4*2 --quick"
#         - "N/4*3 --quick"
#         - "288 --quick"
#
# 6. スクリプトの実行オプション
# - `--testcategory` (省略可能)
#     省略時は "Full" としてすべてのカテゴリを実行対象とする。
#     リスト形式で指定可能で、指定されたカテゴリ(例: "Multimedia", "Processor")のみを対象とする。
# - `--test_length` (省略可能)
#     省略時は "Full" としてすべてのtest_lengthを実行対象とする。
#     リスト形式で指定可能（"short", "middle", "long", "very_long"）。
#     また、`--short`, `--middle`, `--long`, `--very_long` のようにフラグとして直接指定も可能とする。
# - `--dry_run` (省略可能)
#     実行内容（コマンド列）を標準出力にプリントする。デフォルトの動作でありデバッグ用。
# - `--run` (省略可能)
#     省略された場合は上記の dry_run モードが優先される。
#     指定された場合は、プリントするだけでなくターミナルで実際にコマンド群を実行する。
#
# 7. 実行の順序
# 実行（オプション生成も含む）は以下の順序で行う。
# - <testcategory> ごとに順次実行する。カテゴリの順番は `test_suite.json` に記載された順序を死守。
# - カテゴリ内の <testname> の順番は、test_length を基準にして `short` -> `middle` -> `long` -> `very_long` の順になるようにソート。
#
# 8. 実行コマンドの出力フォーマット
# 生成される実行コマンドは、そのままコピー＆ペーストしてリモート環境などでも確実に実行できるよう、
# 以下のフォーマットとする。
# 
#   cd ~/cloud_onehour && git pull && ./pts_runner/pts_runner_<testname>.py <exec_opt> > /tmp/pts_runner_<testname>.log 2>&1
#
# ※ `long` や `very_long` など、1つの <testname> から複数（4つなど）のコマンドが生成される場合：
#   最初の1つ目は `>` (新規作成/上書き) とし、2つ目以降のコマンドに対しては `>>` (追記) とすることで、
#   同一のログファイル (`/tmp/pts_runner_<testname>.log`) に結果が蓄積されるようにする。
#
# 9. 実行時の json_parser との整合性チェック
# テストを実行（コマンド生成）する前に、対応する `results/json_parser/json_parser_<testname>.py` が
# 存在するかを必ずチェックする。
# 1つ間違っていただけで即死させず、実行予定のすべての `<testname>` について存在チェックを行い、
# 不足しているパーサーがあればまとめて Critical Error として警告文を表示し、コマンド生成を中止（スクリプト停止）する。
#
# 10. 例外処理（エラーハンドリング）
# - `test_suite.json` が存在しない場合はエラーメッセージを出力して終了する。
# - `test_suite.json` に文法エラー (JSONパースエラー) があった場合、単に落ちるのではなく、
#   どの行・どの文字付近でエラーが発生しているかを標準出力にプリントしてから停止させる。
# - CLI引数 `--testcategory` や `--test_length` でタイポなどにより存在しない値が指定された場合、
#   「そのようなカテゴリ(または属性)は存在しません」と親切に警告し、さらに
#   「もしかして: <正しい候補> ですか？」といった類似の候補 (Spell Suggestion / did you mean) を出し、
#   実行前にスクリプトを停止する。
# - `pts_runner_<testname>.py` が存在しない場合、手順9の `json_parser` チェックと同様に、
#   実行予定の全テストを対象に一括で存在チェックを行い、不足があれば Critical Error としてまとめてスクリプトを停止する。 

import argparse
import json
import os
import sys
import difflib
import py_compile
import subprocess
from pathlib import Path

# 1. 動作環境と参照ファイル: 自身の文法チェック
try:
    py_compile.compile(str(Path(__file__).resolve()), doraise=True)
except py_compile.PyCompileError as e:
    print(f"[CRITICAL ERROR] Syntax error in this script ({Path(__file__).name}):\n{e}", file=sys.stderr)
    sys.exit(1)

# --- Look-Up-Table (LUT) ---

def get_test_length(exe_time: float) -> str:
    if exe_time <= 10.0:
        return "short"
    if exe_time <= 20.0:
        return "middle"
    if exe_time <= 100.0:
        return "long"
    return "very_long"

def get_scaling(th_fix: bool, th_change: bool, th_scaling: str) -> str:
    # "THFix_in_compile": false かつ "THChange_at_runtime": true → "full"
    if not th_fix and th_change:
        return "full"
    # "THFix_in_compile": false かつ "THChange_at_runtime": false かつ "TH_scaling" に "single-threaded" を含む → "single_th"
    if not th_fix and not th_change and "single-threaded" in str(th_scaling).lower():
        return "single_th"
    # "THFix_in_compile": true かつ "THChange_at_runtime": false → "max_th"
    if th_fix and not th_change:
        return "max_th"
    
    # 該当しない場合はエラーとするために None を返す
    return None

# --- Helper Functions ---

def load_test_suite(suite_path: Path):
    if not suite_path.exists():
        print(f"[ERROR] Test suite file not found: {suite_path}", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(suite_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[CRITICAL ERROR] Failed to parse JSON in {suite_path}.", file=sys.stderr)
        print(f"Error at line {e.lineno}, column {e.colno}:\n{e.msg}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[CRITICAL ERROR] Could not read {suite_path}: {e}", file=sys.stderr)
        sys.exit(1)

def check_args_typo(provided_list, valid_list, arg_name):
    # 'Full' は特殊な ALL 指定フラグとみなすためスキップ
    if "Full" in provided_list:
        return
        
    for item in provided_list:
        if item not in valid_list:
            matches = difflib.get_close_matches(item, valid_list, n=1, cutoff=0.5)
            suggestion = f" もしかして: '{matches[0]}' ですか？" if matches else ""
            
            # 複数指定の区切り文字間違い（カンマ使用）に対するヒント
            list_hint = ""
            if "," in item:
                list_hint = f"\n  [ヒント] 複数指定する場合はカンマ(,)ではなくスペース(空欄)で区切ってください。\n  (例: {arg_name} {item.replace(',', ' ')})"
                
            print(f"[ERROR] {arg_name} に '{item}' という値(カテゴリ/属性)は存在しません。{suggestion}{list_hint}", file=sys.stderr)
            sys.exit(1)

def generate_commands(testname: str, test_length: str, scaling: str) -> list[str]:
    nproc = os.cpu_count() or 1
    
    # ベースの文字列 (base_opt) を決定
    if scaling == "full":
        base_opt = ""
    elif scaling == "single_th":
        base_opt = "1"
    elif scaling == "max_th":
        base_opt = "288"
    else:
        base_opt = ""
        
    # test_length に組み合わせて exec_opt のリストを構築
    opts = []
    if scaling in ("single_th", "max_th"):
        if test_length == "short":
            opts = [base_opt]
        elif test_length == "middle":
            opts = [f"{base_opt} --quick" if base_opt else "--quick"]
        else: # long, very_long の場合はscaling優先 (1コマンドのみ)
            opts = [base_opt]
    else: # scaling == "full"
        if test_length == "short":
            opts = [base_opt]
        elif test_length == "middle":
            opts = ["--quick"]
        else: # long, very_long の場合は 3パターンのコマンドを生成
            n_div_4_2 = max(1, (nproc * 2) // 4)
            n_div_4_3 = max(1, (nproc * 3) // 4)
            opts = [
                f"{n_div_4_2} --quick",
                f"{n_div_4_3} --quick",
                "288 --quick"
            ]

    # 余分な空白を削除
    opts = [o.strip() for o in opts]
    
    # 実行用のフォーマットに組み立て
    commands = []
    base_dir_str = str(Path(__file__).resolve().parent)
    base_cmd = f"cd {base_dir_str} && git pull && ./pts_runner/pts_runner_{testname}.py"
    log_file = f"/tmp/pts_runner_{testname}.log"
    
    for idx, opt in enumerate(opts):
        c_opt = f" {opt}" if opt else ""
        redirect = ">" if idx == 0 else ">>"
        cmd = f"{base_cmd}{c_opt} {redirect} {log_file} 2>&1"
        commands.append(cmd)
        
    return commands

def main():
    parser = argparse.ArgumentParser(description="PTS Regression Command Generator")
    parser.add_argument("--testcategory", nargs="*", default=["Full"], help="Target test categories (e.g. Multimedia)")
    parser.add_argument("--test_length", nargs="*", default=["Full"], help="Target test lengths (short, middle, long, very_long)")
    
    # ショートカットフラグ
    parser.add_argument("--short", action="store_true", help="Shortcut for --test_length short")
    parser.add_argument("--middle", action="store_true", help="Shortcut for --test_length middle")
    parser.add_argument("--long", action="store_true", help="Shortcut for --test_length long")
    parser.add_argument("--very_long", action="store_true", help="Shortcut for --test_length very_long")
    
    # 実行モード
    parser.add_argument("--dry_run", action="store_true", help="Print commands without executing (Default)")
    parser.add_argument("--run", action="store_true", help="Execute generated commands (overrides dry_run)")
    parser.add_argument("--regression", action="store_true", help="Output command to run pts_regression.py itself")
    parser.add_argument("-v", "--verbose", action="store_true", help="Outputs explicitly expanded arguments in --regression mode")
    
    args = parser.parse_args()
    
    # 指定された test_length をまとめる
    selected_lengths = set(args.test_length)
    if args.short: selected_lengths.add("short")
    if args.middle: selected_lengths.add("middle")
    if args.long: selected_lengths.add("long")
    if args.very_long: selected_lengths.add("very_long")
    
    if "Full" in selected_lengths and len(selected_lengths) > 1:
        # Fullと個別指定が混ざっている場合はFullを解除
        selected_lengths.remove("Full")
        
    VALID_LENGTHS = ["short", "middle", "long", "very_long"]
    check_args_typo(list(selected_lengths), VALID_LENGTHS, "--test_length")
    
    # JSONの読み込み・パース
    base_dir = Path(__file__).resolve().parent
    suite_path = base_dir / "test_suite.json"
    suite_data = load_test_suite(suite_path)
    
    valid_categories = list(suite_data.get("test_category", {}).keys())
    check_args_typo(args.testcategory, valid_categories, "--testcategory")

    # --regression フラグが指定された場合は、自身を実行するコマンドを標準出力して終了する
    if args.regression:
        base_dir_str = str(Path(__file__).resolve().parent)
        
        if args.verbose:
            # -v が指定された場合、省略されたオプションを明示的に展開して表示
            final_opts = []
            
            # testcategory の展開
            if "Full" in args.testcategory:
                cat_cmd = " ".join([f'"{c}"' if ' ' in c else c for c in valid_categories])
                final_opts.append(f"--testcategory {cat_cmd}")
            else:
                cat_cmd = " ".join([f'"{c}"' if ' ' in c else c for c in args.testcategory])
                final_opts.append(f"--testcategory {cat_cmd}")
                
            # test_length の展開
            if "Full" in selected_lengths and not (args.short or args.middle or args.long or args.very_long):
                final_opts.append("--short --middle --long --very_long")
            else:
                len_flags = []
                if "short" in selected_lengths: len_flags.append("--short")
                if "middle" in selected_lengths: len_flags.append("--middle")
                if "long" in selected_lengths: len_flags.append("--long")
                if "very_long" in selected_lengths: len_flags.append("--very_long")
                if len_flags:
                    final_opts.append(" ".join(len_flags))
            
            if args.run: final_opts.append("--run")
            if args.dry_run: final_opts.append("--dry_run")
            
            opts_str = " ".join(final_opts)
        else:
            # 通常は入力されたコマンド引数から --regression を取り除くだけ
            opts = [arg for arg in sys.argv[1:] if arg != "--regression"]
            opts_str = " ".join([f'"{opt}"' if ' ' in opt else opt for opt in opts])
            
        cmd_suffix = f" {opts_str}" if opts_str else ""
        print(f"cd {base_dir_str} && ./pts_regression.py{cmd_suffix}")
        sys.exit(0)
    
    # カテゴリ・テストの抽出と属性の付与
    test_plan = []
    
    for cat_name, cat_data in suite_data.get("test_category", {}).items():
        if "Full" not in args.testcategory and cat_name not in args.testcategory:
            continue
            
        items = cat_data.get("items", {})
        for item_key, attrs in items.items():
            if not item_key.startswith("pts/"):
                continue
            testname = item_key[4:] # remove "pts/"
            
            # exe_time 取得
            try:
                exe_time = float(attrs.get("exe_time_v8cpu", 0.0))
            except ValueError:
                exe_time = 0.0
                
            t_len = get_test_length(exe_time)
            
            # test_length でのフィルタリング (ここで弾けば、scalingエラー等のチェックをスキップできる)
            if "Full" not in selected_lengths and t_len not in selected_lengths:
                continue
            
            # scaling 取得
            t_scaling = get_scaling(
                attrs.get("THFix_in_compile", False),
                attrs.get("THChange_at_runtime", False),
                attrs.get("TH_scaling", "")
            )
            
            # scaling がどれにも当てはまらない場合はエラーで停止する (手順4)
            if t_scaling is None:
                print(f"[CRITICAL ERROR] Cannot classify test '{testname}' scaling properties into attributes.", file=sys.stderr)
                print(f"  THFix_in_compile={attrs.get('THFix_in_compile')} / THChange_at_runtime={attrs.get('THChange_at_runtime')} / TH_scaling='{attrs.get('TH_scaling')}'", file=sys.stderr)
                sys.exit(1)
                
            test_plan.append({
                "category": cat_name,
                "testname": testname,
                "length": t_len,
                "scaling": t_scaling
            })

    # カテゴリごとにソート (test_suite.jsonの記載順=valid_categoriesの順を維持しつつ、length順にソート)
    length_order = {"short": 0, "middle": 1, "long": 2, "very_long": 3}
    ordered_plan = []
    
    for cat in valid_categories: 
        cat_tests = [t for t in test_plan if t["category"] == cat]
        cat_tests.sort(key=lambda x: length_order[x["length"]])
        ordered_plan.extend(cat_tests)
        
    if not ordered_plan:
        print("[INFO] No tests matched the given criteria.")
        print("\n[Help] Available Test Categories (--testcategory):")
        for cat in valid_categories:
            print(f"  - {cat}")
        sys.exit(0)

    # 実行前のパーサーおよびランナー存在チェック (手順9, 10)
    missing_files = []
    base_dir = Path(__file__).resolve().parent
    for t in ordered_plan:
        tname = t["testname"]
        runner = base_dir / "pts_runner" / f"pts_runner_{tname}.py"
        parser_s = base_dir / "results" / "json_parser" / f"json_parser_{tname}.py"
        
        if not runner.exists():
            missing_files.append(f"[Missing] Runner Script: {runner.relative_to(base_dir)}")
        if not parser_s.exists():
            missing_files.append(f"[Missing] JSON Parser  : {parser_s.relative_to(base_dir)}")
            
    if missing_files:
        print("[CRITICAL ERROR] The following required files are missing. Test execution aborted.", file=sys.stderr)
        for m in missing_files:
            print(f"  - {m}", file=sys.stderr)
        sys.exit(1)

    # コマンド生成
    all_commands = []
    base_dir_str = str(Path(__file__).resolve().parent)
    first_clean_cmd = f"cd {base_dir_str} && git pull && rm -rf ~/.phoronix-test-suite/installed-tests/pts/* && df -h / > /tmp/pts_cache_clean.log 2>&1"
    append_clean_cmd = f"cd {base_dir_str} && git pull && rm -rf ~/.phoronix-test-suite/installed-tests/pts/* && df -h / >> /tmp/pts_cache_clean.log 2>&1"
    
    # 実行予定のカテゴリ一覧を（順序を維持したまま）抽出
    categories_in_plan = []
    for t in ordered_plan:
        if t["category"] not in categories_in_plan:
            categories_in_plan.append(t["category"])
            
    # 必ず1行目に全消去＋ログ新規作成コマンドを挿入
    all_commands.append(first_clean_cmd)
    
    for idx, cat in enumerate(categories_in_plan):
        all_commands.append(f"\n# {'='*60}")
        all_commands.append(f"# Category: {cat}")
        all_commands.append(f"# {'='*60}")
        
        cat_tests = [t for t in ordered_plan if t["category"] == cat]
        for t in cat_tests:
            cmds = generate_commands(t["testname"], t["length"], t["scaling"])
            all_commands.extend(cmds)
            
        # 複数カテゴリが存在する場合、1つのカテゴリ終了後に全消去＋ログ追記コマンドを挿入 (最後は不要)
        if len(categories_in_plan) > 1 and idx < len(categories_in_plan) - 1:
            all_commands.append(append_clean_cmd)
        
    # dry run もしくは 実行 (手順6)
    should_run = args.run # --run が指定されていれば run, そうでなければ dry_run
    
    if should_run:
        print(f"=== Starting execution of {len(all_commands)} commands ===")
        for i, cmd in enumerate(all_commands, 1):
            print(f"[{i}/{len(all_commands)}] Executing: {cmd}")
            # subprocess.runで実際にコマンドを実行
            res = subprocess.run(cmd, shell=True)
            if res.returncode != 0:
                print(f"[WARN] Command returned non-zero exit code: {res.returncode}")
        print("=== Execution Complete ===")
    else:
        # dry_run (デフォルト)
        print("=== DRY RUN (Generated Commands) ===")
        for cmd in all_commands:
            print(cmd)
            
        # Generate Summary Table
        print("\n=== Summary of Selected Tests ===")
        print(f"{'Category':<25} | {'Short':<7} | {'Middle':<7} | {'Long':<7} | {'Very Long':<10} | {'Total'}")
        print("-" * 75)
        for cat in categories_in_plan:
            cat_tests = [t for t in ordered_plan if t["category"] == cat]
            c_short = sum(1 for t in cat_tests if t["length"] == "short")
            c_mid = sum(1 for t in cat_tests if t["length"] == "middle")
            c_long = sum(1 for t in cat_tests if t["length"] == "long")
            c_vlong = sum(1 for t in cat_tests if t["length"] == "very_long")
            c_total = len(cat_tests)
            print(f"{cat:<25} | {c_short:<7} | {c_mid:<7} | {c_long:<7} | {c_vlong:<10} | {c_total}")
        print("-" * 75)
        print(f"{'TOTAL':<25} | "
              f"{sum(1 for t in ordered_plan if t['length'] == 'short'):<7} | "
              f"{sum(1 for t in ordered_plan if t['length'] == 'middle'):<7} | "
              f"{sum(1 for t in ordered_plan if t['length'] == 'long'):<7} | "
              f"{sum(1 for t in ordered_plan if t['length'] == 'very_long'):<10} | "
              f"{len(ordered_plan)}")
        
        actual_cmds = [c for c in all_commands if not c.strip().startswith('#') and c.strip()]
        print(f"\n[INFO] Total {len(actual_cmds)} commands generated.")
        print("[INFO] Note: Use --run flag to execute these commands.\n")

if __name__ == "__main__":
    main()
