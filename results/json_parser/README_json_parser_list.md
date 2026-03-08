# json_parser 一覧

`results/json_parser` 配下の `json_parser_*.py` 一覧です。  
更新時はこのファイルを編集してください。

| スクリプト | パターン | テスト数 | 備考 |
|---|---|---|---|
| `json_parser_apache-3.0.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_build-gcc-1.5.0.py` | D (ビルド系) | 単一 | time = values |
| `json_parser_build-linux-kernel-1.17.1.py` | D (ビルド系) | 複数 | ログ内複数ビルド設定 |
| `json_parser_build-llvm-1.6.0.py` | D (ビルド系) | 複数 | ログ内複数ビルドシステム |
| `json_parser_cachebench-1.2.0.py` | C (JSON) | 3 | `<N>-thread.json` から取得 (Read / Write / Read+Modify+Write) |
| `json_parser_cassandra-1.3.1.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_compress-7zip-1.12.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_compress-lz4-1.10.0.py` | C (JSON) | 複数 (10) | `<N>-thread.json` から取得 (Level×Speed/Decompression) |
| `json_parser_compress-xz-1.1.0.py` | C (JSON) | 単一 | `<N>-thread.json` から取得 (unit=Seconds) |
| `json_parser_compress-zstd-1.6.0.py` | C (JSON) | 複数 (12〜14) | `<N>-thread.json` から取得 (機種によりテスト数が異なる) |
| `json_parser_coremark-1.0.1.py` | A (ログ単一) | 単一 | time 取得不可 |
| `json_parser_cpuminer-opt-1.8.0.py` | C (JSON) | 複数 | `<N>-thread.json` 有り機種のみ出力。log-only機種はスキップ |
| `json_parser_ffmpeg-7.0.1.py` | B (ログ複数) | 複数 | ケース5相当 |
| `json_parser_glibc-bench-1.9.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_java-jmh-1.0.1.py` | A (ログ単一) | 単一 | time 取得不可 |
| `json_parser_memcached-1.2.0.py` | C (JSON) | - | 全機種でテスト失敗 (FORCE_TIMES_TO_RUN=1エラー)。出力は {} |
| `json_parser_nginx-3.0.1.py` | C (JSON) | 複数 (6〜7) | `<N>-thread.json` から取得 (Connections数で変化) |
| `json_parser_numpy-1.2.1.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_openssl-3.6.0.py` | C (JSON) | 7 | `<N>-thread.json` から取得 (SHA256/SHA512/RSA4096 等) |
| `json_parser_perf-bench-1.1.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_pgbench-1.11.1.py` | B (ログ複数) | 2 (TPS × Mode) | ログから `Average: X TPS` を抽出。TPS_RE バグ修正済み（neo55sGen6 で検証） |
| `json_parser_pgbench-1.17.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_phpbench-1.1.6.py` | C (JSON) | 単一 | `<N>-thread.json` から取得 (unit=Score) |
| `json_parser_pmbench-1.0.2.py` | C (JSON) | 20 | `<N>-thread.json` から取得 |
| `json_parser_redis-1.3.1.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_redis-1.5.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_renaissance-1.4.0.py` | C (JSON) | 12 | `<N>-thread.json` から取得 |
| `json_parser_rustls-1.0.0.py` | C (JSON) | 12 | `<N>-thread.json` から取得 (handshake × Suite) |
| `json_parser_simdjson-2.1.0.py` | C (JSON) | 5 | `<N>-thread.json` から取得 |
| `json_parser_spark-1.0.1.py` | C (JSON) | 16 | `<N>-thread.json` から取得 |
| `json_parser_stream-1.3.4.py` | C (JSON) | 4 | `<N>-thread.json` から取得 (Copy / Scale / Triad / Add) |
| `json_parser_sysbench-1.1.0.py` | B (ログ複数) | 2 (CPU + Memory) | time 取得不可 |
| `json_parser_tensorflow-lite-1.1.0.py` | C (JSON) | 6 | `<N>-thread.json` から取得 (モデル別: SqueezeNet 等) |
| `json_parser_tinymembench-1.0.2.py` | C (JSON) | 2 | `<N>-thread.json` から取得 (Memcpy / Memset) |
| `json_parser_valkey-1.0.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_valkey-1.1.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_webp-1.4.0.py` | C (JSON) | 5 | `<N>-thread.json` から取得 (Default / Quality 100 等), pts/webp-1.2.0用 |
| `json_parser_x265-1.5.0.py` | C (JSON) | 2 | `<N>-thread.json` から取得 (Bosphorus 4K / 1080p) |
| `json_parser_svt-av1-2.17.0.py` | C (JSON) | 12 | `<N>-thread.json` から取得 (Preset 3/5/8/13 × Bosphorus 4K/1080p/Beauty 4K 10-bit)。neo55sGen6 で検証済み |
| `json_parser_srsran-2.5.0.py` | C (JSON) | 4 | `<N>-thread.json` から取得 (PDSCH/PUSCH × Total/Thread)。neo55sGen6 で検証済み |

## 現在実行ログがなく未検証（動作未確認）のパーサーリスト

neo55sGen6/Ubuntu_24_04_3 にテストデータが存在しないため未検証:
- `json_parser_apache-3.0.0.py` — neo55sGen6 でテスト未実行（ディレクトリが空）
- `json_parser_build-llvm-1.6.0.py` — neo55sGen6 でテスト未完了（Pre-Test Script のみ、結果なし）

## neo55sGen6/Ubuntu_24_04_3 で検証済み（2026-03-08）

以下は neo55sGen6 の実行ログを使って動作確認済み:
- `json_parser_build-gcc-1.5.0.py` — OK (1 test: time_to_compile)
- `json_parser_build-linux-kernel-1.17.1.py` — OK (2 tests: defconfig / allyesconfig)
- `json_parser_cachebench-1.2.0.py` — OK (3 tests: Read / Write / Read+Modify+Write)
- `json_parser_compress-lz4-1.10.0.py` — OK (10 tests: Level × Speed/Decompression)
- `json_parser_compress-xz-1.1.0.py` — OK (1 test)
- `json_parser_compress-zstd-1.6.0.py` — OK (14 tests)
- `json_parser_cpuminer-opt-1.8.0.py` — OK (12 tests: Algorithm 別)
- `json_parser_ffmpeg-7.0.1.py` — OK (8 tests: Encoder × Scenario)
- `json_parser_numpy-1.2.1.py` — OK (1 test)
- `json_parser_pgbench-1.11.1.py` — OK after fix (2 tests: Read Only / Read Write TPS)。TPS_RE を `Average: X TPS` 形式に修正
- `json_parser_svt-av1-2.17.0.py` — OK (12 tests: Preset 3/5/8/13 × 3 入力)（新規作成）
- `json_parser_pmbench-1.0.2.py` — OK (20 tests)
- `json_parser_renaissance-1.4.0.py` — OK (12 tests)
- `json_parser_rustls-1.0.0.py` — OK (12 tests)
- `json_parser_spark-1.0.1.py` — OK (16 tests)
- `json_parser_tensorflow-lite-1.1.0.py` — OK (6 tests)
- `json_parser_tinymembench-1.0.2.py` — OK (2 tests)
