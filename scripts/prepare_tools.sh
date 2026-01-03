#!/bin/bash
set -euo pipefail
# setup gcc14
./setup_gcc14.sh
./setup_binutil244.sh

# setup jdkxx, see the version in setup_jdkxx.sh.
./setup_jdkxx.sh

# build zlib
./build_zlib.sh

# build openssl
#./build_openssh.sh

# build pts
./setup_pts.sh

# build others
./setup_init.sh


