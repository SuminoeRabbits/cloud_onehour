#!/bin/bash
set -euo pipefail
# setup gcc14
./setup_gcc14.sh

# build zlib
./build_zlib.sh

# build openssl
./build_openssl.sh

# build pts
./build_pts.sh
