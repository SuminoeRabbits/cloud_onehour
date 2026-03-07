# JSON to Excel

Convert benchmark result JSON files to analysis-ready Excel (`.xlsx`) format.

## Python Version

Requires Python 3.12 or newer.

## Excel Columns (A–I)

Row 1 is the header row.

| Col | Header | Type |
|-----|--------|------|
| A | `benchmark` | string |
| B | `os` | string |
| C | `thread` | integer |
| D | `unit` | string |
| E | `machinename` | string |
| F | `cpu_name` | string |
| G | `score` | float, 2 decimal places |
| H | `relative_performance` | float, 2 decimal places |
| I | `performance` | float, 2 decimal places |

## Excel Contents

Data starts from row 2. Source JSON structure:

```json
{
  "performance_comparison": {
    "workload": {
      "<workload-key>": {
        "<benchmark>": {
          "os": {
            "<OS>": {
              "thread": {
                "<thread>": {
                  "unit": "Microseconds",
                  "leaderboard": [
                    {
                      "rank": 1,
                      "machinename": "aws-m8a-4xlarge-amd64",
                      "cpu_name": "AMD EPYC 9R45 (Zen 5 \"Turin\")",
                      "score": 2271.33,
                      "relative_performance": 1.0
                    }
                  ]
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

Field mapping for each entry in `leaderboard` (or `ranking`):

| Column | Source field |
|--------|-------------|
| A `benchmark` | benchmark name key |
| B `os` | OS key |
| C `thread` | thread key (string converted to integer) |
| D `unit` | `unit` |
| E `machinename` | `machinename` |
| F `cpu_name` | `cpu_name` |
| G `score` | `score`; falls back to `efficiency_score` |
| H `relative_performance` | `relative_performance`; falls back to `relative_cost_efficiency` |
| I `performance` | computed (see below) |

## Column I: performance

Column I is populated **only for `*performance_analysis*` files**; it is empty for all other files.

Within each (benchmark, OS, unit) group, the baseline is defined as the `score` of rank=1 at the minimum thread count = **100**.

For all other rows the ratio relative to that baseline is computed:

- If `unit` is `Microseconds`: `performance = (baseline_score / score) × 100`
- Otherwise: `performance = (score / baseline_score) × 100`

## Sort Order

Rows are sorted in the following priority:

1. **A** `benchmark` — natural order
2. **E** `machinename` — natural order
3. **C** `thread` — ascending

> **Natural order**: numbers are compared as integers, not strings.
> Example: `1 < 20 < 100 < 200 < 1000 < 4000`

## Graph Generation

Graphs are generated **only for `*performance_analysis*` files**.
One graph per `benchmark`, all compiled into a single PDF (one page per graph).

### Line Colors

Colors are determined from **F** `cpu_name`:

| Architecture | Color |
|---|---|
| ARM64 (Graviton, Neoverse, Ampere, …) | Blue variants |
| AMD (EPYC, …) | Black / dark grey variants |
| Intel (Xeon, …) | Red variants |

Within each architecture group, line styles (solid, dashed, dotted, dash-dot) cycle to improve distinguishability. Colors and styles are consistent between the graph lines and the legend entries.

### CSP Marker Table (Line Chart)

Line markers are determined from the `machinename` prefix (case-insensitive).
Sync this table with `CSP_MARKER_TABLE` in `JSONtoEXCEL.py` when making changes.

| CSP   | `machinename` prefix | `marker` | `fillstyle` | Shape |
|-------|----------------------|----------|-------------|-------|
| AWS   | `aws-`               | `"o"`    | `"none"`    | ○ 白抜き円 |
| GCP   | `gcp-`               | `"o"`    | `"full"`    | ● 塗り円 |
| OCI   | `oci-`               | `"^"`    | `"none"`    | △ 白抜き三角 |
| Azure | `azure-`             | `"^"`    | `"full"`    | ▲ 塗り三角 |
| Other | (none of the above)  | `"s"`    | `"full"`    | ■ 正方形 |

### Graph Title Layout

Each graph has a two-level title:

| Level | Content | Source |
|-------|---------|--------|
| **Main title** (large, bold) | `test_snippet` value | `test_suite.json` → `"test_snippet"` field |
| **Subtitle** (smaller, below) | `benchmark (unit)` or `benchmark (unit), Thread=N` | benchmark name + unit from data |

If `test_snippet` is not found for a benchmark, only the subtitle is shown (using `axis.set_title()`).

### Multiple Thread Values — Line Chart

- X-axis: `thread` (integer)
- Y-axis: `performance`, grid lines every 10
- Series: one line per `machinename`
- Main title: `test_snippet` from `test_suite.json`
- Subtitle: `benchmark (unit)`
- Markers: determined by CSP (see CSP Marker Table above)

Each series is labeled with a number (1, 2, …) in both the legend and on the graph.
To prevent overlap, labels alternate between the **left end** and **right end** of each line.

### Single Thread Value — Bar Chart

- Y-axis: `performance`, range 0–100, grid lines every 10
- Series: one bar per `machinename`
- Main title: `test_snippet` from `test_suite.json`
- Subtitle: `benchmark (unit), Thread=<thread>`
- X-axis labels: shortened `machinename`

Bars are arranged from **highest performance on the left** to lowest on the right.
Series numbers are not shown.

## Excel File Output

Output location: same directory as the source JSON, with `.json` replaced by `.xlsx`.
If a file with the same name already exists, it is **overwritten**.

Test categories processed:
`AI`, `Compression`, `Cryptography_and_TLS`, `Database`, `Java_Applications`,
`Memory_Access`, `Multimedia`, `Network`, `Processor`, `System`

For each category `<Category>`, files matching `<Category>/<Category>_*.json`
are converted to `<Category>/<Category>_*.xlsx`.

## Conversion Rules

- Comparison block: any top-level key ending in `*_comparison` that contains a `workload` sub-key.
- Entry list: `leaderboard` is preferred; falls back to `ranking`.
- Score field: `score` is preferred; falls back to `efficiency_score`.
- Relative performance field: `relative_performance` is preferred; falls back to `relative_cost_efficiency`.
- Thread value: converted from string to integer when the value is numeric (e.g. `"4"` → `4`).

## JSONtoEXCEL.py

### Requirements

- Python 3.12 or newer
- `openpyxl`
- `matplotlib`

### Usage

```bash
python JSONtoEXCEL.py [options]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--root PATH` | script directory | Workspace root. The `global/` sub-directory is searched for JSON files. |
| `--ext .xlsx` | `.xlsx` | Output file extension (currently fixed to `.xlsx`). |
| `--log DIR` | `$PWD/log` | Directory for log files. Created automatically if absent. |
| `--graph` | — | Read existing Excel files and regenerate PDFs. **Excel files are not modified or recreated.** Use this when you have manually edited an Excel file and want to refresh the graphs without re-running the full JSON conversion. |

### Notes

- All processing is implemented in the single file `JSONtoEXCEL.py`.
- Previous intermediate PowerShell files (`*.ps1`) are deprecated.
