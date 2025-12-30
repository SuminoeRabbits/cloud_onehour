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

### Test options configuration (required)

All benchmarks MUST have test-specific XML configuration files in `user_config/test-options/`. These files contain test-specific PTS settings that override the base configuration in `user_config/user-config.xml`.

**Configuration Architecture:**
- `user_config/user-config.xml` - Base PTS configuration (common settings for all tests)
- `user_config/test-options/pts_<benchmark-name>.config` - Test-specific overrides (XML format)

**How it works:**
1. The benchmark script loads `user_config/user-config.xml` as the base configuration
2. Test-specific settings from `user_config/test-options/pts_<benchmark-name>.config` are merged into the base config
3. The merged configuration is written to `~/.phoronix-test-suite/user-config.xml`
4. PTS uses the merged configuration for the test run

**XML Config File Format:**

Config file: `user_config/test-options/pts_coremark-1.0.1.config`
```xml
<?xml version="1.0"?>
<PhoronixTestSuite>
  <Options>
    <TestResultValidation>
      <DynamicRunCount>FALSE</DynamicRunCount>
      <LimitDynamicToTestLength>20</LimitDynamicToTestLength>
    </TestResultValidation>
  </Options>
  <TestOptions>
    <Test>
      <Identifier>pts/coremark-1.0.1</Identifier>
      <Option>1</Option>
    </Test>
  </TestOptions>
</PhoronixTestSuite>
```

Config file: `user_config/test-options/pts_openssl-3.6.0.config`
```xml
<?xml version="1.0"?>
<PhoronixTestSuite>
  <Options>
    <TestResultValidation>
      <DynamicRunCount>FALSE</DynamicRunCount>
      <LimitDynamicToTestLength>20</LimitDynamicToTestLength>
    </TestResultValidation>
  </Options>
  <TestOptions>
    <Test>
      <Identifier>pts/openssl-3.6.0</Identifier>
      <Option>3</Option>
    </Test>
  </TestOptions>
</PhoronixTestSuite>
```

**Key Elements:**
- `<Options>` - Overrides base PTS settings (e.g., DynamicRunCount, LimitDynamicToTestLength)
- `<TestOptions><Test><Option>` - Specifies which test option to select (e.g., "1", "3")

**Note**: All tests in `test_suite.json` already have pre-configured XML files. Modify these to customize test behavior per benchmark. The script is completely generic - all test-specific settings are in the XML config files.

## Run benchmark on local

### Simple run

```bash
# Compiler environment is automatically loaded by run_pts_benchmark.sh
./scripts/run_pts_benchmark.sh coremark-1.0.1 2>&1 | tee -a stdout.log
./scripts/run_pts_benchmark.sh openssl-3.0.1 2>&1 | tee -a stdout.log
```

## Run benchmark on cloud

### AWS CLI authentification

Check you have been authorized to use aws cli.
```
aws configure list
```

### Total run
Use and test_suite.json to run multiple tests at once in batch mode.

```
python3.10 cloud_exec.py 2>&1 | tee -a stdout.log
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
# 公開鍵を確実に生成（上書き）
ssh-keygen -y -f ~/.ssh/<your project key>.pem > ~/.ssh/<your project key>.pub
# メタデータへの登録（"ubuntu:" という接頭辞が重要です）
gcloud compute project-info add-metadata \
    --metadata ssh-keys="ubuntu:$(cat ~/.ssh/<your project key>.pub)"
#「OS Login」機能の無効化確認
gcloud compute project-info add-metadata --metadata enable-oslogin=FALSE
```

Confirm your GCP is Ok.
```
gcloud config list
```
### clean up

```bash
# インスタンス一覧を表示
gcloud compute instances list
gcloud compute instances list --format="table(name,zone,status,externalIp)"

# 全てのインスタンスを強制的に削除する（全ゾーン対応、確認ダイアログなし）
gcloud compute instances list --format="value(name,zone)" | while read name zone; do
  [ -n "$name" ] && gcloud compute instances delete "$name" --zone="$zone" --quiet
done

# または1行で（xargs使用）
gcloud compute instances list --format="value(name,zone)" | awk '{if($1!="") print $1, "--zone=" $2}' | xargs -r gcloud compute instances delete --quiet
```


## version history

