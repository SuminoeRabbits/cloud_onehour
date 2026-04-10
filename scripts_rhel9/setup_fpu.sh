#!/bin/bash
#
# setup_fpu.sh - FPU Benchmark System Dependency Setup (RHEL/Oracle Linux EL9+)
#
# Installs dependencies for FPU-category benchmarks idempotently.
# Already-installed packages are skipped.
#
# Covered benchmarks:
#   pts/vkpeak-1.3.0      : Vulkan compute GFLOPS (GPU)
#                           Requires: Vulkan runtime loader + GPU ICD
#   pts/vkresample-1.0.2  : Vulkan image upscaling via VkFFT (GPU)
#                           Requires: Vulkan dev, cmake, fftw-devel, libpng-devel
#   pts/c-ray-2.0.0       : Multi-threaded CPU raytracer
#                           Requires: gcc, make (covered by setup_pts.sh Dev Tools)
#   pts/cp2k-1.5.0        : CP2K molecular dynamics (CPU/MPI)
#                           Requires: Fortran toolchain, OpenMPI, MPI HDF5, BLAS/LAPACK
#   pts/ospray-1.0.3      : Intel OSPray ray-tracing (x86_64 pre-built binary)
#                           Requires: unzip, bzip2 (covered by setup_pts.sh)
#
# Repository requirements:
#   - mesa-vulkan-drivers and vulkan-loader-devel are in the CRB (CodeReady Builder)
#     repository. setup_init.sh should have enabled it via --set-enabled crb or epel.
#   - fftw-devel is in EPEL (Extra Packages for Enterprise Linux).
#   - glslang (GLSL->SPIR-V compiler) may be in EPEL or CRB depending on EL version.
#
# Note on GPU/Vulkan:
#   - mesa-vulkan-drivers covers software (CPU) Vulkan (lavapipe) and AMD/Intel iGPU.
#   - NVIDIA GPUs need proprietary driver (cuda-drivers or akmod-nvidia).
#   - For headless cloud VMs without GPU: lavapipe works but is slow.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/dnf_utils.sh"

log_fpu() { echo "[setup_fpu] $*"; }

EL_VER=$(get_el_version)
log_fpu "=== FPU benchmark dependency setup (EL${EL_VER}) ==="

# Check if an rpm package is already installed
pkg_installed() {
    rpm -q "$1" >/dev/null 2>&1
}

# Safe install: try candidate packages in order, use first one found in repos
install_from_candidates() {
    local logical_name="$1"
    shift
    local candidates=("$@")
    for candidate in "${candidates[@]}"; do
        if sudo dnf -q list --available "$candidate" >/dev/null 2>&1 || \
           sudo dnf -q repoquery "$candidate" >/dev/null 2>&1; then
            log_fpu "[OK] ${logical_name}: using package '${candidate}'"
            sudo dnf install -y "$candidate"
            return 0
        fi
    done
    log_fpu "[WARN] ${logical_name}: none of [${candidates[*]}] found in enabled repos. Skipping."
    return 1
}

wait_for_dnf_lock

# ---------------------------------------------------------------------------
# Group 1: Vulkan runtime - required by vkpeak and vkresample
#   vulkan-loader    : Vulkan loader (runtime, required for any Vulkan app)
#   vulkan-tools     : vulkaninfo etc. (optional, useful for diagnostics)
#   mesa-vulkan-drivers : Mesa Vulkan ICDs (software lavapipe, AMD RADV, Intel ANV)
#                         Available in CRB on EL9+
# ---------------------------------------------------------------------------
VULKAN_RUNTIME_PACKAGES=(
    vulkan-loader
    vulkan-tools
    mesa-vulkan-drivers
)

# ---------------------------------------------------------------------------
# Group 2: Vulkan development - required by vkresample (source build)
#   vulkan-loader-devel : Vulkan headers + loader import library
#   glslang             : glslangValidator (GLSL -> SPIR-V compiler)
#                         Package name varies: glslang (EL9 CRB) or glslang-devel
# ---------------------------------------------------------------------------
VULKAN_DEV_PACKAGES=(
    vulkan-loader-devel
)

# ---------------------------------------------------------------------------
# Group 3: FFT and image libraries - required by vkresample
#   fftw-devel   : FFTW3 header + library (available in EPEL)
#   libpng-devel : libpng headers (available in base repos)
# ---------------------------------------------------------------------------
FFTW_PNG_PACKAGES=(
    fftw-devel
    libpng-devel
)

# ---------------------------------------------------------------------------
# Group 4: cmake - required by vkresample source build
# ---------------------------------------------------------------------------
CMAKE_PACKAGES=(
    cmake
)

# ---------------------------------------------------------------------------
# Group 5: CP2K toolchain/runtime
#   gcc-gfortran       : GNU Fortran compiler
#   openmpi            : mpirun/mpiexec runtime
#   openmpi-devel      : mpicc/mpifort headers and libs
#   hdf5-openmpi-devel : MPI-enabled HDF5 development package
#   blas-devel         : BLAS fallback/system detection
#   lapack-devel       : LAPACK fallback/system detection
#   python3            : helper scripts used by CP2K toolchain
# ---------------------------------------------------------------------------
CP2K_PACKAGES=(
    gcc-gfortran
    openmpi
    openmpi-devel
    hdf5-openmpi-devel
    blas-devel
    lapack-devel
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
    log_fpu "All required FPU benchmark packages are already installed."
else
    log_fpu "Installing missing packages: ${MISSING_PKGS[*]}"
    sudo dnf install -y "${MISSING_PKGS[@]}"
    log_fpu "Done."
fi

# ---------------------------------------------------------------------------
# glslang: package name varies across EL versions and repo configurations
# Try multiple candidate names
# ---------------------------------------------------------------------------
if ! pkg_installed "glslang" && ! pkg_installed "glslang-devel"; then
    log_fpu "Attempting to install glslang (GLSL->SPIR-V compiler)..."
    install_from_candidates "glslang" "glslang" "glslang-devel" || true
fi

# ---------------------------------------------------------------------------
# Post-install validation
# ---------------------------------------------------------------------------
log_fpu "=== Post-install validation ==="

# Check Vulkan loader
if ldconfig -p | grep -q libvulkan; then
    log_fpu "[OK] libvulkan found in ldconfig cache"
elif [[ -f /usr/lib64/libvulkan.so.1 ]] || [[ -f /usr/lib/libvulkan.so.1 ]]; then
    log_fpu "[OK] libvulkan.so.1 found"
else
    log_fpu "[WARN] libvulkan not found. Run: sudo ldconfig"
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
    log_fpu "[HINT] For NVIDIA: install nvidia driver + configure Vulkan ICD"
    log_fpu "[HINT] For CPU/software rendering: mesa-vulkan-drivers provides lavapipe"
fi

# Check cmake
if command -v cmake >/dev/null 2>&1; then
    CMAKE_VER=$(cmake --version | head -1)
    log_fpu "[OK] cmake available: $CMAKE_VER"
else
    log_fpu "[WARN] cmake not found in PATH after installation"
fi

# Check FFTW3
if ldconfig -p | grep -q libfftw3 2>/dev/null || \
   [[ -f /usr/lib64/libfftw3.so ]] || [[ -f /usr/lib/libfftw3.so ]]; then
    log_fpu "[OK] libfftw3 found"
else
    log_fpu "[WARN] libfftw3 not found (fftw-devel may need EPEL repo)"
    log_fpu "[HINT] Ensure EPEL is enabled: sudo dnf install -y epel-release"
fi

# Check CP2K-related toolchain/runtime
if command -v gfortran >/dev/null 2>&1; then
    log_fpu "[OK] gfortran available: $(gfortran --version | head -1)"
else
    log_fpu "[WARN] gfortran not found in PATH"
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

if rpm -q hdf5-openmpi-devel >/dev/null 2>&1; then
    log_fpu "[OK] MPI-enabled HDF5 development package installed"
else
    log_fpu "[WARN] hdf5-openmpi-devel not installed"
fi

log_fpu "=== FPU benchmark dependency setup complete ==="
