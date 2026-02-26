#!/bin/bash
# setup_pcre.sh - Build PCRE v1 (libpcre) from source on EL10+
#
# Background:
#   EL10 / Oracle Linux 10 dropped pcre-devel (PCRE v1) from official repos.
#   Apache httpd 2.4.56 (used by pts/apache-3.0.0) requires PCRE v1 for ./configure.
#   This script builds PCRE 8.45 (the final PCRE v1 release) as a static library
#   and installs pcre-config to /usr/local/bin so Apache httpd ./configure can find it.
#
# EL9:  pcre-devel is available via dnf, no source build needed.
# EL10+: pcre-devel not available, build from source.
#
# Build strategy:
#   --disable-shared: Only static libpcre.a is built. No libpcre.so is installed,
#   so no system-wide shared library changes and no ldconfig needed.
#   Apache httpd will statically link PCRE into the httpd binary.
#
# Install prefix: /usr/local (consistent with build_zlib.sh)
#   pcre-config  -> /usr/local/bin/pcre-config  (already in default PATH)
#   libpcre.a    -> /usr/local/lib/libpcre.a     (static, no ldconfig needed)
#   pcre.h       -> /usr/local/include/pcre.h

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

EL_VER=$(get_el_version)

# ---- Version pinning ----
# PCRE 8.45 is the final release of PCRE v1 (released 2021-06-15).
# Do not change this version; PCRE v2 has an incompatible API.
PCRE_VERSION="8.45"
PCRE_ARCHIVE="pcre-${PCRE_VERSION}.tar.bz2"
PCRE_URL_PRIMARY="https://sourceforge.net/projects/pcre/files/pcre/${PCRE_VERSION}/${PCRE_ARCHIVE}/download"
PCRE_URL_MIRROR="https://downloads.sourceforge.net/project/pcre/pcre/${PCRE_VERSION}/${PCRE_ARCHIVE}"
# SHA256 of pcre-8.45.tar.bz2 (verify at: https://sourceforge.net/projects/pcre/files/pcre/8.45/)
PCRE_SHA256="4dae6fdcd2bb0bb6c37b5f97c33c2be954da743985369cddac3546e3218bffb8"

INSTALL_PREFIX="/usr/local"

# ---- Already available: skip ----
if command -v pcre-config >/dev/null 2>&1; then
    installed_ver=$(pcre-config --version 2>/dev/null || echo "unknown")
    echo "[OK] pcre-config already available: ${installed_ver} at $(command -v pcre-config)"
    exit 0
fi

# ---- EL9: install via dnf (pcre-devel is available) ----
if [ "${EL_VER}" -lt 10 ] 2>/dev/null; then
    echo "[INFO] EL${EL_VER}: Installing pcre-devel via dnf..."
    sudo dnf -y install pcre-devel
    echo "[OK] pcre-devel installed via dnf (version: $(pcre-config --version))"
    exit 0
fi

# ---- EL10+: pcre-devel not in repos, build from source ----
echo "[INFO] EL${EL_VER}: pcre-devel not available in repos."
echo "[INFO] Building PCRE ${PCRE_VERSION} from source (static, --disable-shared)..."

# Ensure minimal build tools
sudo dnf -y install gcc make wget tar

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$WORK_DIR"

# Download (try primary URL, fall back to mirror, then curl)
echo "[INFO] Downloading PCRE ${PCRE_VERSION}..."
download_ok=0

if wget -q --show-progress -O "${PCRE_ARCHIVE}" "${PCRE_URL_PRIMARY}" 2>/dev/null; then
    download_ok=1
    echo "[INFO] Downloaded via primary URL."
elif wget -q --show-progress -O "${PCRE_ARCHIVE}" "${PCRE_URL_MIRROR}" 2>/dev/null; then
    download_ok=1
    echo "[INFO] Downloaded via mirror URL."
elif curl -fSL -o "${PCRE_ARCHIVE}" "${PCRE_URL_MIRROR}" 2>/dev/null; then
    download_ok=1
    echo "[INFO] Downloaded via curl fallback."
fi

if [ "${download_ok}" -eq 0 ]; then
    echo "[ERROR] Failed to download PCRE ${PCRE_VERSION} from all sources."
    echo "[ERROR]   Primary : ${PCRE_URL_PRIMARY}"
    echo "[ERROR]   Mirror  : ${PCRE_URL_MIRROR}"
    echo "[ERROR] Possible causes:"
    echo "[ERROR]   - Network not reachable from this VM"
    echo "[ERROR]   - SourceForge is temporarily unavailable"
    echo "[ERROR]   - Proxy settings required (check http_proxy / https_proxy)"
    echo "[INFO]  Check network connectivity and retry, or install pcre-devel manually."
    exit 1
fi

# SHA256 verification
echo "[INFO] Verifying checksum..."
echo "${PCRE_SHA256}  ${PCRE_ARCHIVE}" | sha256sum -c -

tar -xf "${PCRE_ARCHIVE}"
cd "pcre-${PCRE_VERSION}"

# Configure: static-only, UTF-8 and Unicode properties enabled
# (Apache httpd uses UTF-8 PCRE for URL pattern matching)
./configure \
    --prefix="${INSTALL_PREFIX}" \
    --disable-shared \
    --enable-static \
    --enable-utf8 \
    --enable-unicode-properties

make -j"$(nproc)"
sudo make install

# Verify
if ! command -v pcre-config >/dev/null 2>&1; then
    echo "[ERROR] pcre-config not found after install. Check install prefix PATH."
    exit 1
fi

echo "[OK] PCRE ${PCRE_VERSION} installed (static) to ${INSTALL_PREFIX}"
echo "[OK] pcre-config: $(command -v pcre-config) -> version $(pcre-config --version)"
echo "[OK] libpcre.a:   ${INSTALL_PREFIX}/lib/libpcre.a"
