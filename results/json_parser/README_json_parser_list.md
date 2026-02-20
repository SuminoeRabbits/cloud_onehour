# json_parser 一覧

`results/json_parser` 配下の `json_parser_*.py` 一覧です。  
更新時はこのファイルを編集してください。

| スクリプト | パターン | テスト数 | 備考 |
|---|---|---|---|
| `json_parser_apache-3.0.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_build-gcc-1.5.0.py` | D (ビルド系) | 単一 | time = values |
| `json_parser_build-linux-kernel-1.17.1.py` | D (ビルド系) | 複数 | ログ内複数ビルド設定 |
| `json_parser_build-llvm-1.6.0.py` | D (ビルド系) | 複数 | ログ内複数ビルドシステム |
| `json_parser_cachebench-1.2.0.py` | - | - | 要確認 |
| `json_parser_compress-7zip-1.12.0.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_compress-lz4-1.10.0.py` | - | - | 要確認 |
| `json_parser_compress-xz-1.1.0.py` | - | - | 要確認 |
| `json_parser_compress-zstd-1.6.0.py` | - | - | 要確認 |
| `json_parser_coremark-1.0.1.py` | A (ログ単一) | 単一 | time 取得不可 |
| `json_parser_cpuminer-opt-1.8.0.py` | - | - | 要確認 |
| `json_parser_ffmpeg-7.0.1.py` | B (ログ複数) | 複数 | ケース5相当 |
| `json_parser_java-jmh-1.0.1.py` | A (ログ単一) | 単一 | time 取得不可 |
| `json_parser_memcached-1.2.0.py` | - | - | 要確認 |
| `json_parser_nginx-3.0.1.py` | - | - | 要確認 |
| `json_parser_openssl-3.6.0.py` | - | - | 要確認 |
| `json_parser_pgbench-1.11.1.py` | B (ログ複数) | 複数 | `<N>-thread.json` 存在時はC化検討 |
| `json_parser_phpbench-1.1.6.py` | - | - | 要確認 |
| `json_parser_redis-1.3.1.py` | C (JSON) | 複数 | `<N>-thread.json` から取得 |
| `json_parser_renaissance-1.4.0.py` | - | - | 要確認 |
| `json_parser_rustls-1.0.0.py` | - | - | 要確認 |
| `json_parser_simdjson-2.1.0.py` | - | - | 要確認 |
| `json_parser_stream-1.3.4.py` | - | - | 要確認 |
| `json_parser_sysbench-1.1.0.py` | B (ログ複数) | 2 (CPU + Memory) | time 取得不可 |
| `json_parser_tensorflow-lite-1.1.0.py` | - | - | 要確認 |
| `json_parser_tinymembench-1.0.2.py` | - | - | 要確認 |
| `json_parser_valkey-1.0.0.py` | C (JSON) | 複数 | 新規追加 |
| `json_parser_x265-1.5.0.py` | - | - | 要確認 |
