#!/bin/bash
set -euo pipefail
# setup gcc14
./setup_gcc14.sh

# build zlib
./build_zlib.sh

# build openssl
./build_openssh.sh

# build pts
./setup_pts.sh

# install cpupower
sudo apt-get -y install linux-tools-common linux-tools-$(uname -r)

