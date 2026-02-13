#!/usr/bin/env python3
"""x265-1.5.0 専用 JSON パーサー。"""

from __future__ import annotations

import py_compile
import sys
from pathlib import Path

from _json_parser_common import run_main

BENCHMARK_NAME = "x265-1.5.0"


def main() -> None:
    try:
        py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"Syntax error in {Path(__file__).name}: {exc}", file=sys.stderr)
        sys.exit(1)
    run_main(BENCHMARK_NAME, "x265")


if __name__ == "__main__":
    main()
