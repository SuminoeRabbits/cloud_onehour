#!/bin/bash

# EXECUTION MARKER: Create marker file to prove this script is being executed
MARKER_FILE="/tmp/nginx_install_executed_$(date +%Y%m%d_%H%M%S)_$$"
touch "$MARKER_FILE"
echo "EXECUTION MARKER: Custom install.sh started at $(date) - PID $$" >&2
echo "EXECUTION MARKER: Marker file created at $MARKER_FILE" >&2
echo "EXECUTION MARKER: Working directory: $(pwd)" >&2

mkdir $HOME/nginx_
tar -xf http-test-files-1.tar.xz
tar -xf nginx-1.23.3.tar.gz
cd nginx-1.23.3
CFLAGS="-Wno-error -std=gnu99 -O3 -march=native $CFLAGS" CXXFLAGS="-Wno-error -std=gnu++11 -O3 -march=native $CFLAGS" ./configure --prefix=$HOME/nginx_ --without-http_rewrite_module --without-http-cache  --with-http_ssl_module
make -j $NUM_CPU_CORES
echo $? > ~/install-exit-status
make install
cd ~
rm -rf nginx-1.23.3
openssl req -new -newkey rsa:4096 -days 365 -nodes -x509 -subj "/C=US/ST=Denial/L=Chicago/O=Dis/CN=127.0.0.1" -keyout localhost.key  -out localhost.cert
sed -i "s/worker_processes  1;/worker_processes  auto;/g" nginx_/conf/nginx.conf
sed -i "s/        listen       80;/        listen       8089;/g" nginx_/conf/nginx.conf
sed -i "38 i ssl                  on;" nginx_/conf/nginx.conf
sed -i "38 i ssl_certificate      $HOME/localhost.cert;" nginx_/conf/nginx.conf
sed -i "38 i ssl_certificate_key   $HOME/localhost.key;" nginx_/conf/nginx.conf
sed -i "38 i ssl_ciphers          HIGH:!aNULL:!MD5;" nginx_/conf/nginx.conf
rm -rf wrk-4.2.0
tar -xf wrk-4.2.0.tar.gz
cd wrk-4.2.0

# Apply GCC 14 compatibility fix to wrk Makefile
# The original uses -std=c99 which doesn't support inline assembly (asm keyword)
# We need -std=gnu99 for OpenSSL's inline assembly to compile with GCC 14
echo "[INSTALL.SH] Applying GCC 14 fix to wrk Makefile" >&2

if [ -f "Makefile" ]; then
    echo "[INSTALL.SH] Original CFLAGS:" >&2
    grep "^CFLAGS" Makefile >&2

    # Apply the fix
    sed -i 's/-std=c99/-std=gnu99/g' Makefile

    echo "[INSTALL.SH] Patched CFLAGS:" >&2
    grep "^CFLAGS" Makefile >&2

    if grep -q "std=gnu99" Makefile; then
        echo "[INSTALL.SH] ✓ GCC 14 fix applied successfully" >&2
    else
        echo "[INSTALL.SH] ✗ ERROR: Failed to apply GCC 14 fix" >&2
        exit 1
    fi
else
    echo "[INSTALL.SH] ✗ ERROR: Makefile not found!" >&2
    exit 1
fi

make -j $NUM_CPU_CORES
echo $? > ~/install-exit-status
cd ~
mv -f http-test-files/* nginx_/html/
echo "#!/bin/sh
./wrk-4.2.0/wrk -t \$NUM_CPU_CORES \$@ > \$LOG_FILE 2>&1
echo \$? > ~/test-exit-status" > nginx
chmod +x nginx
