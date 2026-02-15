# scripts_rhel9 概要
このディレクトリには、**RHEL9 (Red Hat Enterprise Linux 9) および Rocky Linux 9** などの互換OSを対象としたセットアップスクリプトが格納されています。オリジナルは [scripts/](../scripts/)であり、オリジナルに変更があった場合はこちらのスクリプトも影響を受けて変更されます。

## 特徴
- **パッケージ管理**: `apt-get` の代わりに `dnf` を使用します。
- **PHP 8.1**: Remi リポジトリを使用して PTS (Phoronix Test Suite) に必要な PHP 8.1 環境を構築します。
- **GCC 14**: `gcc-toolset-14` を優先的に使用し、互換性のために `/usr/local/bin/gcc-14` へのシンボリックリンクを作成します。

## 使い方
オリジナルの [scripts/](../scripts/)と同じ使い方です。
```
cloud_onehour/scripts_rhel9/prepare_tools.sh
```

## Dockerを用いたスクリプト検証

### 注意: PTSはrootユーザーでの実行を想定していません
PTS (Phoronix Test Suite) は root ユーザーで実行すると `/var/lib/phoronix-test-suite/` を使用し、
pts_runner スクリプトが期待する `~/.phoronix-test-suite/` とは異なる場所にインストールされます。
**非rootユーザーでの実行を推奨します。**

### RHEL9,RockyLinux9を使ったInteractive mode（非rootユーザー）での検証
AWS EC2形式に合わせて `ec2-user` を作成します。
```bash
docker run -it --rm --privileged \
  -v /home/snakajim/work/cloud_onehour/scripts_rhel9:/mnt/scripts_rhel9 \
  rockylinux:9 \
  bash -c "
    # 基本パッケージインストール
    dnf -y update && dnf -y install sudo git shadow-utils && \
    # 非rootユーザー作成（AWS EC2のec2-userを模倣）
    useradd -m -G wheel ec2-user && \
    echo 'ec2-user ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers && \
    # マウントしたscriptsをコピー
    cp -r /mnt/scripts_rhel9 /home/ec2-user/ && \
    chown -R ec2-user:ec2-user /home/ec2-user/scripts_rhel9 && \
    # ec2-userでリポジトリクローン
    su - ec2-user -c 'git clone https://github.com/SuminoeRabbits/cloud_onehour.git' && \
    # ec2-userに切り替えて対話型シェル起動
    su - ec2-user
  "
```

コンテナ内（ec2-user）で試験実行：
```bash
# 最小限のセットアップ（時間短縮）
cd ~/cloud_onehour/scripts_rhel9
./setup_init.sh
./setup_gcc14.sh
./setup_pts.sh

# PTS確認（~/.phoronix-test-suite/が使われることを確認）
phoronix-test-suite diagnostics | grep -E "PTS_USER_PATH|PTS_TEST_INSTALL"

# CoreMark実行
cd ~/cloud_onehour
./pts_runner/pts_runner_coremark-1.0.1.py 288 --quick
```


### RHEL10(rocky linux10)検証
`rockylinux:10`は2026年2月時点ではDocker HubにUpstreamされていないので代替として、`almalinux:10`を利用。

### Oracle Linux 9を使ったInteractive mode（非rootユーザー）での検証
OCI (Oracle Cloud Infrastructure) 形式に合わせて `opc` ユーザーを作成します。
```bash
docker run -it --rm --privileged \
  -v /home/snakajim/work/cloud_onehour/scripts_rhel9:/mnt/scripts_rhel9 \
  oraclelinux:9 \
  bash -c "
    # 基本パッケージインストール
    dnf -y update && dnf -y install sudo git shadow-utils && \
    # 非rootユーザー作成（OCI（Oracle Cloud）のopcを模倣）
    useradd -m -G wheel opc && \
    echo 'opc ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers && \
    # マウントしたscriptsをコピー
    cp -r /mnt/scripts_rhel9 /home/opc/ && \
    chown -R opc:opc /home/opc/scripts_rhel9 && \
    # opcユーザーでリポジトリクローン
    su - opc -c 'git clone https://github.com/SuminoeRabbits/cloud_onehour.git' && \
    # opcユーザーに切り替えて対話型シェル起動
    su - opc
  "
```

コンテナ内（opc）で試験実行：
```bash
# 最小限のセットアップ（時間短縮）
cd ~/cloud_onehour/scripts_rhel9
./setup_init.sh
./setup_gcc14.sh
./setup_pts.sh

# PTS確認（~/.phoronix-test-suite/が使われることを確認）
phoronix-test-suite diagnostics | grep -E "PTS_USER_PATH|PTS_TEST_INSTALL"

# CoreMark実行
cd ~/cloud_onehour
./pts_runner/pts_runner_coremark-1.0.1.py 288 --quick
```

## 注意事項
- 基本的な動作ロジックや各スクリプトの役割については、オリジナルの [scripts/](../scripts/) ディレクトリを参照してください。
- このディレクトリ内のスクリプトは、Ubuntu 版（`scripts/`）と同じインターフェースを維持しており、OS 判定後に透過的に使い分けられることを目的としています。
