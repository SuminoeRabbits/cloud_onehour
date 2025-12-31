# nginx 3.0.1 - Custom Test Profile

## Purpose

This custom test profile overrides the default PTS nginx-3.0.1 test profile to enable multi-process nginx server for proper multi-core benchmarking.

## Modifications

**File:** `install.sh`

### Change 1: Enable nginx `worker_processes auto;`

```diff
- sed -i "s/worker_processes  1;/#worker_processes  auto;/g" nginx_/conf/nginx.conf
+ sed -i "s/worker_processes  1;/worker_processes  auto;/g" nginx_/conf/nginx.conf
```

### Change 2: Fix OpenSSL 3.2+ compatibility (Ubuntu 25.04 ARM64)

```diff
- CFLAGS="-Wno-error -O3 -march=native $CFLAGS"
+ CFLAGS="-Wno-error -std=gnu99 -O3 -march=native $CFLAGS"

- CXXFLAGS="-Wno-error -O3 -march=native $CFLAGS"
+ CXXFLAGS="-Wno-error -std=gnu++11 -O3 -march=native $CFLAGS"
```

**Reason:** nginx 1.23.3 has compilation errors with OpenSSL 3.2+ due to `crypto/modes/modes_local.h` syntax. Adding `-std=gnu99` enables Elvis operator (`?:`) support.

### Change 3: Fix GCC 14 compatibility for wrk's OpenSSL build

**File:** `install.sh` (Line 23-25)

```bash
# Fix GCC 14 compatibility: Change -std=c99 to -std=gnu99
# This allows inline assembly (asm keyword) in OpenSSL to work properly
sed -i 's/-std=c99/-std=gnu99/g' Makefile
```

**Problem:**
- wrk builds OpenSSL 1.1.1i internally with `-std=c99` in its Makefile
- OpenSSL uses inline assembly (`asm` keyword) in `crypto/bn/asm/x86_64-gcc.c`
- GCC 14's strict C99 mode doesn't recognize `asm` keyword, causing compilation failure

**Error (before fix):**
```
crypto/bn/asm/x86_64-gcc.c:369:17: error: expected ')' before ':' token
crypto/bn/asm/x86_64-gcc.c:77:9: error: implicit declaration of function 'asm'
```

**Solution:**
- Modify wrk's Makefile to use `-std=gnu99` instead of `-std=c99`
- GNU99 standard enables GNU extensions (including `asm` keyword) while maintaining C99 compatibility

**Why it works:**

| Standard | `asm` Keyword | Inline Assembly | GCC 14 |
|----------|---------------|-----------------|--------|
| `-std=c99` | ❌ Not recognized | ❌ Fails | ❌ Error |
| `-std=gnu99` | ✅ Recognized | ✅ Works | ✅ Success |

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

## Required Files

These files are automatically downloaded by `setup_download_cache.sh`:

1. **nginx-1.23.3.tar.gz** (1.1MB)
   - URL: https://nginx.org/download/nginx-1.23.3.tar.gz
   - Nginx web server source

2. **wrk-4.2.0.tar.gz** (11MB)
   - URL: https://github.com/wg/wrk/archive/4.2.0.tar.gz
   - HTTP benchmarking tool (includes OpenSSL 1.1.1i)

3. **http-test-files-1.tar.xz** (2.3MB)
   - URL: https://phoronix-test-suite.com/benchmark-files/http-test-files-1.tar.xz
   - Test files for HTTP benchmarking

### Download Cache Setup
```bash
# Setup all benchmarks including nginx
./scripts/setup_pts.sh

# Or setup only nginx cache
./scripts/setup_download_cache.sh nginx-3.0.1

# Verify cache
ls -lh ~/.phoronix-test-suite/download-cache/
```

## Requirements

- Phoronix Test Suite >= 9.0.0 (for `PTS_USER_PATH_OVERRIDE` support)
- nginx 1.23.2+ (supports `worker_processes auto;`)
- GCC 14+ (with `-std=gnu99` support for inline assembly)

## Usage

```bash
cd /home/snakajim/work/cloud_onehour/scripts
./run_pts_benchmark.py nginx-3.0.1
```

PTS will automatically use this custom profile instead of the original.

## Troubleshooting

### Build Fails with "asm: not declared"
**Symptoms:**
```
error: implicit declaration of function 'asm' [-Wimplicit-function-declaration]
error: expected ')' before ':' token
```

**Cause:** wrk's Makefile still using `-std=c99` (local test-profile not applied)

**Solution:**
```bash
# Force override and rebuild
rm -rf ~/.phoronix-test-suite/test-profiles/pts/nginx-3.0.1
rm -rf ~/.phoronix-test-suite/installed-tests/pts/nginx-3.0.1
./scripts/run_pts_benchmark.py nginx-3.0.1
```

### Missing Download Files
**Symptoms:**
```
tar: nginx-1.23.3.tar.gz: Cannot open: No such file or directory
tar: wrk-4.2.0.tar.gz: Cannot open: No such file or directory
```

**Solution:**
```bash
# Run download cache setup
./scripts/setup_download_cache.sh nginx-3.0.1

# Verify files
ls -lh ~/.phoronix-test-suite/download-cache/
# Expected: nginx-1.23.3.tar.gz, wrk-4.2.0.tar.gz, http-test-files-1.tar.xz
```

### wrk Build Succeeds But Binary Missing
**Symptoms:** Install completes but wrk executable not found

**Solution:**
```bash
# Check if wrk was built
ls -lh ~/wrk-4.2.0/wrk

# Rebuild if needed
rm -rf ~/.phoronix-test-suite/installed-tests/pts/nginx-3.0.1
phoronix-test-suite force-install pts/nginx-3.0.1
```

## Notes

- Original test profile: System PTS cache (downloaded on first run)
- This override is non-destructive (original profile remains unchanged)
- Multiple fixes applied: nginx worker processes, OpenSSL 3.2+ compatibility, GCC 14 compatibility
- wrk client was already multi-threaded in the original profile
- This patch fixes both nginx server-side bottleneck and GCC 14 compilation issues

## Version Information

- **Nginx Version**: 1.23.3
- **wrk Version**: 4.2.0
- **OpenSSL Version**: 1.1.1i (bundled with wrk)
- **PTS Test Profile**: pts/nginx-3.0.1
- **Supported OS**: Linux
- **Architectures**: x86_64, ARM64 (ARMv8.2-A, ARMv9-A, ARMv9-A+SVE2)

## ARM64 Architecture Support

The GCC 14 fix (`-std=c99` → `-std=gnu99`) is **architecture-agnostic** and works on:

✅ **AMD64/x86_64** - Intel, AMD processors
✅ **ARM64 ARMv8.2-A** - AWS Graviton 2, Neoverse N1
✅ **ARM64 ARMv9-A with SVE** - AWS Graviton 3, Neoverse V1
✅ **ARM64 ARMv9-A with SVE2** - AWS Graviton 4, Neoverse V2

**Why it works universally:**
- The `asm` keyword is a **GCC compiler extension**, not a CPU feature
- OpenSSL uses architecture-specific assembly instructions but the same `asm` keyword
- `-std=gnu99` enables `asm` on all GCC targets

**Detailed verification**: See [ARM64_VERIFICATION.md](ARM64_VERIFICATION.md)

## Date
2025-12-31
