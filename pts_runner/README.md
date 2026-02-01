# pts_runner README（CODE_TEMPLATE ダイジェスト）

このREADMEは `cloud_onehour/pts_runner/CODE_TEMPLATE.md` の**要点のみ**を日本語でまとめたものです。詳細仕様は必ず `CODE_TEMPLATE.md` を参照してください。

## TOC
- [pts\_runner README（CODE\_TEMPLATE ダイジェスト）](#pts_runner-readmecode_template-ダイジェスト)
  - [TOC](#toc)
  - [目的](#目的)
  - [最低限の構成（必須）](#最低限の構成必須)
  - [主要メソッド（実装必須）](#主要メソッド実装必須)
  - [PreSeedDownloader（大容量ダウンロード最適化）](#preseeddownloader大容量ダウンロード最適化)
  - [インストールログ（任意）](#インストールログ任意)
  - [ありがちなトラブルと対策（要点）](#ありがちなトラブルと対策要点)
  - [main() テンプレートの要点](#main-テンプレートの要点)
  - [参考実装（詳細は CODE\_TEMPLATE）](#参考実装詳細は-code_template)
  - [check\_compliance.py について](#check_compliancepy-について)
  - [新しい pts\_runner を作るとき](#新しい-pts_runner-を作るとき)

## 目的
- 新しい `pts_runner_*.py` を作るときの**構造・必須要件の共通化**
- 失敗しがちなPTSインストールやperf周りの**事故回避**

## 最低限の構成（必須）
- **スクリプトヘッダ（docstring）**に `phoronix-test-suite info pts/<benchmark>` の情報を記載
- **BenchmarkRunner.__init__** で以下を必ず設定
  - `self.benchmark`, `self.benchmark_full`, `self.test_category`, `self.test_category_dir`
  - `self.vcpu_count`, `self.machine_name`, `self.os_name`
  - `self.thread_list`（スケール or 単一スレッド）
  - `self.results_dir`
  - **perf順序が重要**：`check_and_setup_perf_permissions()` → `get_perf_events()`
  - `ensure_upload_disabled()` は必ず実行
- **run() は必ず True を返す**
  - 返さないと `cloud_exec.py` で失敗判定される（CRITICAL）

## 主要メソッド（実装必須）
- `run()`：インストール → 各スレッド実行 → export → summary → **return True**
- `run_benchmark(num_threads)`：
  - `TEST_RESULTS_NAME` に **必ず `{self.benchmark}`** を使う
  - perf有無でコマンド分岐
  - 周波数ログ（開始/終了）をクロスプラットフォームで保存
- `get_os_name()` / `get_cpu_affinity_list(n)`
- `get_cpu_frequencies()` / `record_cpu_frequency()`
  - `/proc/cpuinfo` が無い環境（ARM64 / Cloud）に備えて **複数の手段**を試す
- `get_perf_events()`：
  - **HW → SW → 無効** の3段階フォールバック
  - perfが無い/使えない場合も動作継続
- `check_and_setup_perf_permissions()`：
  - `perf_event_paranoid` を確認・必要なら `sudo sysctl` で緩和

## PreSeedDownloader（大容量ダウンロード最適化）
`aria2c` がある場合、downloads.xml から大きいファイルを先に高速DLしてPTSキャッシュに置く。

## インストールログ（任意）
必要時のみ有効化する想定。
- `PTS_INSTALL_LOG=1` で `results/install.log` へ保存
- `PTS_INSTALL_LOG_PATH=/path/to/file` で指定パスへ保存

## ありがちなトラブルと対策（要点）
- **結果ディレクトリのドット除去**  
  `stream-1.3.4` → `stream-134` に変換される前提で export する
- **PTSは失敗しても exit 0 を返すことがある**  
  returncodeだけに頼らず、stdout/stderrと実体ディレクトリで検証する
- **GCC-14互換性**  
  必要なテストのみ `install.sh` に `no-asm` 追加等のパッチを当てる

## main() テンプレートの要点
- `--threads` に加えて **位置引数のスレッド数**も受け付ける
- `--quick` で `FORCE_TIMES_TO_RUN=1` を有効化
- 0以下のスレッド数はエラーで終了

## 参考実装（詳細は CODE_TEMPLATE）
- `pts_runner_coremark-1.0.1.py`：最新の `--quick` パターン
- `pts_runner_stream-1.3.4.py`：ドット除去・perf統計
- `pts_runner_apache-3.0.0.py`：`patch_install_script` 実装

## check_compliance.py について
`cloud_onehour/pts_runner/check_compliance.py` は **CODE_TEMPLATE.md に基づく準拠チェック用**のスクリプトです。  
READMEは要点のみですが、厳密な仕様・生成元は `CODE_TEMPLATE.md` を正とします。

## 新しい pts_runner を作るとき
生成AIには `cloud_onehour/pts_runner/CODE_TEMPLATE.md` を**参照させてから**作らせること。  
READMEは概要だけなので、仕様の根拠は必ず CODE_TEMPLATE を使う。
- **TestCategory を必ず指定**すること（`self.test_category` / `self.test_category_dir`）
- 作成前に `phoronix-test-suite info <テスト名>` を**自分で実行して確認**すること
