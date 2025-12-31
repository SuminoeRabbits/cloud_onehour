# 7-Zip 25.00 Benchmark (pts/compress-7zip-1.12.0)

## Overview
Hardware-aware 7-Zip benchmark with multi-architecture support (x86_64, ARM64) and automatic SHA-512 hardware acceleration detection.

## Features

### Multi-Architecture Support
- ✅ **x86_64 / AMD64**: Intel and AMD processors
- ✅ **ARM64 / AARCH64**: ARMv8-A, ARMv8.2-A, ARMv9-A processors
- Automatic architecture detection via `uname -m`

### Hardware Detection
- **SHA-512 Hardware Acceleration**: Automatic detection and enablement
- **ARM Features**: SVE, SVE2, SHA-3 detection
- **Compiler Optimization**: Uses `-march=native` for optimal performance
- **Fallback Strategy**: Software implementation when hardware not supported

### Platform Compatibility

#### x86_64 / AMD64
| CPU | SHA-512 HW | Build Mode | Status |
|-----|------------|------------|--------|
| Intel Haswell (2013+) | ❌ | Software fallback | ✅ Verified |
| Intel Sapphire Rapids (2023+) | ✅ | HW acceleration | ✅ Ready |
| AMD Zen 4 (2022+) | ✅ | HW acceleration | ✅ Ready |

#### ARM64 / AARCH64
| CPU | SHA-512 | SVE | SVE2 | Build Mode | Status |
|-----|---------|-----|------|------------|--------|
| ARMv8-A (Cortex-A72, RPi4) | ❌ | ❌ | ❌ | Software fallback | ✅ Ready |
| ARMv8.2-A (Graviton 2, N1) | ✅ | ❌ | ❌ | HW acceleration | ✅ Ready |
| ARMv9-A (Graviton 3, V1) | ✅ | ✅ | ⚠️ | HW acceleration + SVE | ✅ Ready |
| ARMv9-A (Graviton 4, V2) | ✅ | ✅ | ✅ | HW acceleration + SVE2 | ✅ Ready |

⚠️ = SVE2 features present but 'sve2' keyword may not appear in `/proc/cpuinfo`

## Usage

### Simple Execution
```bash
./scripts/run_pts_benchmark.py compress-7zip-1.12.0
```

The script automatically:
1. Detects CPU architecture (x86_64 or ARM64)
2. Detects hardware capabilities (SHA-512, SVE, SVE2)
3. Builds optimized binary for your hardware
4. Runs benchmark across all CPU thread counts
5. Exports results

### Expected Output

#### x86_64 without SHA-512 (e.g., Intel Haswell)
```
[INFO] Architecture: x86_64
[INFO] CPU: Intel(R) Core(TM) i5-4460  CPU @ 3.20GHz
[INFO] SHA-512 support: 0
[INFO] Building without SHA-512 optimizations
```

#### ARM64 with SHA-512 and SVE2 (e.g., AWS Graviton 4)
```
[INFO] Architecture: aarch64
[INFO] CPU: ARM CPU: implementer=0x41 variant=0x0 part=0xd49
[INFO] ARM Features: sve=yes sve2=yes sha512=yes sha3=yes
[INFO] SHA-512 support: 1
[INFO] Building with SHA-512 optimizations enabled
```

## Key Differences: ARMv8-A vs ARMv9-A

### ARMv8-A with NEON/SIMD
- **Vector Size**: Fixed 128-bit
- **Instruction Set**: NEON/Advanced SIMD
- **SHA Support**: SHA-1, SHA-256 only
- **Example**: Cortex-A72, Raspberry Pi 4
- **Use Case**: General purpose, embedded

### ARMv8.2-A with SHA-512
- **Vector Size**: Fixed 128-bit
- **Instruction Set**: NEON/Advanced SIMD
- **SHA Support**: SHA-1, SHA-256, **SHA-512**
- **Example**: AWS Graviton 2, Neoverse N1
- **Use Case**: Cloud servers, data centers

### ARMv9-A with SVE2
- **Vector Size**: Scalable (128-2048 bit)
- **Instruction Set**: SVE/SVE2 (Scalable Vector Extension)
- **SHA Support**: SHA-1, SHA-256, SHA-512, SHA-3
- **Example**: AWS Graviton 3/4, Neoverse V1/V2
- **Use Case**: HPC, AI/ML, advanced workloads
- **Advantages**:
  - Vector length agnostic code
  - Better performance on variable data
  - Enhanced cryptographic operations

## How It Works

### Detection Process
1. **Architecture Detection**: `uname -m` → `x86_64`, `aarch64`, `arm64`
2. **SHA-512 Detection**:
   - x86_64: Checks `/proc/cpuinfo` and GCC `__SHA512__` define
   - ARM64: Checks `/proc/cpuinfo Features` and GCC `__ARM_FEATURE_SHA512` define
3. **ARM Features**: Detects SVE, SVE2, SHA-3 via `/proc/cpuinfo Features`

### Build Strategy
**When SHA-512 NOT supported**:
1. Replaces `Sha512Opt.c` with stub implementation
2. Builds with `-march=native -Wno-error`
3. Uses software SHA-512 fallback
4. If fails, retries without `-march=native`

**When SHA-512 supported**:
1. Keeps original `Sha512Opt.c` with HW instructions
2. Builds with `-march=native -Wno-error`
3. Uses hardware SHA-512 acceleration

### GCC 14 Compatibility
Includes fix for GCC 14 dangling-pointer warning:
```bash
sed -i 's/CFLAGS_WARN_WALL = -Wall -Werror -Wextra/CFLAGS_WARN_WALL = -Wall -Wno-error=dangling-pointer -Wextra/g' ../../7zip_gcc.mak
```

## Test Isolation

This test profile is **completely isolated**:
- ✅ Independent `install.sh` (only for compress-7zip-1.12.0)
- ✅ No impact on compress-7zip-1.9.0 (uses different install.sh)
- ✅ No impact on other compression benchmarks (zstd, xz, etc.)
- ✅ Override mechanism only affects this specific test

## Files

### Essential Files
- `install.sh` (5.5KB): Hardware-aware build script
- `README.md` (this file): Quick reference guide
- `test-definition.xml`: PTS test definition
- `downloads.xml`: Source file definitions

### Configuration
- `user_config/test-options/pts_compress-7zip-1.12.0.config`: Test-specific config

### Detailed Documentation (Optional)
- `ARM64_SUPPORT_SUMMARY.md`: ARM64 platform summary
- `ARM64_VERIFICATION.md`: Detailed ARM CPU categories
- `TEST_ISOLATION_VERIFICATION.md`: Isolation proof
- `FINAL_VERIFICATION.md`: Complete verification report

## Verification

### Tested Platforms
- ✅ Intel i5-4460 (Haswell, x86_64): Real hardware verification
- ✅ ARM64 platforms: Logic verification via simulation

### Test Commands
```bash
# Test on current system
./scripts/run_pts_benchmark.py compress-7zip-1.12.0

# Verify detection (no build)
cd ~/.phoronix-test-suite/installed-tests/pts/compress-7zip-1.12.0
grep "\[INFO\]" install.log
```

## Troubleshooting

### Build Fails with SHA-512 Error
```
Error: no such instruction: `vsha512rnds2 %xmm2,%ymm0,%ymm1'
```
**Cause**: CPU doesn't support SHA-512 instructions
**Solution**: Install.sh automatically detects and creates stub - should not occur

### Build Fails with Dangling Pointer Error
```
error: storing the address of local variable 'outStream' in '*p.rc.outStream' [-Werror=dangling-pointer=]
```
**Cause**: GCC 14 strict pointer checking
**Solution**: Install.sh automatically applies `-Wno-error=dangling-pointer` fix

### Architecture Not Detected
**Symptoms**: No "[INFO] Architecture" message
**Solution**: Check that install.sh is being used (run with `phoronix-test-suite force-install`)

### Initial Build Errors During Benchmark Run
**Symptoms**: See SHA-512 errors at the start of `./scripts/run_pts_benchmark.py` output
**Cause**: PTS performs initial test installation checks before the script overrides the test-profile
**Solution**: Ignore initial errors - the script will override the test-profile and successfully rebuild
**Expected Behavior**:
```
# Initial PTS checks (may show errors)
ERROR: /tmp/ccXXXX.s:232: Error: no such instruction: `vsha512rnds2...
[PROBLEM] pts/compress-7zip-1.12.0 is not installed.

# Script takes over and overrides test-profile
[INFO] Overriding PTS test-profile with local version
[OK] Test-profile copied

# Tests complete successfully
[OK] Test with 1 threads completed successfully
[OK] Test with 2 threads completed successfully
...
```

## Development

### Testing Detection Logic
```bash
# Test architecture detection
uname -m

# Test SHA-512 detection (x86_64)
grep "sha512" /proc/cpuinfo
gcc -march=native -dM -E - </dev/null | grep "__SHA512__"

# Test SHA-512 detection (ARM64)
grep "^Features" /proc/cpuinfo | grep "sha512"
gcc -march=native -dM -E - </dev/null | grep "__ARM_FEATURE_SHA512"

# Test ARM features
grep "^Features" /proc/cpuinfo
```

### Modifying Detection
Edit `install.sh` functions:
- `detect_arch()`: Architecture detection
- `detect_sha512_support()`: SHA-512 capability detection
- `detect_cpu_model()`: CPU identification
- `detect_arm_features()`: ARM-specific features (SVE, SVE2, SHA-3)

## Version Information

- **7-Zip Version**: 25.00
- **PTS Test Profile**: pts/compress-7zip-1.12.0
- **Source File**: 7z2500-src.tar.xz (1.46MB)
- **Install Size**: ~16MB
- **Supported OS**: Linux
- **Architectures**: x86_64, ARM64

## References

- Main implementation doc: `/home/snakajim/work/cloud_onehour/docs/compress-7zip-1.12.0-implementation.md`
- Test suite config: `/home/snakajim/work/cloud_onehour/test_suite.json`
- Benchmark runner: `/home/snakajim/work/cloud_onehour/scripts/run_pts_benchmark.py`

## Date
2025-12-31
