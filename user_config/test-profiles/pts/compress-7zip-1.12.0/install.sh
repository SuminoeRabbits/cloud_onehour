#!/bin/sh

# Detect architecture
detect_arch() {
    uname -m
}

# Detect CPU SHA-512 instruction support
detect_sha512_support() {
    ARCH=$(detect_arch)

    # x86_64 / AMD64 detection
    if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
        # Check if CPU supports SHA-512 instructions (Intel Sapphire Rapids+, AMD Zen4+)
        if grep -q "sha512" /proc/cpuinfo 2>/dev/null; then
            echo "1"
        elif gcc -march=native -dM -E - </dev/null 2>/dev/null | grep -q "__SHA512__"; then
            echo "1"
        else
            echo "0"
        fi

    # ARM64 / AARCH64 detection
    elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        # Check for ARMv8.2-A SHA-512 instructions
        # ARM SHA-512 support indicated by 'sha512' in /proc/cpuinfo Features
        if grep -E "^Features.*sha512" /proc/cpuinfo 2>/dev/null | grep -q "sha512"; then
            echo "1"
        # Also check compiler defines for ARM SHA-512
        elif gcc -march=native -dM -E - </dev/null 2>/dev/null | grep -q "__ARM_FEATURE_SHA512"; then
            echo "1"
        else
            echo "0"
        fi

    else
        # Unknown architecture - assume no SHA-512 support
        echo "0"
    fi
}

# Detect CPU model and features for additional checks
detect_cpu_model() {
    ARCH=$(detect_arch)

    if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
        grep "model name" /proc/cpuinfo 2>/dev/null | head -1 | sed 's/.*: //'
    elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        # ARM CPU information
        CPU_IMPLEMENTER=$(grep "CPU implementer" /proc/cpuinfo 2>/dev/null | head -1 | awk '{print $4}')
        CPU_VARIANT=$(grep "CPU variant" /proc/cpuinfo 2>/dev/null | head -1 | awk '{print $4}')
        CPU_PART=$(grep "CPU part" /proc/cpuinfo 2>/dev/null | head -1 | awk '{print $4}')
        echo "ARM CPU: implementer=$CPU_IMPLEMENTER variant=$CPU_VARIANT part=$CPU_PART"
    else
        echo "Unknown architecture: $ARCH"
    fi
}

# Detect ARM-specific features (SVE, SVE2, etc.)
detect_arm_features() {
    ARCH=$(detect_arch)

    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        FEATURES=$(grep "^Features" /proc/cpuinfo 2>/dev/null | head -1)

        # Check for SVE (Scalable Vector Extension)
        if echo "$FEATURES" | grep -q "sve"; then
            echo "sve=yes"
        else
            echo "sve=no"
        fi

        # Check for SVE2
        if echo "$FEATURES" | grep -q "sve2"; then
            echo " sve2=yes"
        else
            echo " sve2=no"
        fi

        # Check for SHA512
        if echo "$FEATURES" | grep -q "sha512"; then
            echo " sha512=yes"
        else
            echo " sha512=no"
        fi

        # Check for SHA3
        if echo "$FEATURES" | grep -q "sha3"; then
            echo " sha3=yes"
        else
            echo " sha3=no"
        fi
    fi
}

ARCH=$(detect_arch)
SHA512_SUPPORT=$(detect_sha512_support)
CPU_MODEL=$(detect_cpu_model)

echo "[INFO] Architecture: $ARCH"
echo "[INFO] CPU: $CPU_MODEL"

# Display ARM-specific features if on ARM64
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
    ARM_FEATURES=$(detect_arm_features)
    echo "[INFO] ARM Features: $ARM_FEATURES"
fi

echo "[INFO] SHA-512 support: $SHA512_SUPPORT"

tar -xf 7z2500-src.tar.xz
cd CPP/7zip/Bundles/Alone2

# Apply patches based on CPU capabilities
if [ "$SHA512_SUPPORT" = "0" ]; then
    echo "[INFO] SHA-512 instructions not supported - disabling SHA-512 optimization"

    # Patch makefile to exclude Sha512Opt.c compilation
    # This prevents the use of vsha512* instructions
    if [ -f "../../../../C/Sha512Opt.c" ]; then
        # Rename the optimized file to disable it
        mv ../../../../C/Sha512Opt.c ../../../../C/Sha512Opt.c.disabled 2>/dev/null || true

        # Create a dummy file with empty implementation
        cat > ../../../../C/Sha512Opt.c << 'EOF'
/* SHA-512 optimizations disabled for CPU compatibility */
#include "Precomp.h"
#include "Sha512.h"

/* Stub implementation - CPU_IsSupported_SHA512 is defined in CpuArch.c and will return false */
/* We only need to provide the HW function stub */
void Z7_FASTCALL Sha512_UpdateBlocks_HW(UInt64 state[8], const Byte *data, size_t numBlocks)
{
  /* Empty stub - this should never be called since CPU_IsSupported_SHA512 returns false */
  UNUSED_VAR(state);
  UNUSED_VAR(data);
  UNUSED_VAR(numBlocks);
}
EOF
    fi
fi

# Patch makefile to fix GCC 14 dangling-pointer error (same as 1.9.0)
sed -i 's/CFLAGS_WARN_WALL = -Wall -Werror -Wextra/CFLAGS_WARN_WALL = -Wall -Wno-error=dangling-pointer -Wextra/g' ../../7zip_gcc.mak

# Build with appropriate flags
if [ "$SHA512_SUPPORT" = "1" ]; then
    echo "[INFO] Building with SHA-512 optimizations enabled"
    CFLAGS="-O3 -march=native -Wno-error $CFLAGS" make -j $NUM_CPU_CORES -f makefile.gcc
    EXIT_STATUS=$?
else
    echo "[INFO] Building without SHA-512 optimizations"
    # Use -march=native but SHA-512 opt is already disabled
    CFLAGS="-O3 -march=native -Wno-error $CFLAGS" make -j $NUM_CPU_CORES -f makefile.gcc
    EXIT_STATUS=$?

    if [ $EXIT_STATUS -ne 0 ]; then
        # Fallback: try without -march=native
        echo "[WARN] Build failed with -march=native, retrying without it"
        make clean
        CFLAGS="-O3 -Wno-error $CFLAGS" make -j $NUM_CPU_CORES -f makefile.gcc
        EXIT_STATUS=$?
    fi
fi

echo $EXIT_STATUS > ~/install-exit-status
cd ~
echo "#!/bin/sh
./CPP/7zip/Bundles/Alone2/_o/7zz b -mmt=\$NUM_CPU_CORES > \$LOG_FILE 2>&1
echo \$? > ~/test-exit-status" > compress-7zip
chmod +x compress-7zip
