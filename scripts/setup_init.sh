#!/bin/bash

# Stop on error
set -e

# install various tools depends on your local needs.
sudo apt-get -y update
sudo apt-get -y install bc
sudo apt-get -y install uuid-dev libxml2-dev
# install cpupower
sudo apt-get -y install linux-tools-common linux-tools-$(uname -r)
sudo apt-get -y install sysstat htop aria2 curl
sudo apt-get -y install flex bison libssl-dev libelf-dev