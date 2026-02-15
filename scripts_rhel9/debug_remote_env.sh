#!/bin/bash
# Debug script to check remote environment differences

echo "=== System Information ==="
cat /etc/os-release | grep -E "NAME|VERSION"
echo ""

echo "=== PHP Version ==="
php --version
echo ""

echo "=== Kernel Version ==="
uname -r
echo ""

echo "=== SSH Configuration ==="
grep -E "ClientAliveInterval|ClientAliveCountMax|TCPKeepAlive" /etc/ssh/sshd_config 2>/dev/null || echo "No relevant SSH settings found"
echo ""

echo "=== System Limits ==="
ulimit -a
echo ""

echo "=== Available Memory ==="
free -h
echo ""

echo "=== Active PHP Processes ==="
ps aux | grep php | grep -v grep
echo ""

echo "=== PTS Installation ==="
phoronix-test-suite version 2>&1 | head -5
echo ""

echo "=== Check for OOM killer activity ==="
sudo dmesg | tail -50 | grep -i "out of memory\|oom\|kill" || echo "No OOM activity found"
