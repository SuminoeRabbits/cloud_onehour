#!/bin/bash
#
# setup_fpu.sh - FPU Benchmark System Dependency Setup (Ubuntu/Debian)
#
# Installs dependencies for FPU-category benchmarks idempotently.
# Already-installed packages are skipped.
#
# Covered benchmarks:
#   pts/vkpeak-1.3.0      : Vulkan compute GFLOPS (GPU)
#                           Requires: Vulkan runtime loader + GPU ICD
#   pts/vkresample-1.0.2  : Vulkan image upscaling via VkFFT (GPU)
#                           Requires: Vulkan dev, cmake, fftw3-dev, libpng-dev
#   pts/c-ray-2.0.0       : Multi-threaded CPU raytracer
#                           Requires: build-essential (covered by setup_pts.sh)
#   pts/mt-dgemm-1.3.1    : ACES DGEMM floating-point benchmark
#                           Requires: build-essential + unzip (covered by setup_pts.sh)
#   pts/mocassin-1.1.0    : Monte Carlo Simulations of Ionised Nebulae (CPU/MPI)
#                           Requires: Fortran toolchain + OpenMPI (covered below)
#   pts/cp2k-1.5.0        : CP2K molecular dynamics (CPU/MPI)
#                           Requires: Fortran toolchain, OpenMPI, MPI HDF5, BLAS/LAPACK
#   pts/ospray-1.0.3      : Intel OSPray ray-tracing (x86_64 pre-built binary)
#                           Requires: unzip, bzip2 (covered by setup_pts.sh)
#
# Note on GPU/Vulkan:
#   - mesa-vulkan-drivers covers software (CPU) Vulkan (lavapipe) and AMD/Intel iGPU.
#   - NVIDIA GPUs need proprietary driver + nvidia-utils (installed separately).
#   - For headless cloud VMs without GPU: lavapipe (software Vulkan) works but is slow.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APT_UTILS="${SCRIPT_DIR}/lib/apt_utils.sh"
if [[ -f "${APT_UTILS}" ]]; then
    # shellcheck disable=SC1090
    source "${APT_UTILS}"
fi

log_fpu() { echo "[setup_fpu] $*"; }

# Check if a dpkg package is already installed
pkg_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

apt_get_install() {
    if declare -F wait_for_apt_lock >/dev/null 2>&1; then
        wait_for_apt_lock
    fi
    sudo apt-get -o Dpkg::Lock::Timeout=300 install -y "$@"
}

log_fpu "=== FPU benchmark dependency setup (Ubuntu/Debian) ==="

# ---------------------------------------------------------------------------
# Group 1: Vulkan runtime - required by vkpeak and vkresample
#   libvulkan1       : Vulkan loader (runtime, required for any Vulkan app)
#   vulkan-tools     : vulkaninfo etc. (optional, useful for diagnostics)
#   mesa-vulkan-drivers : Mesa Vulkan ICDs (software lavapipe, AMD RADV, Intel ANV)
# ---------------------------------------------------------------------------
VULKAN_RUNTIME_PACKAGES=(
    libvulkan1
    vulkan-tools
    mesa-vulkan-drivers
)

# ---------------------------------------------------------------------------
# Group 2: Vulkan development - required by vkresample (source build)
#   libvulkan-dev    : Vulkan headers + loader import library
#   glslang-tools    : glslangValidator (GLSL -> SPIR-V compiler, used by VkFFT)
# ---------------------------------------------------------------------------
VULKAN_DEV_PACKAGES=(
    libvulkan-dev
    glslang-tools
)

# ---------------------------------------------------------------------------
# Group 3: FFT and image libraries - required by vkresample
#   libfftw3-dev     : FFTW3 header + library (VkFFT optionally links against it)
#   libpng-dev       : libpng headers (VkResample reads PNG input images)
# ---------------------------------------------------------------------------
FFTW_PNG_PACKAGES=(
    libfftw3-dev
    libpng-dev
)

# ---------------------------------------------------------------------------
# Group 4: cmake - required by vkresample source build
# ---------------------------------------------------------------------------
CMAKE_PACKAGES=(
    cmake
)

# ---------------------------------------------------------------------------
# Group 5: CP2K toolchain/runtime
#   gfortran-14          : keep Fortran major in sync with gcc-14/g++-14 setup
#   libgfortran-14-dev   : provides libgfortran.so linker symlink for GCC 14 toolchain
#   openmpi-bin          : mpirun/mpiexec
#   libopenmpi-dev       : mpicc/mpifort headers and libs
#   libhdf5-openmpi-dev  : MPI-enabled HDF5 for CP2K toolchain/CMake detection
#   libblas-dev          : BLAS fallback/system detection
#   liblapack-dev        : LAPACK fallback/system detection
#   python3              : build/helper scripts invoked by CP2K toolchain
# ---------------------------------------------------------------------------
CP2K_PACKAGES=(
    gfortran-14
    libgfortran-14-dev
    openmpi-bin
    libopenmpi-dev
    libhdf5-openmpi-dev
    libblas-dev
    liblapack-dev
    python3
)

ALL_PACKAGES=(
    "${VULKAN_RUNTIME_PACKAGES[@]}"
    "${VULKAN_DEV_PACKAGES[@]}"
    "${FFTW_PNG_PACKAGES[@]}"
    "${CMAKE_PACKAGES[@]}"
    "${CP2K_PACKAGES[@]}"
)

# Check which packages are missing
MISSING_PKGS=()
for pkg in "${ALL_PACKAGES[@]}"; do
    if pkg_installed "$pkg"; then
        log_fpu "[OK] already installed: $pkg"
    else
        log_fpu "[MISS] will install: $pkg"
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
    log_fpu "All FPU benchmark dependencies are already installed. Nothing to do."
else
    log_fpu "Installing missing packages: ${MISSING_PKGS[*]}"
    sudo apt-get update -qq
    apt_get_install "${MISSING_PKGS[@]}"
    log_fpu "Done."
fi

# ---------------------------------------------------------------------------
# Keep gfortran default aligned with GCC 14 for CP2K/OpenBLAS toolchain builds.
# CP2K's internal toolchain resolves plain "gfortran" from PATH; if Ubuntu keeps
# gfortran -> gfortran-13 while gcc/g++ point to 14, mixed-major linking breaks.
# ---------------------------------------------------------------------------
if [[ -x /usr/bin/gfortran-14 ]]; then
    log_fpu "Configuring gfortran-14 as the default gfortran via update-alternatives..."
    sudo update-alternatives --install /usr/bin/gfortran gfortran /usr/bin/gfortran-14 100 || true
    sudo update-alternatives --set gfortran /usr/bin/gfortran-14
    hash -r 2>/dev/null || true
    log_fpu "[OK] Default gfortran now points to: $(readlink -f "$(command -v gfortran)")"
else
    log_fpu "[WARN] /usr/bin/gfortran-14 not found; cannot switch default gfortran"
fi

# ---------------------------------------------------------------------------
# Post-install validation
# ---------------------------------------------------------------------------
log_fpu "=== Post-install validation ==="

# Check Vulkan loader
if ldconfig -p | grep -q libvulkan; then
    log_fpu "[OK] libvulkan found in ldconfig cache"
else
    log_fpu "[WARN] libvulkan not found in ldconfig cache (may need ldconfig update)"
fi

# Check available Vulkan ICDs
ICD_DIR="/usr/share/vulkan/icd.d"
if [[ -d "$ICD_DIR" ]] && ls "$ICD_DIR"/*.json >/dev/null 2>&1; then
    log_fpu "[OK] Vulkan ICD files found in $ICD_DIR:"
    for icd in "$ICD_DIR"/*.json; do
        log_fpu "     $(basename "$icd")"
    done
else
    log_fpu "[WARN] No Vulkan ICD files found in $ICD_DIR"
    log_fpu "[WARN] vkpeak/vkresample will fail without a Vulkan-capable GPU or software ICD."
    log_fpu "[HINT] For NVIDIA: install nvidia-driver-xxx and nvidia-utils-xxx"
    log_fpu "[HINT] For CPU/software rendering: mesa-vulkan-drivers (lavapipe) should be sufficient"
fi

# Check cmake
if command -v cmake >/dev/null 2>&1; then
    CMAKE_VER=$(cmake --version | head -1)
    log_fpu "[OK] cmake available: $CMAKE_VER"
else
    log_fpu "[WARN] cmake not found in PATH after installation"
fi

# Check FFTW3
if ldconfig -p | grep -q libfftw3; then
    log_fpu "[OK] libfftw3 found in ldconfig cache"
else
    log_fpu "[WARN] libfftw3 not found in ldconfig cache"
fi

# Check CP2K-related toolchain/runtime
if command -v gfortran-14 >/dev/null 2>&1; then
    log_fpu "[OK] gfortran-14 available: $(gfortran-14 --version | head -1)"
elif command -v gfortran >/dev/null 2>&1; then
    log_fpu "[WARN] gfortran-14 not found; fallback gfortran detected: $(gfortran --version | head -1)"
else
    log_fpu "[WARN] gfortran not found in PATH"
fi

if command -v gfortran >/dev/null 2>&1; then
    log_fpu "[OK] default gfortran: $(gfortran --version | head -1)"
    log_fpu "[OK] gfortran path: $(readlink -f "$(command -v gfortran)")"
fi

if command -v mpirun >/dev/null 2>&1; then
    log_fpu "[OK] mpirun available: $(mpirun --version 2>/dev/null | head -1)"
else
    log_fpu "[WARN] mpirun not found in PATH"
fi

if command -v mpifort >/dev/null 2>&1; then
    log_fpu "[OK] mpifort available"
else
    log_fpu "[WARN] mpifort not found in PATH"
fi

if [[ -e /usr/lib/gcc/x86_64-linux-gnu/14/libgfortran.so ]]; then
    log_fpu "[OK] GCC 14 libgfortran linker symlink present"
else
    log_fpu "[WARN] GCC 14 libgfortran linker symlink missing"
fi

if dpkg -s libhdf5-openmpi-dev >/dev/null 2>&1; then
    log_fpu "[OK] MPI-enabled HDF5 development package installed"
else
    log_fpu "[WARN] libhdf5-openmpi-dev not installed"
fi

log_fpu "=== FPU benchmark dependency setup complete ==="
