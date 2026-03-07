import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import matplotlib
matplotlib.use("Agg")

from openpyxl import Workbook
from openpyxl.styles import Font
from matplotlib import ticker as mticker
from matplotlib import pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

TEST_CATEGORIES = [
    "AI",
    "Compression",
    "Cryptography_and_TLS",
    "Database",
    "Java_Applications",
    "Memory_Access",
    "Multimedia",
    "Network",
    "Processor",
    "System",
]

HEADERS = [
    "benchmark",
    "machinename",
    "thread",
    "performance",
]


def find_comparison_block(payload: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in payload.items():
        if key.endswith("_comparison") and isinstance(value, dict) and "workload" in value:
            return value
    raise ValueError("No *_comparison block with workload found")


def to_int_if_possible(value: Any) -> Any:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def to_float_if_possible(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def is_rank_one(value: Any) -> bool:
    if value == 1:
        return True
    if isinstance(value, str) and value.strip() == "1":
        return True
    return False


def extract_rows(payload: Dict[str, Any]) -> List[Tuple[Any, ...]]:
    comparison = find_comparison_block(payload)
    workload = comparison.get("workload", {})

    rows: List[Tuple[Any, ...]] = []
    for _, benchmark_group in workload.items():
        if not isinstance(benchmark_group, dict):
            continue

        for benchmark_name, benchmark_data in benchmark_group.items():
            os_map = benchmark_data.get("os", {}) if isinstance(benchmark_data, dict) else {}
            for os_name, os_data in os_map.items():
                thread_map = os_data.get("thread", {}) if isinstance(os_data, dict) else {}
                for thread, thread_data in thread_map.items():
                    if not isinstance(thread_data, dict):
                        continue

                    unit = thread_data.get("unit")
                    ranking = thread_data.get("leaderboard") or thread_data.get("ranking") or []

                    for item in ranking:
                        if not isinstance(item, dict):
                            continue

                        score = to_float_if_possible(item.get("score", item.get("efficiency_score")))
                        relative = item.get(
                            "relative_performance",
                            item.get("relative_cost_efficiency"),
                        )
                        relative = to_float_if_possible(relative)
                        rank = item.get("rank")

                        rows.append(
                            (
                                benchmark_name,
                                os_name,
                                to_int_if_possible(thread),
                                unit,
                                item.get("machinename"),
                                item.get("cpu_name"),
                                score,
                                relative,
                                rank,
                            )
                        )

    return rows


def add_original_column(rows: Iterable[Tuple[Any, ...]], json_path: Path) -> List[Tuple[Any, ...]]:
    row_list = list(rows)
    baseline_scores: Dict[Tuple[Any, Any, Any], float] = {}
    selected_threads: Dict[Tuple[Any, Any, Any], int] = {}

    file_name = json_path.name
    is_performance_analysis = "performance_analysis" in file_name

    if not is_performance_analysis:
        return [
            (
                benchmark,
                os_name,
                thread,
                unit,
                machine_name,
                cpu_name,
                score,
                relative,
                None,
            )
            for benchmark, os_name, thread, unit, machine_name, cpu_name, score, relative, _ in row_list
        ]

    for row in row_list:
        benchmark, os_name, thread, unit, _, _, score, _, rank = row
        if not isinstance(thread, int):
            continue
        if not isinstance(score, (int, float)):
            continue
        if not is_rank_one(rank):
            continue

        key = (benchmark, os_name, unit)
        prev_thread = selected_threads.get(key)
        if prev_thread is None or thread < prev_thread:
            selected_threads[key] = thread
            baseline_scores[key] = float(score)

    enriched_rows: List[Tuple[Any, ...]] = []
    for row in row_list:
        benchmark, os_name, thread, unit, machine_name, cpu_name, score, relative, _ = row
        key = (benchmark, os_name, unit)
        baseline = baseline_scores.get(key)

        original_value = None
        if baseline not in (None, 0) and isinstance(score, (int, float)):
            unit_normalized = unit.strip().lower() if isinstance(unit, str) else ""
            if unit_normalized == "microseconds":
                original_value = (baseline / float(score)) * 100 if score != 0 else None
            else:
                original_value = (float(score) / baseline) * 100

        enriched_rows.append(
            (
                benchmark,
                os_name,
                thread,
                unit,
                machine_name,
                cpu_name,
                score,
                relative,
                original_value,
            )
        )

    return enriched_rows


def build_output_rows(rows: Iterable[Tuple[Any, ...]]) -> List[Tuple[Any, ...]]:
    projected_rows: List[Tuple[Any, Any, Any, Any]] = []

    for row in rows:
        benchmark, _, thread, _, machine_name, _, _, _, performance = row
        projected_rows.append((benchmark, machine_name, thread, performance))

    def normalize_sort_text(value: Any, case_insensitive: bool = True, collapse_spaces: bool = True) -> str:
        if value is None:
            return ""
        text = str(value)
        if collapse_spaces:
            text = " ".join(text.strip().split())
        return text.casefold() if case_insensitive else text

    def natural_sort_key(
        value: Any,
        case_insensitive: bool = True,
        collapse_spaces: bool = True,
    ) -> Tuple[Tuple[int, Any], ...]:
        text = normalize_sort_text(
            value,
            case_insensitive=case_insensitive,
            collapse_spaces=collapse_spaces,
        )
        parts = re.split(r"(\d+)", text)
        key_parts: List[Tuple[int, Any]] = []
        for part in parts:
            if part == "":
                continue
            if part.isdigit():
                key_parts.append((0, int(part)))
            else:
                key_parts.append((1, part))
        return tuple(key_parts)

    def thread_sort_value(value: Any) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return 10**9

    def sort_key(item: Tuple[Any, Any, Any, Any]) -> Tuple[Tuple[Tuple[int, Any], ...], Tuple[Tuple[int, Any], ...], int]:
        benchmark, machine_name, thread, _ = item
        thread_key = thread_sort_value(thread)
        machine_key = natural_sort_key(machine_name, case_insensitive=True, collapse_spaces=True)
        benchmark_key = natural_sort_key(benchmark, case_insensitive=True, collapse_spaces=False)
        return (benchmark_key, machine_key, thread_key)

    projected_rows.sort(key=sort_key)
    return projected_rows


def write_xlsx(rows: Iterable[Tuple[Any, ...]], output_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"

    for col, header in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)

    col_widths = [45, 30, 10, 20]
    column_letters = ["A", "B", "C", "D"]
    for letter, width in zip(column_letters, col_widths):
        sheet.column_dimensions[letter].width = width

    for row_idx, row in enumerate(rows, start=2):
        for col_idx, value in enumerate(row, start=1):
            cell = sheet.cell(row=row_idx, column=col_idx, value=value)
            if col_idx == 4 and isinstance(value, (float, int)):
                cell.number_format = "0.00"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(output_path))


def generate_graph_pdf(rows: Iterable[Tuple[Any, ...]], output_path: Path) -> int:
    grouped: Dict[str, Dict[str, List[Tuple[int, float]]]] = {}
    benchmark_units: Dict[str, str] = {}
    machine_family_by_benchmark: Dict[str, Dict[str, str]] = {}

    def detect_machine_family(cpu_name: Any) -> str:
        cpu_text = cpu_name.casefold() if isinstance(cpu_name, str) else ""

        arm_markers = ("arm", "aarch64", "graviton", "ampere", "neoverse", "cortex")
        amd_markers = ("amd", "epyc")
        intel_markers = ("intel", "xeon")

        if any(marker in cpu_text for marker in arm_markers):
            return "arm"
        if any(marker in cpu_text for marker in amd_markers):
            return "amd"
        if any(marker in cpu_text for marker in intel_markers):
            return "intel"
        return "other"

    for row in rows:
        benchmark, _, thread, unit, machinename, cpu_name, _, _, performance = row
        if not isinstance(benchmark, str):
            continue
        if not isinstance(machinename, str):
            continue
        if not isinstance(thread, int):
            continue
        if not isinstance(performance, (int, float)):
            continue

        if benchmark not in benchmark_units and isinstance(unit, str):
            benchmark_units[benchmark] = unit

        benchmark_map = grouped.setdefault(benchmark, {})
        benchmark_map.setdefault(machinename, []).append((thread, float(performance)))
        family_map = machine_family_by_benchmark.setdefault(benchmark, {})
        family_map.setdefault(machinename, detect_machine_family(cpu_name))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_count = 0

    def natural_sort_key(value: Any) -> Tuple[Tuple[int, Any], ...]:
        text = "" if value is None else str(value).casefold().strip()
        parts = re.split(r"(\d+)", text)
        key_parts: List[Tuple[int, Any]] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key_parts.append((0, int(part)))
            else:
                key_parts.append((1, part))
        return tuple(key_parts)

    def shorten_machinename(name: str, max_len: int = 18) -> str:
        text = name.strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    def build_machine_colors(benchmark: str, machine_names: List[str]) -> Dict[str, str]:
        palettes = {
            "arm": ["#1f77b4", "#4f97cc", "#76afd8", "#9bc8e7", "#c2dff2"],
            "intel": ["#b22222", "#c74444", "#d96868", "#e58d8d", "#f0b3b3"],
            "amd": ["#111111", "#2a2a2a", "#444444", "#5e5e5e", "#7a7a7a"],
            "other": ["#666666", "#808080", "#9a9a9a", "#b3b3b3", "#cdcdcd"],
        }
        family_counters: Dict[str, int] = {"arm": 0, "intel": 0, "amd": 0, "other": 0}
        family_map = machine_family_by_benchmark.get(benchmark, {})
        machine_colors: Dict[str, str] = {}

        for machinename in machine_names:
            family = family_map.get(machinename, "other")
            if family not in palettes:
                family = "other"
            palette = palettes[family]
            index = family_counters[family] % len(palette)
            machine_colors[machinename] = palette[index]
            family_counters[family] += 1

        return machine_colors

    with PdfPages(str(output_path)) as pdf:
        if not grouped:
            figure, axis = plt.subplots(figsize=(11.69, 8.27))
            axis.axis("off")
            axis.text(
                0.5,
                0.5,
                "No performance data available for graph generation",
                ha="center",
                va="center",
                fontsize=14,
            )
            pdf.savefig(figure)
            plt.close(figure)
            return 1

        for benchmark, machine_map in grouped.items():
            figure, axis = plt.subplots(figsize=(11.69, 8.27))
            figure.subplots_adjust(right=0.72)
            legend_handles: List[Any] = []
            all_threads = sorted(
                {
                    thread
                    for points in machine_map.values()
                    for thread, _ in points
                }
            )
            single_thread_mode = len(all_threads) == 1

            machine_items = sorted(machine_map.items(), key=lambda item: natural_sort_key(item[0]))
            machine_names = [name for name, _ in machine_items]
            machine_colors = build_machine_colors(benchmark, machine_names)
            if single_thread_mode:
                ranked_items: List[Tuple[str, List[Tuple[int, float]], float]] = []
                for machinename, points in machine_items:
                    y_values = [value for _, value in points]
                    if not y_values:
                        continue
                    bar_value = sum(y_values) / len(y_values)
                    ranked_items.append((machinename, points, bar_value))

                ranked_items.sort(key=lambda item: item[2], reverse=True)

                x_positions = list(range(len(ranked_items)))
                x_labels: List[str] = []
                label_counts: Dict[str, int] = {}

                for index, (series_number, (machinename, points, bar_value)) in enumerate(
                    enumerate(ranked_items, start=1)
                ):
                    legend_label = machinename
                    short_label = shorten_machinename(machinename)
                    label_counts[short_label] = label_counts.get(short_label, 0) + 1
                    if label_counts[short_label] > 1:
                        short_label = f"{short_label}#{label_counts[short_label]}"
                    x_labels.append(short_label)

                    color = machine_colors.get(machinename, "#808080")
                    axis.bar(index, bar_value, width=0.7, color=color)
                    legend_handles.append(
                        Patch(facecolor=color, edgecolor=color, label=legend_label)
                    )

                axis.set_xticks(x_positions)
                axis.set_xticklabels(x_labels)
                axis.set_xlabel("machinename")
                axis.tick_params(axis="x", labelrotation=25)
            else:
                line_styles = ["-", "--", ":", "-."]
                for series_number, (machinename, points) in enumerate(machine_items, start=1):
                    points_sorted = sorted(points, key=lambda item: item[0])
                    x_values = [item[0] for item in points_sorted]
                    y_values = [item[1] for item in points_sorted]
                    legend_label = f"{series_number}: {machinename}"
                    color = machine_colors.get(machinename, "#808080")
                    line_style = line_styles[(series_number - 1) % len(line_styles)]
                    axis.plot(
                        x_values,
                        y_values,
                        marker="o",
                        linewidth=1.5,
                        label=legend_label,
                        color=color,
                        linestyle=line_style,
                    )
                    legend_handles.append(
                        Line2D(
                            [0],
                            [0],
                            color=color,
                            linestyle=line_style,
                            marker="o",
                            linewidth=1.5,
                            label=legend_label,
                        )
                    )

                    if x_values and y_values:
                        place_on_left = series_number % 2 == 1
                        anchor_index = 0 if place_on_left else -1
                        x_anchor = float(x_values[anchor_index])
                        y_anchor = float(y_values[anchor_index])
                        x_offset = -7 if place_on_left else 7
                        align = "right" if place_on_left else "left"
                        y_offset = ((series_number - 1) % 4 - 1.5) * 4
                        axis.annotate(
                            str(series_number),
                            xy=(x_anchor, y_anchor),
                            xytext=(x_offset, y_offset),
                            textcoords="offset points",
                            ha=align,
                            va="center",
                            fontsize=8,
                            fontweight="bold",
                            color=color,
                            bbox={"boxstyle": "round,pad=0.1", "fc": "white", "ec": "none", "alpha": 0.7},
                        )

            unit_label = benchmark_units.get(benchmark)
            if single_thread_mode and all_threads:
                title = (
                    f"{benchmark} ({unit_label}),Thread={all_threads[0]}"
                    if unit_label
                    else f"{benchmark},Thread={all_threads[0]}"
                )
            else:
                title = f"{benchmark} ({unit_label})" if unit_label else benchmark
            axis.set_title(title)
            if not single_thread_mode:
                axis.set_xlabel("thread")
                axis.yaxis.set_major_locator(mticker.MultipleLocator(10))
            axis.set_ylabel("performance")
            if single_thread_mode:
                axis.set_ylim(0, 100)
                axis.set_yticks(list(range(0, 101, 10)))
            axis.grid(True, axis="y", linestyle="--", alpha=0.35)

            if legend_handles:
                axis.legend(
                    handles=legend_handles,
                    loc="upper left",
                    bbox_to_anchor=(1.01, 1.0),
                    borderaxespad=0.0,
                    frameon=False,
                    fontsize=7,
                )

            pdf.savefig(figure)
            plt.close(figure)
            page_count += 1

    return page_count


def convert_json_file(json_path: Path, output_ext: str) -> int:
    with json_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    raw_rows = extract_rows(payload)
    rows_with_performance = add_original_column(raw_rows, json_path)
    output_rows = build_output_rows(rows_with_performance)
    output_path = json_path.with_suffix(output_ext)
    pdf_path = json_path.with_suffix(".pdf")
    write_xlsx(output_rows, output_path)

    is_performance_analysis = "performance_analysis" in json_path.name
    if is_performance_analysis:
        generate_graph_pdf(rows_with_performance, pdf_path)
    elif pdf_path.exists():
        pdf_path.unlink()

    return len(output_rows)


def gather_target_jsons(global_dir: Path) -> List[Path]:
    json_files: List[Path] = []

    for category in TEST_CATEGORIES:
        category_dir = global_dir / category
        if not category_dir.exists() or not category_dir.is_dir():
            continue

        json_files.extend(
            sorted(
                json_file
                for json_file in category_dir.glob("*.json")
                if json_file.name.startswith(f"{category}_")
            )
        )

    return json_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert benchmark JSON files to Excel")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Workspace root path (default: script directory)",
    )
    parser.add_argument(
        "--ext",
        choices=[".xlsx"],
        default=".xlsx",
        help="Output extension (default: .xlsx)",
    )
    args = parser.parse_args()

    global_dir = args.root / "global"
    targets = gather_target_jsons(global_dir)

    if not targets:
        print("No target JSON files found")
        return 1

    total_rows = 0
    converted = 0

    for json_file in targets:
        try:
            row_count = convert_json_file(json_file, args.ext)
            total_rows += row_count
            converted += 1
            print(f"[OK] {json_file} -> {json_file.with_suffix(args.ext)} ({row_count} rows)")
        except Exception as exc:
            print(f"[NG] {json_file}: {exc}")

    print(f"Converted files: {converted}/{len(targets)}, total rows: {total_rows}")
    return 0 if converted == len(targets) else 2


if __name__ == "__main__":
    raise SystemExit(main())