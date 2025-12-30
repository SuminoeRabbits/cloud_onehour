# nginx 3.0.1 - Custom Test Profile

## Purpose

This custom test profile overrides the default PTS nginx-3.0.1 test profile to enable multi-process nginx server for proper multi-core benchmarking.

## Modification

**File:** `install.sh`

**Change:** Enable nginx `worker_processes auto;` by removing the comment-out.

```diff
- sed -i "s/worker_processes  1;/#worker_processes  auto;/g" nginx_/conf/nginx.conf
+ sed -i "s/worker_processes  1;/worker_processes  auto;/g" nginx_/conf/nginx.conf
```

**Result in nginx.conf:**
```diff
- #worker_processes  auto;
+ worker_processes  auto;
```

## How It Works

1. When `run_pts_benchmark.py` executes, it sets `PTS_USER_PATH_OVERRIDE` environment variable
2. PTS searches for test profiles in this priority:
   - First: `user_config/test-profiles/pts/nginx-3.0.1/install.sh` (this custom version)
   - Second: System default nginx-3.0.1 install.sh (original)
3. PTS uses the custom `install.sh` which enables nginx multi-process mode

## Effect

### Before (Original):
- **nginx server:** Single worker process (`worker_processes 1;`)
- **wrk client:** Multi-threaded (`-t $NUM_CPU_CORES`) ✅
- **Bottleneck:** Server-side single process limits throughput

### After (Custom):
- **nginx server:** Auto-detect worker processes (`worker_processes auto;`) ✅
- **wrk client:** Multi-threaded (`-t $NUM_CPU_CORES`) ✅
- **Result:** Both server and client utilize all CPU cores

### nginx worker_processes auto behavior:
- Automatically detects CPU core count
- Creates one worker process per CPU core
- Example: 8-core machine → 8 nginx worker processes

## Architecture

```
┌─────────────────────────────────────┐
│  wrk (Client - Load Generator)     │
│  -t $NUM_CPU_CORES                 │  ← Already multi-threaded
│  (e.g., 8 threads)                  │
└──────────────┬──────────────────────┘
               │ HTTP/HTTPS requests
               ↓
┌─────────────────────────────────────┐
│  nginx (Server)                     │
│  worker_processes auto;             │  ← Enabled by this patch
│  (e.g., 8 worker processes)         │
└─────────────────────────────────────┘
```

## Requirements

- Phoronix Test Suite >= 9.0.0 (for `PTS_USER_PATH_OVERRIDE` support)
- nginx 1.23.2+ (supports `worker_processes auto;`)

## Usage

```bash
cd /home/snakajim/work/cloud_onehour/scripts
./run_pts_benchmark.py nginx-3.0.1
```

PTS will automatically use this custom profile instead of the original.

## Notes

- Original test profile: System PTS cache (downloaded on first run)
- This override is non-destructive (original profile remains unchanged)
- Only **1 character changed** (`#` removed from line 13)
- wrk client was already multi-threaded in the original profile
- This patch only fixes the nginx server-side bottleneck
