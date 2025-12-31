#!/bin/bash
# Setup PTS download cache for offline benchmark execution
#
# Purpose: Pre-download required source files to PTS cache directory
# This allows benchmarks to run without internet connectivity
#
# Usage:
#   ./scripts/setup_download_cache.sh [test-name]
#
# Examples:
#   ./scripts/setup_download_cache.sh compress-7zip-1.12.0
#   ./scripts/setup_download_cache.sh all

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CACHE_DIR="$HOME/.phoronix-test-suite/download-cache"

# Create cache directory if it doesn't exist
mkdir -p "$CACHE_DIR"

echo "=== PTS Download Cache Setup ==="
echo "Cache directory: $CACHE_DIR"
echo ""

# Download function
download_file() {
    local url="$1"
    local filename="$2"
    local dest="$CACHE_DIR/$filename"
    
    if [ -f "$dest" ]; then
        echo "[SKIP] $filename already exists in cache"
        return 0
    fi
    
    echo "[DOWNLOAD] $filename"
    echo "  URL: $url"
    
    # Try multiple methods
    if command -v wget >/dev/null 2>&1; then
        wget --no-check-certificate -q -O "$dest" "$url" || \
        wget --no-check-certificate -O "$dest" "$url"
    elif command -v curl >/dev/null 2>&1; then
        curl -k -L -o "$dest" "$url"
    else
        echo "[ERROR] Neither wget nor curl available"
        return 1
    fi
    
    if [ -f "$dest" ]; then
        local size=$(du -h "$dest" | cut -f1)
        echo "[OK] Downloaded $filename ($size)"
        return 0
    else
        echo "[ERROR] Failed to download $filename"
        return 1
    fi
}

# Setup compress-7zip-1.12.0 cache
setup_compress_7zip_1_12_0() {
    echo "--- Setting up compress-7zip-1.12.0 ---"
    download_file "https://www.7-zip.org/a/7z2500-src.tar.xz" "7z2500-src.tar.xz"
    echo ""
}

# Setup nginx-3.0.1 cache
setup_nginx_3_0_1() {
    echo "--- Setting up nginx-3.0.1 ---"
    download_file "https://nginx.org/download/nginx-1.23.3.tar.gz" "nginx-1.23.3.tar.gz"
    download_file "https://github.com/wg/wrk/archive/4.2.0.tar.gz" "wrk-4.2.0.tar.gz"
    download_file "https://phoronix-test-suite.com/benchmark-files/http-test-files-1.tar.xz" "http-test-files-1.tar.xz"
    echo ""
}

# Setup all tests
setup_all() {
    setup_compress_7zip_1_12_0
    setup_nginx_3_0_1
    # Add more tests here as needed
}

# Parse arguments
case "${1:-all}" in
    compress-7zip-1.12.0)
        setup_compress_7zip_1_12_0
        ;;
    nginx-3.0.1)
        setup_nginx_3_0_1
        ;;
    all)
        setup_all
        ;;
    *)
        echo "Usage: $0 [test-name|all]"
        echo ""
        echo "Available tests:"
        echo "  compress-7zip-1.12.0 - 7-Zip compression benchmark"
        echo "  nginx-3.0.1          - Nginx web server benchmark"
        echo "  all                  - Setup cache for all tests"
        exit 1
        ;;
esac

echo "=== Cache Setup Complete ==="
echo "Files in cache:"
ls -lh "$CACHE_DIR"

echo ""
echo "You can now run benchmarks offline:"
echo "  ./scripts/run_pts_benchmark.py compress-7zip-1.12.0"
echo "  ./scripts/run_pts_benchmark.py nginx-3.0.1"
