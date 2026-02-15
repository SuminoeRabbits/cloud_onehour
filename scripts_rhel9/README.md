# scripts_rhel9

## 概要
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
### RHEL9(rocky linux9)検証
```
# Rocky Linux 9
docker run -it --rm --privileged \
  -v /home/snakajim/work/cloud_onehour/scripts_rhel9:/root/scripts_rhel9 \
  rockylinux:9 \
  bash -c "dnf -y update && dnf -y install sudo git && cd && \
    git clone https://github.com/SuminoeRabbits/cloud_onehour.git && \
    cd ~/cloud_onehour/scripts_rhel9 && ./prepare_tools.sh && \
    cd ~/cloud_onehour && ./pts_runner/pts_runner_coremark-1.0.1.py 288"
```

### RHEL10(rocky linux10)検証
`rockylinux:10`は2026年2月時点ではDocker HubにUpstreamされていないが、いつかはされるだろう。
```
# Rocky Linux 10
docker run -it --rm --privileged \
  -v /home/snakajim/work/cloud_onehour/scripts_rhel9:/root/scripts_rhel9 \
  rockylinux:10 \
  bash -c "dnf -y update && dnf -y install sudo git && cd && \
    git clone https://github.com/SuminoeRabbits/cloud_onehour.git && \
    cd ~/cloud_onehour/scripts_rhel9 && ./prepare_tools.sh && \
    cd ~/cloud_onehour && ./pts_runner/pts_runner_coremark-1.0.1.py 288"
```

### Oracle Linux9検証
```
# Oracle Linux 9
docker run -it --rm --privileged \
  -v /home/snakajim/work/cloud_onehour/scripts_rhel9:/root/scripts_rhel9 \
  oraclelinux:9 \
  bash -c "dnf -y update && dnf -y install sudo git && cd && \
    git clone https://github.com/SuminoeRabbits/cloud_onehour.git && \
    cd ~/cloud_onehour/scripts_rhel9 && ./prepare_tools.sh && \
    cd ~/cloud_onehour && ./pts_runner/pts_runner_coremark-1.0.1.py 288"
```

## 注意事項
- 基本的な動作ロジックや各スクリプトの役割については、オリジナルの [scripts/](../scripts/) ディレクトリを参照してください。
- このディレクトリ内のスクリプトは、Ubuntu 版（`scripts/`）と同じインターフェースを維持しており、OS 判定後に透過的に使い分けられることを目的としています。
