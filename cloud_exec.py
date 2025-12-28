import json
import subprocess
import os
import time
import sys

def run_command(cmd, capture=True, ignore_error=False):
    """コマンド実行の共通ラッパー"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=capture, text=True, check=not ignore_error)
        return result.stdout.strip() if capture else True
    except subprocess.CalledProcessError as e:
        if not ignore_error:
            print(f"\n[Error] Command failed: {cmd}\n{e.stderr}")
        return None

def get_my_ip():
    """現在のパブリックIPを取得"""
    return run_command("curl -s https://checkip.amazonaws.com")

def setup_aws_security_group(region, sg_name):
    """SSH(22番)を許可するセキュリティグループを自動作成/取得"""
    # 既存SGの確認
    sg_id = run_command(f"aws ec2 describe-security-groups --region {region} --group-names {sg_name} --query 'SecurityGroups[0].GroupId' --output text", ignore_error=True)
    
    if not sg_id or sg_id == "None":
        print(f"Creating new security group: {sg_name}")
        vpc_id = run_command(f"aws ec2 describe-vpcs --region {region} --query 'Vpcs[0].VpcId' --output text")
        sg_id = run_command(f"aws ec2 create-security-group --group-name {sg_name} --description 'SG for benchmarking' --vpc-id {vpc_id} --region {region} --query 'GroupId' --output text")
    
    # 自分のIPからのSSHを許可（ルールが既にあればエラーになるので無視）
    my_ip = get_my_ip()
    print(f"Authorizing SSH access from your IP: {my_ip}")
    run_command(f"aws ec2 authorize-security-group-ingress --group-id {sg_id} --protocol tcp --port 22 --cidr {my_ip}/32 --region {region}", ignore_error=True)
    
    return sg_id

def get_aws_ami(region, arch, os_ver):
    pattern = f"ubuntu/images/hvm-ssd-gp3/ubuntu-*-{os_ver}-{arch}-server-*"
    cmd = (f"aws ec2 describe-images --region {region} --owners 099720109477 "
           f"--filters 'Name=name,Values={pattern}' "
           f"--query 'reverse(sort_by(Images, &CreationDate))[:1] | [0].ImageId' --output text")
    return run_command(cmd)

def get_aws_key_info(region):
    cmd = f"aws ec2 describe-key-pairs --region {region} --query 'KeyPairs[*].KeyName' --output json"
    out = run_command(cmd); keys = json.loads(out) if out else []
    if not keys: return None, None
    key_name = keys[0]
    key_path = os.path.expanduser(f"~/.ssh/{key_name}.pem")
    return key_name, key_path

def main():
    with open('cloud_config.json', 'r') as f:
        config = json.load(f)

    os.makedirs(config['common']['local_log_dir'], exist_ok=True)
    ssh_opt = "-o StrictHostKeyChecking=no -o ConnectTimeout=20"

    if config['aws']['enable']:
        region = config['aws']['region']
        key_name, key_path = get_aws_key_info(region)
        if not key_name or not os.path.exists(key_path):
            print(f"Key error: {key_path} not found"); sys.exit(1)

        # セキュリティグループの準備
        sg_id = setup_aws_security_group(region, config['common']['security_group_name'])

        for inst in config['aws']['instances']:
            if not inst.get('enable'): continue
            instance_id = None
            try:
                print(f"\n>>> AWS Launching: {inst['name']} ({inst['type']})")
                ami_id = get_aws_ami(region, inst['arch'], config['common']['os_version'])
                
                # インスタンス起動（SGを指定）
                launch_cmd = (f"aws ec2 run-instances --region {region} --image-id {ami_id} "
                              f"--instance-type {inst['type']} --key-name {key_name} "
                              f"--security-group-ids {sg_id} "
                              f"--tag-specifications 'ResourceType=instance,Tags=[{{Key=Name,Value={inst['name']}}}]' "
                              f"--query 'Instances[0].InstanceId' --output text")
                instance_id = run_command(launch_cmd)
                
                print("Waiting for instance to be 'running'...")
                run_command(f"aws ec2 wait instance-running --region {region} --instance-ids {instance_id}")
                ip = run_command(f"aws ec2 describe-instances --region {region} --instance-ids {instance_id} --query 'Reservations[0].Instances[0].PublicIpAddress' --output text")
                
                print(f"IP: {ip}. Waiting 60s for SSH daemon...")
                time.sleep(60) # SSH起動待ち時間を延長

                # Steps 1-3
                for i in range(1, 4):
                    cmd = config['common'][f"benchmark_command{i}"].format(vcpus=inst['vcpus'])
                    print(f"[Step {i}] Executing..."); run_command(f"ssh -i {key_path} {ssh_opt} ubuntu@{ip} '{cmd}'", capture=False)

                # Directory check and Archive
                rep_dir = config['common']['reports_dir']
                check_cmd = f"ssh -i {key_path} {ssh_opt} ubuntu@{ip} 'test -d {rep_dir}'"
                if subprocess.run(check_cmd, shell=True).returncode == 0:
                    print(f"Archiving {rep_dir}...")
                    run_command(f"ssh -i {key_path} {ssh_opt} ubuntu@{ip} 'tar -czf /tmp/reports.tar.gz -C $(dirname {rep_dir}) $(basename {rep_dir})'", capture=False)
                    local_f = f"{config['common']['local_log_dir']}/{inst['name']}_reports.tar.gz"
                    run_command(f"scp -i {key_path} {ssh_opt} ubuntu@{ip}:/tmp/reports.tar.gz {local_f}")
                    print(f"Downloaded: {local_f}")
                else:
                    print(f"[Warning] {rep_dir} not found.")

            except Exception as e: print(f"Error: {e}")
            finally:
                if instance_id:
                    print(f"Terminating {instance_id}..."); run_command(f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}")

if __name__ == "__main__":
    main()