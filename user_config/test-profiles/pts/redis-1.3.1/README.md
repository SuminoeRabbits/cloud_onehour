# Redis 1.3.1 - Custom Test Profile

## Purpose

This custom test profile overrides the default PTS redis-1.3.1 test profile to enable multi-threaded benchmarking.

## Modification

**File:** `install.sh`

**Change:** Add `--threads $NUM_CPU_CORES` option to redis-benchmark command.

```diff
- ./src/redis-benchmark \$@ > \$LOG_FILE
+ ./src/redis-benchmark --threads \$NUM_CPU_CORES \$@ > \$LOG_FILE
```

## How It Works

1. When `run_pts_benchmark.py` executes, it sets `PTS_USER_PATH_OVERRIDE` environment variable
2. PTS searches for test profiles in this priority:
   - First: `user_config/test-profiles/pts/redis-1.3.1/install.sh` (this custom version)
   - Second: `~/.phoronix-test-suite/test-profiles/pts/redis-1.3.1/install.sh` (original)
3. PTS uses the custom `install.sh` and generates a benchmark script with multi-threading enabled

## Effect

- **Before:** redis-benchmark runs single-threaded
- **After:** redis-benchmark uses `--threads $NUM_CPU_CORES` to utilize all available CPU cores

## Requirements

- Phoronix Test Suite >= 9.0.0 (for `PTS_USER_PATH_OVERRIDE` support)
- Redis 6.0+ (for `--threads` option support)

## Usage

```bash
cd /home/snakajim/work/cloud_onehour/scripts
./run_pts_benchmark.py redis-1.3.1
```

PTS will automatically use this custom profile instead of the original.

## Notes

- Original test profile: `~/.phoronix-test-suite/test-profiles/pts/redis-1.3.1/`
- This override is non-destructive (original profile remains unchanged)
- Thread count is controlled by `$NUM_CPU_CORES` environment variable set by PTS
