#!/bin/sh

tar -xf 7z2200-src.tar.xz
cd CPP/7zip/Bundles/Alone2
# Patch makefile to remove -Werror
sed -i 's/-Werror/-Wno-error=dangling-pointer/g' ../../7zip_gcc.mak
make -j $NUM_CPU_CORES -f makefile.gcc
echo $? > ~/install-exit-status
cd ~
echo "#!/bin/sh
./CPP/7zip/Bundles/Alone2/_o/7zz b -mmt=\$NUM_CPU_CORES > \$LOG_FILE 2>&1
echo \$? > ~/test-exit-status" > compress-7zip
chmod +x compress-7zip
