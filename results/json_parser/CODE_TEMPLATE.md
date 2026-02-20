# json_parser スクリプト テンプレート仕様書

新しいベンチマーク向け `json_parser_<benchmark>.py` を作成する際の設計ガイド。

## TOC
- [スクリプト要件](#スクリプト要件)
- [ファイル命名規則](#ファイル命名規則)
- [全体構造](#全体構造)
- [共通部分（コピー可能）](#共通部分コピー可能)
- [ベンチマーク固有部分（要実装）](#ベンチマーク固有部分要実装)
- [データソースパターン別の実装ガイド](#データソースパターン別の実装ガイド)
  - [パターンA: ログのみ・単一テスト](#パターンa-ログのみ単一テスト)
  - [パターンB: ログのみ・複数テスト](#パターンb-ログのみ複数テスト)
  - [パターンC: ログ + JSON 併用](#パターンc-ログ--json-併用)
  - [パターンD: ビルド系（値=時間）](#パターンd-ビルド系値時間)
- [test_name キーの命名規則](#test_name-キーの命名規則)
- [time / test_run_times / cost の決定ルール](#time--test_run_times--cost-の決定ルール)
- [出力JSON構造](#出力json構造)
- [チェックリスト](#チェックリスト)
- [既存スクリプト一覧](#既存スクリプト一覧)

---

## スクリプト要件

1. **Python 3.10 で動作すること**
   - Python 3.10 で利用可能な標準ライブラリのみを使用する
   - 3.11 以降の構文（`ExceptionGroup`, `match` 文の高度なパターン等）は使用しない
   - 型ヒントは `from __future__ import annotations` で対応する

2. **実行時に自身の Syntax Error チェックを行うこと**
   - スクリプト起動時に `py_compile.compile()` で自身のソースを検証する
   - Syntax Error が検出された場合はエラーメッセージを出力して即座に終了する
   - 以下のコードを `main()` の先頭に追加する:

```python
import py_compile

def main() -> None:
    # Self syntax check
    try:
        py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    except py_compile.PyCompileError as e:
        print(f"Syntax error in {Path(__file__).name}: {e}", file=sys.stderr)
        sys.exit(1)

    # ... 以降の通常処理 ...
```

---

## ファイル命名規則

```
json_parser_<benchmark>.py
```
`<benchmark>` は `README_results.md` で定義されるベンチマークディレクトリ名と一致させる。
例: `json_parser_apache-3.0.0.py`, `json_parser_compress-7zip-1.12.0.py`

---

## 全体構造

スクリプトは以下の6ブロックで構成される。

```
[1] 共通インポート・初期化     ← コピーで済む
[2] ベンチマーク定数・正規表現  ← ベンチマーク毎に定義
[3] 共通ヘルパー関数           ← コピーで済む
[4] ベンチマーク固有抽出        ← ベンチマーク毎に実装（★コア）
[5] 共通ディレクトリ走査        ← コピーで済む
[6] 共通CLIエントリポイント     ← コピーで済む
```

処理フロー:
```
main()
  └─ _build_full_payload(search_root)
       ├─ glob で **/<BENCHMARK_NAME> ディレクトリを再帰検索
       ├─ _find_machine_info_in_hierarchy() で各ベンチマークディレクトリから
       │   上位階層を遡り、有効な machinename を自動検出
       │   （入れ子構造やOS直接指定にも対応）
       ├─ get_machine_info() で LUT 情報取得（cost_hour 等）
       └─ 各 <N>-thread について:
            └─ _collect_thread_payload()    ← ★ここがベンチマーク固有
                 ├─ (パターンA/B/D) <N>-thread.log を読んで値を抽出
                 ├─ (パターンC) <N>-thread.json から全データを取得
                 ├─ time / test_run_times を決定
                 ├─ cost = cost_hour * time / 3600
                 ├─ freq ファイルを読んで perf_stat を構築
                 └─ {"perf_stat": ..., "test_name": ...} を返す
```

---

## 共通部分（コピー可能）

以下のコードはすべてのパーサーで同一。新規作成時はそのままコピーする。

### [1] インポート・初期化

```python
#!/usr/bin/env python3
"""<benchmark> 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で <testcategory>/<benchmark> のみを抽出する。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics                       # test_run_times の中央値計算に必要
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

try:
    from make_one_big_json import get_machine_info
except ImportError:
    def get_machine_info(machinename: str) -> Dict[str, Any]:
        return {}
```

**注意**: `import statistics` はパターンC（`<N>-thread.json` から中央値計算）で必須。パターンA/B/D では不要だが、統一のため常に含めてよい。

### [3] 共通ヘルパー関数

```python
ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from log text."""
    return ANSI_ESCAPE_RE.sub("", text)

def _read_freq_file(freq_file: Path) -> Dict[str, int]:
    """Load <N>-thread_freq_{start,end}.txt into {freq_N: Hz} dict."""
    if not freq_file.exists():
        return {}
    freqs: Dict[str, int] = {}
    with freq_file.open(encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            value = line.strip()
            if not value:
                continue
            try:
                freq_hz = int(value)
            except ValueError:
                if ":" not in value:
                    continue
                try:
                    freq_mhz = float(value.split(":", 1)[1].strip())
                except ValueError:
                    continue
                freq_hz = int(freq_mhz * 1000)
            freqs[f"freq_{idx}"] = freq_hz
    return freqs

def _discover_threads(benchmark_dir: Path) -> Iterable[str]:
    """Return iterable of thread identifiers from <N>-thread.log files."""
    log_threads = sorted(benchmark_dir.glob("*-thread.log"))
    for file_path in log_threads:
        thread_prefix = file_path.stem.split("-", 1)[0]
        if thread_prefix:
            yield thread_prefix

def _find_machine_info_in_hierarchy(benchmark_dir: Path, search_root: Path) -> tuple[str, str, str, Dict[str, Any]]:
    """Find valid machinename by traversing up from benchmark_dir.
    
    Searches upward from benchmark_dir toward search_root, checking each
    directory name against the machine Look-Up-Table (LUT) until a valid
    machinename is found.
    
    IMPORTANT: get_machine_info() behavior
    - Returns valid machine info (with CSP != \"unknown\") if found in LUT
    - Returns fallback dict with CSP=\"unknown\" if NOT found in LUT
    - This function must reject CSP=\"unknown\" to avoid false positives
    
    Returns: (machinename, os_name, category_name, machine_info)
    
    This function handles various directory structures:
    1. Standard: .../machinename/os/testcategory/benchmark
    2. Nested: .../machinename/results/machinename/os/testcategory/benchmark
    3. OS-direct: .../machinename/os/testcategory/benchmark (--dir points to os)
    4. Multiple machines: bench_results/ with multiple machinename subdirectories
    
    Key behavior:
    - Searches UPWARD from benchmark_dir toward search_root
    - Selects the FIRST valid machinename found (closest to benchmark)
    - If same machinename appears multiple times (nested), uses the INNER one
    
    Example with nested structure:
        bench_results/aws-m7g-2xlarge-arm64/results/aws-m7g-2xlarge-arm64/Ubuntu/processor/compress-zstd-1.6.0/
        ^^^^^^^^^^^^^                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        (outer, ignored)                     (inner, selected as machinename)
        
        1. Start from: Ubuntu/ (category_dir.parent)
        2. Check: Ubuntu → not a valid machinename → continue
        3. Check: aws-m7g-2xlarge-arm64 (inner) → VALID → return immediately
        4. Outer aws-m7g-2xlarge-arm64 is never checked (already found and returned)
    """
    category_dir = benchmark_dir.parent
    category_name = category_dir.name
    
    # Start from os_dir and traverse upward toward search_root
    current = category_dir.parent
    
    # Track hierarchy from benchmark up to find valid machine
    path_parts = []
    while current != search_root.parent and current != current.parent:
        path_parts.append((current.name, current))
        
        # Try this directory name as machinename via LUT lookup
        machine_info = get_machine_info(current.name)
        
        # Valid machine found if get_machine_info returns non-empty dict with valid CSP        # CRITICAL: Must check CSP != \"unknown\" to avoid false positives
        # get_machine_info() returns {\"CSP\": \"unknown\", ...} for non-existent machines
        # Without this check, OS names (e.g., Ubuntu_24_04_3) would be treated as valid machines        if machine_info and machine_info.get("CSP") and machine_info.get("CSP") != "unknown":
            machinename = current.name
            
            # Determine os_name: directory immediately above testcategory
            # Structure: .../machinename/.../os_name/testcategory/benchmark
            # category_dir.parent is the os_name directory
            # We use relative_to to verify the path relationship and extract os_name
            
            try:
                # Calculate relative path from machinename dir to os dir
                # Example: current = ".../aws-m7g-2xlarge-arm64"
                #          category_dir.parent = ".../aws-m7g-2xlarge-arm64/Ubuntu_22_04_5"
                #          rel_path = "Ubuntu_22_04_5"
                # Or nested: category_dir.parent = ".../aws-m7g-2xlarge-arm64/some_dir/Ubuntu"
                #          rel_path = "some_dir/Ubuntu"
                rel_path = category_dir.parent.relative_to(current)
                parts = rel_path.parts
                
                # Extract the final component (os_name directory)
                # parts[-1] is always the directory name immediately above testcategory
                if len(parts) >= 1:
                    os_name = parts[-1]
                else:
                    # Edge case: machinename/testcategory/benchmark (no os level)
                    os_name = category_dir.parent.name
            except (ValueError, IndexError):
                # Fallback: use parent of category_dir directly
                os_name = category_dir.parent.name
            
            return machinename, os_name, category_name, machine_info
        
        current = current.parent
    
    # No valid machinename found in hierarchy, use fallback
    os_dir = category_dir.parent
    machine_dir = os_dir.parent
    return machine_dir.name, os_dir.name, category_name, {}
```

### [5] 共通ディレクトリ走査

```python
def _build_full_payload(search_root: Path) -> Dict[str, Any]:
    if not search_root.exists():
        raise FileNotFoundError(f"Directory not found: {search_root}")

    all_payload: Dict[str, Any] = {}

    for benchmark_dir in sorted(search_root.glob(f"**/{BENCHMARK_NAME}")):
        if not benchmark_dir.is_dir():
            continue

        # Robust machinename detection supporting nested and various structures
        machinename, os_name, category_name, machine_info = _find_machine_info_in_hierarchy(
            benchmark_dir, search_root
        )
        
        # Fallback if machine_info is empty
        if not machine_info:
            machine_info = get_machine_info(machinename)
        
        cost_hour = machine_info.get("cost_hour[730h-mo]", 0.0)

        thread_nodes: Dict[str, Any] = {}
        for thread_num in _discover_threads(benchmark_dir):
            thread_payload = _collect_thread_payload(
                benchmark_dir, thread_num, cost_hour
            )
            if thread_payload:
                thread_nodes[thread_num] = thread_payload

        if not thread_nodes:
            continue

        if machinename not in all_payload:
            all_payload[machinename] = {
                "CSP": machine_info.get("CSP", "N/A"),
                "total_vcpu": machine_info.get("total_vcpu", 0),
                "cpu_name": machine_info.get("cpu_name", "N/A"),
                "cpu_isa": machine_info.get("cpu_isa", "N/A"),
                "cost_hour[730h-mo]": cost_hour,
                "os": {},
            }

        machine_node = all_payload[machinename]
        if os_name not in machine_node["os"]:
            machine_node["os"][os_name] = {"testcategory": {}}

        os_node = machine_node["os"][os_name]
        if category_name not in os_node["testcategory"]:
            os_node["testcategory"][category_name] = {"benchmark": {}}

        benchmark_group = os_node["testcategory"][category_name]["benchmark"]
        benchmark_group[BENCHMARK_NAME] = {"thread": thread_nodes}

    return all_payload
```

### [6] 共通CLIエントリポイント

```python
import py_compile

def main() -> None:
    # Self syntax check
    try:
        py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    except py_compile.PyCompileError as e:
        print(f"Syntax error in {Path(__file__).name}: {e}", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description=(
            "<benchmark> parser: cloud_onehour/results/<machinename> を入力に "
            f"{BENCHMARK_NAME} を README 構造で出力する"
        )
    )
    parser.add_argument(
        "--dir", "-d", type=Path, required=True, dest="search_root",
        help="探索対象を含む親ディレクトリを指定",
    )
    parser.add_argument(
        "--out", "-o", type=Path,
        help="出力先 JSON ファイルへのパス。省略時は stdout に出力",
    )

    args = parser.parse_args()
    payload = _build_full_payload(args.search_root)

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)

if __name__ == "__main__":
    main()
```

**注意**: `import py_compile` はファイル先頭の `[1] インポート・初期化` に含めてもよい。

**`--dir` オプションの使用例**:

```bash
# ケース1: 複数machinename配下を一括処理
./json_parser_<benchmark>.py --dir /path/to/bench_results

# ケース2: 入れ子構造（machinename/results/machinename/...）
./json_parser_<benchmark>.py --dir /path/to/bench_results/aws-m7g-2xlarge-arm64/results/aws-m7g-2xlarge-arm64

# ケース3: OS直接指定（親ディレクトリがmachinename）
./json_parser_<benchmark>.py --dir /path/to/bench_results/aws-m7g-2xlarge-arm64/results/aws-m7g-2xlarge-arm64/Ubuntu_22_04_5

# ケース4: 出力をファイルに保存
./json_parser_<benchmark>.py --dir /path/to/bench_results --out output.json
```

いずれのケースでも `_find_machine_info_in_hierarchy()` が上位階層を遡って有効な machinename を自動検出するため、柔軟な指定が可能。

**同じmachinenameが複数回出現する場合の動作**:

入れ子構造で同じmachinenameが複数回パスに現れる場合の処理例：

```
ディレクトリ構造:
bench_results/
  └── aws-m7g-2xlarge-arm64/          ← 外側（検索されない）
      └── results/
          └── aws-m7g-2xlarge-arm64/  ← 内側（これをmachinenameとして採用）
              └── Ubuntu_22_04_5/      ← os_name
                  └── processor/        ← testcategory
                      └── compress-zstd-1.6.0/  ← benchmark

実行: --dir bench_results

処理の流れ:
1. glob で bench_results/**/compress-zstd-1.6.0 を検索
2. 見つかったパス: .../aws-m7g-2xlarge-arm64/results/aws-m7g-2xlarge-arm64/Ubuntu_22_04_5/processor/compress-zstd-1.6.0/
3. _find_machine_info_in_hierarchy() を呼び出し:
   - Ubuntu_22_04_5 から上へ検索開始
   - チェック1: "Ubuntu_22_04_5" → get_machine_info() が空 → 続行
   - チェック2: "aws-m7g-2xlarge-arm64" (内側) → get_machine_info() が有効 → ★採用して即座にreturn
   - チェック3以降: 実行されない（外側の aws-m7g-2xlarge-arm64 には到達しない）
4. 結果:
   - machinename: "aws-m7g-2xlarge-arm64" (内側)
   - os_name: "Ubuntu_22_04_5"
   - category_name: "processor"
```

この動作により、複数のマシンが存在し、かつ同じ名前が入れ子で出現する環境でも正しく処理される。

**注意**: `import py_compile` はファイル先頭の `[1] インポート・初期化` に含めてもよい。

---

## ベンチマーク固有部分（要実装）

新規ベンチマーク作成時に**変更・実装が必要な箇所**は以下の3点のみ。

### [2] ベンチマーク定数・正規表現

```python
BENCHMARK_NAME = "<benchmark>"          # ディレクトリ名と完全一致
TESTCATEGORY_HINT = "<testcategory>"    # 参考用（処理には使わない）

# ログ解析用の正規表現（ベンチマーク毎に異なる）
AVERAGE_RE = re.compile(r"Average:\s*([\d.]+)\s+<unit>", re.IGNORECASE)
```

### [4-a] `_load_thread_json()`（パターンCで必要）

`<N>-thread.json` が存在するベンチマークでのみ追加する。
この関数はすべてのパターンC実装で**同一コード**なのでコピーでよい。

```python
def _load_thread_json(benchmark_dir: Path, thread_num: str) -> Dict[str, list]:
    """Load <N>-thread.json and build a lookup: description -> test_run_times."""
    json_file = benchmark_dir / f"{thread_num}-thread.json"
    if not json_file.exists():
        return {}
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    lookup: Dict[str, list] = {}
    for _hash, entry in data.get("results", {}).items():
        desc = entry.get("description", "")
        for _sys_id, sys_data in entry.get("results", {}).items():
            if "test_run_times" in sys_data:
                lookup[desc] = sys_data["test_run_times"]
    return lookup
```

### [4-b] `_collect_thread_payload()`（必須・コア実装）

これがベンチマーク固有ロジックの本体。返却する dict 構造は固定。

```python
def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:

    # 1. ログ読み込み
    # 2. 正規表現で値を抽出
    # 3. (パターンC) _load_thread_json() で test_run_times を取得
    # 4. time / test_run_times / cost を決定
    # 5. test_payload dict を構築
    # 6. perf_stat (freq) を読み込み
    # 7. 返却

    return {
        "perf_stat": perf_stat,         # Dict
        "test_name": test_payload       # Dict[str, test_entry]
    }
```

`test_payload` の各エントリ（`test_entry`）は以下の固定構造:

```python
{
    "description": str,           # テストの説明
    "values": float,              # 代表値（スコアまたは秒）
    "raw_values": [float, ...],   # 生データ配列
    "unit": str,                  # 単位（MIPS, Seconds, Requests Per Second 等）
    "time": float | "N/A",       # 実行時間（秒）。コスト計算に使用
    "test_run_times": list|"N/A", # 各実行の実行時間配列。取得不可なら "N/A"
    "cost": float,                # cost_hour * time / 3600
}
```

---

## データソースパターン別の実装ガイド

ベンチマークの種類に応じて4パターンがある。新規作成時はまず**対象がどのパターンに該当するか**を判定する。

### 判定フローチャート

```
<N>-thread.json が存在する?
  ├─ YES → ベンチマーク値の単位が "Seconds" ?
  │          ├─ YES → パターンD（ビルド系）
  │          └─ NO  → パターンC（JSON からデータ取得）
  └─ NO  → ログ内に複数の独立テストがある?
              ├─ YES → パターンB（ログのみ・複数テスト）
              └─ NO  → パターンA（ログのみ・単一テスト）
```

---

### パターンA: ログのみ・単一テスト

**該当**: coremark, java-jmh
**README ケース**: ケース5
**特徴**: `<N>-thread.log` から1つの `Average: XXX <unit>` を抽出。時間情報なし。

```python
def _collect_thread_payload(benchmark_dir, thread_num, cost_hour):
    log_file = benchmark_dir / f"{thread_num}-thread.log"
    if not log_file.exists():
        return None

    content = _strip_ansi(log_file.read_text(encoding="utf-8"))
    match = AVERAGE_RE.search(content)
    if not match:
        return None

    value = float(match.group(1))

    test_payload = {
        "<test_name_key>": {
            "description": "<description>",
            "values": value,
            "raw_values": [value],
            "unit": "<unit>",
            "time": "N/A",
            "test_run_times": "N/A",
            "cost": 0.0,
        }
    }

    # perf_stat 構築（共通処理）
    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    perf_stat = {}
    if start_freq: perf_stat["start_freq"] = start_freq
    if end_freq:   perf_stat["end_freq"] = end_freq

    return {"perf_stat": perf_stat, "test_name": test_payload}
```

---

### パターンB: ログのみ・複数テスト

**該当**: sysbench（CPU + Memory）, ffmpeg（複数エンコーダ x シナリオ）
**README ケース**: ケース5
**特徴**: 1つの `<N>-thread.log` 内に複数の独立テスト結果。時間情報なし。

```python
def _collect_thread_payload(benchmark_dir, thread_num, cost_hour):
    log_file = benchmark_dir / f"{thread_num}-thread.log"
    if not log_file.exists():
        return None

    content = _strip_ansi(log_file.read_text(encoding="utf-8"))
    test_payload = {}

    # セクション毎にパースし、各テストの key/value/description を決定
    for section_id, value_str in TEST_SECTION_RE.findall(content):
        value = float(value_str)
        description = f"<セクションから生成>"
        key = f"<テスト名> - {description}"

        test_payload[key] = {
            "description": description,
            "values": value,
            "raw_values": [value],
            "unit": "<unit>",
            "time": "N/A",
            "test_run_times": "N/A",
            "cost": 0.0,
        }

    if not test_payload:
        return None

    # perf_stat（パターンA と同一）
    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    perf_stat = {}
    if start_freq: perf_stat["start_freq"] = start_freq
    if end_freq:   perf_stat["end_freq"] = end_freq

    return {"perf_stat": perf_stat, "test_name": test_payload}
```

---

### パターンC: JSON からデータ取得

**該当**: apache, compress-7zip, compress-lz4, compress-xz, compress-zstd, nginx, openssl, pgbench, redis, renaissance, simdjson, tensorflow-lite, tinymembench, x265 等
**README ケース**: ケース1, 2（`<N>-thread.json` が存在）
**特徴**: `values`, `raw_values`, `test_run_times`, `unit`, `description` を**すべて `<N>-thread.json` から取得**。ログからの正規表現抽出は不要。

```python
def _load_thread_json(benchmark_dir, thread_num):
    """Load <N>-thread.json and return list of test entries."""
    json_file = benchmark_dir / f"{thread_num}-thread.json"
    if not json_file.exists():
        return []
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    entries = []
    for _hash, entry in data.get("results", {}).items():
        title = entry.get("title", "")
        description = entry.get("description", "")
        scale = entry.get("scale", "")
        for _sys_id, sys_data in entry.get("results", {}).items():
            value = sys_data.get("value")
            if value is None:
                continue
            raw_values = sys_data.get("raw_values", [value])
            test_run_times = sys_data.get("test_run_times", "N/A")
            entries.append({
                "title": title,
                "description": description,
                "scale": scale,
                "value": value,
                "raw_values": raw_values,
                "test_run_times": test_run_times,
            })
    return entries

def _collect_thread_payload(benchmark_dir, thread_num, cost_hour):
    # ★ すべてのデータを <N>-thread.json から取得
    entries = _load_thread_json(benchmark_dir, thread_num)
    if not entries:
        return None

    test_payload = {}

    for entry in entries:
        title = entry["title"]
        description = entry["description"]
        value = entry["value"]
        raw_values = entry["raw_values"]
        test_run_times = entry["test_run_times"]
        unit = entry["scale"]

        if isinstance(test_run_times, list) and len(test_run_times) > 0:
            time_val = statistics.median(test_run_times)  # ★ 中央値
        else:
            time_val = 0.0

        cost = round(cost_hour * time_val / 3600, 6) if time_val > 0 else 0.0

        # キー: "<title> - <description>"
        key = f"{title} - {description}"

        test_payload[key] = {
            "description": description,
            "values": value,
            "raw_values": raw_values,
            "unit": unit,
            "time": time_val,
            "test_run_times": test_run_times,
            "cost": cost,
        }

    # perf_stat（パターンA と同一）
    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    perf_stat = {}
    if start_freq: perf_stat["start_freq"] = start_freq
    if end_freq:   perf_stat["end_freq"] = end_freq

    return {"perf_stat": perf_stat, "test_name": test_payload}
```

**重要**:
- ログファイルは `_discover_threads()` によるスレッド番号検出にのみ使用する
- `value` が `None`（エラー終了等）のエントリはスキップされる
- `raw_values` が JSON に存在しない場合は `[value]` をフォールバックとして使用する

---

### パターンD: ビルド系（値=時間）

**該当**: build-gcc, build-linux-kernel, build-llvm
**README ケース**: ケース5
**特徴**: ベンチマーク結果の値自体が実行時間（秒）なので `time = values`

```python
def _collect_thread_payload(benchmark_dir, thread_num, cost_hour):
    log_file = benchmark_dir / f"{thread_num}-thread.log"
    if not log_file.exists():
        return None

    content = _strip_ansi(log_file.read_text(encoding="utf-8"))
    match = AVERAGE_RE.search(content)
    if not match:
        return None

    value = float(match.group(1))
    cost = round(cost_hour * value / 3600, 6) if value else 0.0

    test_payload = {
        "<test_name_key>": {
            "description": "<description>",
            "values": value,
            "raw_values": [value],
            "unit": "Seconds",
            "time": value,              # ★ 値そのものが実行時間
            "test_run_times": [value],   # ★ 値そのものが実行時間
            "cost": cost,
        }
    }

    # perf_stat（パターンA と同一）
    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    perf_stat = {}
    if start_freq: perf_stat["start_freq"] = start_freq
    if end_freq:   perf_stat["end_freq"] = end_freq

    return {"perf_stat": perf_stat, "test_name": test_payload}
```

---

## test_name キーの命名規則

README_results.md の `"test_name"のキー生成ルール` に従う。

| 状況 | キー形式 | 例 |
|------|----------|-----|
| テストが1つだけ | `"<test_title>"` | `"Coremark 1.0"` |
| 同一 test_title で description が異なる | `"<test_title> - <description>"` | `"7-Zip Compression - Test: Compression Rating"` |
| ベンチマーク固有の複合キー | `"<固有名> - <variant>"` | `"Apache HTTP Server 2.4.56 - Concurrent Requests: 100"` |

**description フィールド**: パターンCでは `<N>-thread.json` 内の `description` と一致させる。ケース5では、README の仕様に従った固定文字列を使う。

---

## time / test_run_times / cost の決定ルール

| パターン | time | test_run_times | cost |
|---------|------|----------------|------|
| A (ログのみ・単一) | `"N/A"` | `"N/A"` | `0.0` |
| B (ログのみ・複数) | `"N/A"` | `"N/A"` | `0.0` |
| C (JSON) | `statistics.median(test_run_times)` | JSON の配列 | `round(cost_hour * time / 3600, 6)` |
| D (ビルド系) | `values` と同値 | `[values]` | `round(cost_hour * time / 3600, 6)` |

**cost の丸め**: `round(..., 6)` で小数点6桁。

---

## 出力JSON構造

各パーサーが出力する JSON のトップレベル構造:

```json
{
  "<machinename>": {
    "CSP": "<csp>",
    "total_vcpu": "<vcpu>",
    "cpu_name": "<cpu_name>",
    "cpu_isa": "<cpu_isa>",
    "cost_hour[730h-mo]": "<cost>",
    "os": {
      "<os>": {
        "testcategory": {
          "<testcategory>": {
            "benchmark": {
              "<benchmark>": {
                "thread": {
                  "<N>": {
                    "perf_stat": {
                      "start_freq": {"freq_0": "<Hz>", "...": "..."},
                      "end_freq": {"freq_0": "<Hz>", "...": "..."}
                    },
                    "test_name": {
                      "<key>": {
                        "description": "...",
                        "values": 1234.56,
                        "raw_values": [1234.56],
                        "unit": "...",
                        "time": 90.07,
                        "test_run_times": [90.07],
                        "cost": 0.001234
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

---

## チェックリスト

新規パーサー作成時に以下を確認:

- [ ] Python 3.10 で動作するか（3.11+ 固有の構文を使っていないか）
- [ ] `main()` 先頭で `py_compile.compile()` による自己 Syntax チェックを行っているか
- [ ] `BENCHMARK_NAME` がディレクトリ名と完全一致しているか
- [ ] パターンA/B/Dの場合、正規表現が実際の `<N>-thread.log` の出力形式と一致するか（ANSIエスケープ除去後）
- [ ] `<N>-thread.json` の有無を確認し、適切なパターン（A/B/C/D）を選択したか
- [ ] パターンCの場合、すべてのデータ（values, raw_values, test_run_times, unit）を `<N>-thread.json` から取得しているか（ログからの正規表現抽出は不要）
- [ ] `test_name` キーが README_results.md のキー生成ルールに従っているか
- [ ] `time` が正しく設定されているか（`0.0` や空配列 `[]` になっていないか）
- [ ] `cost` が `round(cost_hour * time / 3600, 6)` で計算されているか
- [ ] `_find_machine_info_in_hierarchy()` が含まれており、堅牢なmachinename検出が実装されているか
- [ ] `--dir` オプションで以下のパターンをテストしたか:
  - [ ] **パターン1**: 複数machinename直下（`--dir bench_results`）
  - [ ] **パターン2**: 入れ子構造で同じmachinenameが複数回出現（`--dir bench_results/machine/results/machine`）
    - 内側（ベンチマークに近い）のmachinenameが正しく採用されることを確認
    - 出力JSONのmachinenameキーに重複がなく、正しいosとcategoryが紐付いているか確認
  - [ ] **パターン3**: OS直接指定（`--dir bench_results/machine/results/machine/Ubuntu_22_04_5`）
    - 親ディレクトリから正しくmachinenameを検出できるか確認
  - [ ] **パターン4**: 複数マシン + 入れ子の組み合わせ（`--dir bench_results`で複数のマシンがそれぞれ入れ子構造）
    - 各マシンが独立して正しくパースされ、JSONの最上位キーに全マシンが含まれるか確認
- [ ] 出力 JSON が正しく machinename, os, testcategory を抽出できているか確認したか
- [ ] 同じベンチマークが複数のOS配下に存在する場合、正しく別エントリとして処理されるか確認したか
- [ ] README_results.md のケース5リストへの追加/非追加を判断したか

---

## 既存スクリプト一覧

既存スクリプト一覧は以下の独立ファイルで管理します。

- [README_json_parser_list.md](README_json_parser_list.md)
