# run_pts_benchmark.py 確認事項

## 1. カスタマイズ関連の重要箇所

### ✅ PTS_USER_PATH_OVERRIDE設定（Line 196, 304）

**Line 196 (force_install_test):**
```python
env = os.environ.copy()
env['PTS_USER_PATH_OVERRIDE'] = str(self.config_dir)
# self.config_dir = project_root / "user_config"
```

**Line 304 (run_benchmark_for_threads):**
```python
env.update({
    'PTS_USER_PATH_OVERRIDE': str(self.config_dir),
    'NUM_CPU_CORES': str(threads),
    # ...
})
```

**確認結果:**
- ✅ `force_install_test()` (install.sh実行時) にPTS_USER_PATH_OVERRIDE設定
- ✅ `run_benchmark_for_threads()` (ベンチマーク実行時) にPTS_USER_PATH_OVERRIDE設定
- ✅ 両方で `self.config_dir` (user_config/) が正しく渡される

---

## 2. NUM_CPU_CORES環境変数（Line 305）

```python
'NUM_CPU_CORES': str(threads),
```

**確認結果:**
- ✅ ベンチマーク実行時に `NUM_CPU_CORES` が設定される
- ✅ カスタムinstall.shで `$NUM_CPU_CORES` を参照可能
- ✅ redis: `--threads $NUM_CPU_CORES`
- ✅ nginx: `-t $NUM_CPU_CORES` (wrk)

**注意:** `force_install_test()` (Line 196) では `NUM_CPU_CORES` が設定されていない

---

## 3. 実行モード判定（Line 61-94）

### Mode 1: RUNTIME SCALING (threads引数なし)
```python
if self.requested_threads is None:
    self.thread_start = 1
    self.thread_end = self.available_cores
```
- 1スレッドから最大vCPUまで全テスト

### Mode 2: COMPILE-TIME (threads >= vCPU)
```python
elif self.requested_threads >= self.available_cores:
    self.thread_start = self.available_cores
    self.thread_end = self.available_cores
```
- 全vCPUで1回だけ実行（コンパイル時固定）

### Mode 3: RUNTIME FIXED (1 <= threads < vCPU)
```python
else:
    self.thread_start = self.requested_threads
    self.thread_end = self.requested_threads
```
- 指定スレッド数で1回だけ実行

**確認結果:**
- ✅ すべてのモードでカスタマイズが適用される
- ✅ Mode 1で複数回実行時も毎回PTS_USER_PATH_OVERRIDE設定

---

## 4. 設定ファイル検証（Line 44-59）

```python
def validate_config(self):
    repo_test_config = self.config_dir / "test-options" / f"{self.benchmark_config_name}.config"
    
    if not repo_test_config.exists():
        print(f"[ERROR] Test-specific config file not found: {repo_test_config}")
        sys.exit(1)
```

**確認結果:**
- ✅ test-options configの存在を必須チェック
- ✅ 存在しない場合はエラーで停止
- ✅ カスタマイズ忘れを防ぐ安全機構

---

## 5. カスタムinstall.shの使用タイミング

### force_install_test() 実行タイミング（Line 577）
```python
def run(self):
    # ...
    self.force_install_test()  # ← ここでカスタムinstall.sh実行
    # ...
    for threads in range(self.thread_start, self.thread_end + 1):
        self.run_benchmark_for_threads(threads)
```

**確認結果:**
- ✅ ベンチマーク実行前に1回だけ `force_install_test()` 実行
- ✅ カスタムinstall.shで生成されたスクリプトを全スレッド数で再利用

---

## 6. 潜在的な問題点

### ⚠️ 問題1: install.sh実行時にNUM_CPU_CORESが未設定

**Line 196 (force_install_test):**
```python
env = os.environ.copy()
env['PTS_USER_PATH_OVERRIDE'] = str(self.config_dir)
# NUM_CPU_CORESが設定されていない！
```

**影響範囲:**
- redis-1.3.1: install.sh内で `$NUM_CPU_CORES` を参照
- nginx-3.0.1: install.sh内で `$NUM_CPU_CORES` を参照（wrk）

**対策が必要か？**

#### Case 1: install.sh生成スクリプト内で使用
```bash
# install.sh (redis)
echo "#!/bin/sh
./src/redis-benchmark --threads \$NUM_CPU_CORES \$@ > \$LOG_FILE" > redis
```
→ ✅ **問題なし**: `\$NUM_CPU_CORES` はエスケープされており、実行時に展開

#### Case 2: install.sh実行時に直接使用
```bash
# もしinstall.sh内に以下があったら
make -j $NUM_CPU_CORES  # これは問題
```
→ ⚠️ **問題あり**: install.sh実行時に未定義

**現在のカスタムinstall.sh確認:**
- redis-1.3.1: すべて `\$NUM_CPU_CORES` (エスケープ済み) → ✅ OK
- nginx-3.0.1: すべて `\$NUM_CPU_CORES` (エスケープ済み) → ✅ OK

**結論:** 現在のカスタマイズでは問題なし

---

## 7. 実行フロー全体

```
./run_pts_benchmark.py redis-1.3.1
    ↓
validate_config()
    - user_config/test-options/pts_redis-1.3.1.config 存在確認
    ↓
determine_execution_mode()
    - Mode決定 (例: RUNTIME SCALING, threads 1-8)
    ↓
set_cpu_governor_performance()
    ↓
force_install_test()  ← PTS_USER_PATH_OVERRIDE設定（NUM_CPU_CORESなし）
    ↓
    phoronix-test-suite force-install pts/redis-1.3.1
        ↓
        PTS: user_config/test-profiles/pts/redis-1.3.1/install.sh 実行
        ↓
        install.sh: redis実行スクリプト生成
            ./src/redis-benchmark --threads \$NUM_CPU_CORES ...
            （\$でエスケープ、実行時に展開）
    ↓
for threads in [1, 2, 3, ..., 8]:
    run_benchmark_for_threads(threads)  ← PTS_USER_PATH_OVERRIDE + NUM_CPU_CORES設定
        ↓
        env['NUM_CPU_CORES'] = str(threads)  # 例: "4"
        ↓
        taskset -c 0,2,4,6 phoronix-test-suite benchmark pts/redis-1.3.1
            ↓
            PTS: 生成済みredisスクリプト実行
            ↓
            ./src/redis-benchmark --threads 4 ...
                （$NUM_CPU_COREsが4に展開）
```

**確認結果:**
- ✅ カスタムinstall.shは確実に使用される
- ✅ NUM_CPU_CORESは実行時に正しく設定される
- ✅ 複数スレッド数テスト時も各回で正しいNUM_CPU_CORES値

---

## 8. 最終確認項目チェックリスト

| 項目 | 状態 | 詳細 |
|------|------|------|
| PTS_USER_PATH_OVERRIDE (install時) | ✅ | Line 196で設定 |
| PTS_USER_PATH_OVERRIDE (実行時) | ✅ | Line 304で設定 |
| NUM_CPU_CORES (実行時) | ✅ | Line 305で設定 |
| NUM_CPU_CORES (install時) | ⚠️ | 未設定（現状は問題なし） |
| test-options config必須チェック | ✅ | Line 48-51 |
| カスタムinstall.shエスケープ | ✅ | `\$NUM_CPU_CORES`で正しい |
| 複数スレッド数対応 | ✅ | ループ内で毎回設定 |
| CPU affinityサポート | ✅ | Line 319 taskset使用 |

---

## 9. 推奨事項

### オプション: install.sh実行時にもNUM_CPU_CORES設定

現在は問題ありませんが、将来の拡張性のため：

```python
def force_install_test(self):
    # ...
    env = os.environ.copy()
    env['PTS_USER_PATH_OVERRIDE'] = str(self.config_dir)
    env['NUM_CPU_CORES'] = str(self.available_cores)  # ← 追加推奨
```

**理由:**
- install.sh内で直接 `$NUM_CPU_CORES` 参照が必要になる可能性
- 一貫性: 実行時と同じ環境変数セット

**影響:**
- 現在のカスタマイズ: 影響なし（すべてエスケープ済み）
- 将来の拡張: より柔軟に対応可能

---

## 10. 結論

### ✅ 現在の実装で問題なし

1. PTS_USER_PATH_OVERRIDEが正しく設定されている
2. カスタムinstall.shが確実に使用される
3. NUM_CPU_CORESが実行時に正しく渡される
4. エスケープ処理が適切

### オプション改善（優先度: 低）

install.sh実行時にもNUM_CPU_COREsを設定すると、将来の拡張性が向上。
