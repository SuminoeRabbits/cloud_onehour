# Cloud one hour project
GIving about "one hour" equivalent benchmark workload for cloud instance with Phoronix Test Suite (PTS). 

## TOC

- [Benchmark preparation](#benchmark-preparation)
- [Run benchmark](#run-benchmark)
- [Analyze results](#analyze-results)
- [version history](#version-history)   

## Benchmark preparation

### Automated setup (recommended)

This will install all dependencies including GCC-14, zlib, OpenSSL, and Phoronix Test Suite.
It will also configure passwordless sudo for automated benchmark runs.

```bash
cd cloud_onehour/scripts
./prepare_tools.sh
```

**Note**: You will be asked for your sudo password once during setup. After that, sudo commands will run without password prompts.

## Run benchmark

### Simple run

```bash
# Compiler environment is automatically loaded by run_pts_benchmark.sh
./scripts/run_pts_benchmark.sh coremark-1.0.1
./scripts/run_pts_benchmark.sh openssl-3.0.1
```

### Total run
Use and test_suite.json to run multiple tests at once in batch mode.

```
```

### Tune your test_suite.json

## Analyze results

## AWS CLI tips

### Install

### login


### Clean up
Make sure your instance has been clean up in EC2.
```
aws ec2 describe-instances \
    --query "Reservations[*].Instances[*].{Name:Tags[?Key=='Name'].Value|[0], ID:InstanceId, State:State.Name}" \
    --output table
```

Delete all instances in EC2.
```
aws ec2 terminate-instances --instance-ids $(aws ec2 describe-instances --filters "Name=instance-state-name,Values=pending,running,stopping,stopped" --query "Reservations[*].Instances[*].InstanceId" --output text)
```
## GCP CLI tips

### install

```
sudo apt-get update
sudo apt-get install apt-transport-https ca-certificates gnupg curl
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt-get update && sudo apt-get install google-cloud-cli
```

### login

```
gcloud auth login
# プロジェクト一覧を表示して ID (PROJECT_ID) を確認
gcloud projects list

# 確認したIDをデフォルトに設定（例: gcloud config set project my-bench-12345）
gcloud config set project [あなたのプロジェクトID]
```

Firewall setting.

```
# 自分のIPを取得
MY_IP=$(curl -s https://checkip.amazonaws.com)
# ファイアウォールルールを作成
gcloud compute firewall-rules create allow-ssh-from-home \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:22 \
    --source-ranges=$MY_IP/32
```

Confirm your GCP is Ok.
```
gcloud config list
```
### clean up

```
gcloud compute instances list
gcloud compute instances list --format="table(name,zone,status,externalIp)"
# 全てのインスタンスを強制的に削除する（確認ダイアログなし）
gcloud compute instances delete $(gcloud compute instances list --format="value(name)") --quiet
```


## version history

