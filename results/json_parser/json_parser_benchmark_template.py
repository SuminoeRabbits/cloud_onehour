#!/usr/bin/env python3
"""Template for creating benchmark-specific JSON parsers.

このテンプレートは `cloud_onehour/results/<machinename>` 配下にある
ベンチマーク結果から README_results.md と同じ構造の JSON を生成する
スクリプトを素早く作るためのひな型です。

## 使い方の流れ
1. このファイルをコピーし、`json_parser_<benchmark>.py` のようにリネームする。
2. 下記のプレースホルダ定数（`BENCHMARK_NAME` など）を対象ベンチマークに合わせて更新する。
3. `_collect_thread_payload()` を実装し、<thread> ノード（`perf_stat` と `test_name`）を返すようにする。
4. 必要であれば `_discover_threads()`・`_read_freq_file()`・`_strip_ansi()` などの補助関数を調整／拡張する。
5. `--dir /path/to/cloud_onehour/results/<machinename>` で動作確認し、README_results.md の仕様に沿っているか確認する。

JSONベース（ケース1/2）とログベース（ケース5）で取り込み方が異なるので、
テンプレート内の NOTE を参照しながら適宜ロジックを差し替えてください。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from make_one_big_json import get_machine_info  # type: ignore  # pylint: disable=import-error


# ---------------------------------------------------------------------------
# Benchmark-specific placeholders
# ---------------------------------------------------------------------------

# 1) ベンチマーク名（ディレクトリ名）を設定
BENCHMARK_NAME = "<benchmark-name>"  # 例: "redis-1.3.1"

# 2) README_results.md 上の testcategory を必要に応じてヒントとして残す
TESTCATEGORY_HINT = "<testcategory-name>"  # 例: "Database" / "AI"

# 3) ログのみ（ケース5）を扱う場合はパターン用の正規表現などをここに定義
#    例: AVERAGE_RE = re.compile(r"Average:\s*([\d.]+)\s+Seconds", re.IGNORECASE)

#	※ 途中失敗などで JSON を構築できない場合は、理由をメッセージで表示し
#		空の出力を返さずに処理を打ち切る。何を Fail とみなすかは
#		`cloud_onehour/results/README_results.md` の完了条件を必ず参照する。


# ---------------------------------------------------------------------------
# Optional helpers reused across many parsers
# ---------------------------------------------------------------------------

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
	"""Remove ANSI escape sequences from log text."""

	return ANSI_ESCAPE_RE.sub("", text)


def _read_freq_file(freq_file: Path) -> Dict[str, int]:
	"""Load `<thread>-thread_freq_{start,end}.txt` into `{freq_N: Hz}` dict."""

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
	"""Return iterable of thread identifiers.

	NOTE:
	- JSONベースのベンチ: `*-thread.json` ファイル名からスレッド数を抽出する。
	- ログベースのベンチ: `*-thread.log` を列挙する。
	- 特殊ケースはこの関数を書き換えて対応する。
	"""

	json_threads = sorted(benchmark_dir.glob("*-thread.json"))
	log_threads = sorted(benchmark_dir.glob("*-thread.log"))
	files = json_threads or log_threads

	for file_path in files:
		thread_prefix = file_path.stem.split("-", 1)[0]
		if thread_prefix:
			yield thread_prefix


# ---------------------------------------------------------------------------
# Benchmark-specific extraction hooks
# ---------------------------------------------------------------------------

def _collect_thread_payload(
	benchmark_dir: Path,
	thread_num: str,
	cost_hour: float,
) -> Optional[Dict[str, Any]]:
	"""Build the `<thread>` node for README_results.md structure.

	この関数を各ベンチに合わせて実装する。返り値は以下の辞書構造:
	```
	{
	    "perf_stat": {
	        "start_freq": {...},  # 任意
	        "end_freq": {...},    # 任意
	    },
	    "test_name": {
	        "<key>": {
	            "description": str,
	            "values": float|str,
	            "raw_values": list|str,
	            "unit": str,
	            "time": float|str,
	            "test_run_times": list,
	            "cost": float|str,
	            "error": Optional[str],
	        },
	        ...
	    }
	}
	```

	実装ヒント:
	- JSONケース: `<N>-thread.json` を読み、`results` を走査。
	- ログケース (build-*, coremark など): `_strip_ansi()` で整形してから正規表現で値を抽出。
	- `test_name` キー生成ルールは README_results.md の「Multiple "test_name"...」節に従う。
	- コスト計算は `cost_hour * time_seconds / 3600` を丸めて使う（time が無い場合は "N/A" 等）。
	"""

	raise NotImplementedError("Customize _collect_thread_payload() for this benchmark.")


# ---------------------------------------------------------------------------
# Machine-level aggregation (共通ロジック)
# ---------------------------------------------------------------------------

def _build_machine_payload(machine_dir: Path) -> Dict[str, Any]:
	if BENCHMARK_NAME.startswith("<"):
		raise SystemExit("Update BENCHMARK_NAME before using this template.")

	if not machine_dir.is_dir():
		raise FileNotFoundError(f"Machine directory not found: {machine_dir}")

	machinename = machine_dir.name
	machine_info = get_machine_info(machinename)
	cost_hour = machine_info.get("cost_hour[730h-mo]", 0.0)

	machine_node: Dict[str, Any] = {
		"CSP": machine_info.get("CSP", "N/A"),
		"total_vcpu": machine_info.get("total_vcpu", 0),
		"cpu_name": machine_info.get("cpu_name", "N/A"),
		"cpu_isa": machine_info.get("cpu_isa", "N/A"),
		"cost_hour[730h-mo]": cost_hour,
		"os": {},
	}

	for os_dir in sorted([p for p in machine_dir.iterdir() if p.is_dir()]):
		os_node: Dict[str, Any] = {"testcategory": {}}
		for testcategory_dir in sorted([p for p in os_dir.iterdir() if p.is_dir()]):
			benchmark_dir = testcategory_dir / BENCHMARK_NAME
			if not benchmark_dir.is_dir():
				continue

			thread_nodes: Dict[str, Any] = {}
			for thread_num in _discover_threads(benchmark_dir):
				thread_payload = _collect_thread_payload(benchmark_dir, thread_num, cost_hour)
				if thread_payload:
					thread_nodes[thread_num] = thread_payload

			if not thread_nodes:
				print(
					f"Info: No complete results for {BENCHMARK_NAME} under {benchmark_dir} (test failed?)",
					file=sys.stderr,
				)
				continue

			os_node["testcategory"].setdefault(testcategory_dir.name, {"benchmark": {}})
			os_node["testcategory"][testcategory_dir.name]["benchmark"][BENCHMARK_NAME] = {
				"thread": thread_nodes
			}

		if os_node["testcategory"]:
			machine_node["os"][os_dir.name] = os_node

	return {machinename: machine_node}


# ---------------------------------------------------------------------------
# CLI entry point (共通ロジック)
# ---------------------------------------------------------------------------

def main() -> None:
	parser = argparse.ArgumentParser(
		description=(
			"Template parser: cloud_onehour/results/<machinename> を入力に "
			f"{BENCHMARK_NAME} を README 構造で出力する"
		)
	)
	parser.add_argument(
		"--dir",
		"-d",
		type=Path,
		required=True,
		dest="machine_dir",
		help="cloud_onehour/results/<machinename> ディレクトリへのパスを指定 (必須)",
	)
	parser.add_argument(
		"--out",
		"-o",
		type=Path,
		help="出力先 JSON ファイルへのパス。省略時は stdout に出力",
	)

	args = parser.parse_args()
	payload = _build_machine_payload(args.machine_dir)

	text = json.dumps(payload, ensure_ascii=False, indent=2)
	if args.out:
		args.out.write_text(text + "\n", encoding="utf-8")
	else:
		print(text)


if __name__ == "__main__":
	main()
