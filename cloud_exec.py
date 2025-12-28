import json, subprocess, os, time, sys

def run_cmd(cmd, capture=True, ignore=False):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=capture, text=True, check=not ignore)
        return res.stdout.strip() if capture else True
    except subprocess.CalledProcessError as e:
        if not ignore: print(f"\n[Error] {e.stderr}")
        return None

def get_gcp_project():
    project = run_cmd("gcloud config get-value project")
    if not project or "(unset)" in project or project == "":
        return None
    return project

def setup_aws_sg(region, sg_name):
    sg_id = run_cmd(f"aws ec2 describe-security-groups --region {region} --group-names {sg_name} --query 'SecurityGroups[0].GroupId' --output text", ignore=True)
    if not sg_id or sg_id == "None":
        vpc_id = run_cmd(f"aws ec2 describe-vpcs --region {region} --query 'Vpcs[0].VpcId' --output text")
        sg_id = run_cmd(f"aws ec2 create-security-group --group-name {sg_name} --description 'SG for benchmarking' --vpc-id {vpc_id} --region {region} --query 'GroupId' --output text")
    my_ip = run_cmd("curl -s https://checkip.amazonaws.com")
    run_cmd(f"aws ec2 authorize-security-group-ingress --group-id {sg_id} --protocol tcp --port 22 --cidr {my_ip}/32 --region {region}", ignore=True)
    return sg_id

def process_instance(cloud, inst, config, key_path, region_info):
    name, itype = inst['name'], inst['type']
    print(f"\n>>> Starting {cloud.upper()}: {name} ({itype})")
    instance_id = None
    ip = None
    
    try:
        if cloud == 'aws':
            ami = run_cmd(f"aws ec2 describe-images --region {region_info['region']} --owners 099720109477 --filters 'Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-*-{config['common']['os_version']}-{inst['arch']}-server-*' --query 'reverse(sort_by(Images, &CreationDate))[:1] | [0].ImageId' --output text")
            launch_cmd = f"aws ec2 run-instances --region {region_info['region']} --image-id {ami} --instance-type {itype} --key-name {region_info['key_name']} --security-group-ids {region_info['sg_id']} --query 'Instances[0].InstanceId' --output text"
            instance_id = run_cmd(launch_cmd)
            run_cmd(f"aws ec2 wait instance-running --region {region_info['region']} --instance-ids {instance_id}")
            ip = run_cmd(f"aws ec2 describe-instances --region {region_info['region']} --instance-ids {instance_id} --query 'Reservations[0].Instances[0].PublicIpAddress' --output text")
        
        elif cloud == 'gcp':
            # GCPイメージ名の修正: x86-64 -> amd64
            img_arch = "arm64" if inst['arch'] == "arm64" else "amd64"
            launch_cmd = (f"gcloud compute instances create {name} --project={region_info['project']} "
                          f"--zone={region_info['zone']} --machine-type={itype} "
                          f"--image-family=ubuntu-2404-lts-{img_arch} --image-project=ubuntu-os-cloud "
                          f"--format='get(networkInterfaces[0].accessConfigs[0].natIP)'")
            ip = run_cmd(launch_cmd)
            if ip: instance_id = name 

        # IPが取得できていない（起動失敗）場合はSSH処理をスキップ
        if not ip or ip == "None":
            print(f"[Error] Failed to get IP for {name}. Skipping SSH steps.")
            return

        print(f"IP: {ip}. Waiting 60s for SSH...")
        time.sleep(60)

        ssh_opt = f"-i {key_path} -o StrictHostKeyChecking=no -o ConnectTimeout=20"
        for i in range(1, 4):
            cmd = config['common'][f"benchmark_command{i}"].format(vcpus=inst['vcpus'])
            print(f"  [Step {i}] Executing..."); run_cmd(f"ssh {ssh_opt} {config['common']['ssh_user']}@{ip} '{cmd}'", capture=False)

        rep_dir = config['common']['reports_dir']
        run_cmd(f"ssh {ssh_opt} {config['common']['ssh_user']}@{ip} 'tar -czf /tmp/reports.tar.gz -C $(dirname {rep_dir}) $(basename {rep_dir})'", capture=False)
        local_f = f"{config['common']['local_log_dir']}/{cloud}_{name}.tar.gz"
        run_cmd(f"scp {ssh_opt} {config['common']['ssh_user']}@{ip}:/tmp/reports.tar.gz {local_f}")
        print(f"Collected: {local_f}")

    except Exception as e: print(f"Error in {name}: {e}")
    finally:
        if instance_id:
            print(f"Terminating {name}...")
            if cloud == 'aws': run_cmd(f"aws ec2 terminate-instances --region {region_info['region']} --instance-ids {instance_id}")
            if cloud == 'gcp': run_cmd(f"gcloud compute instances delete {name} --project={region_info['project']} --zone={region_info['zone']} --quiet")

def main():
    if not os.path.exists('cloud_config.json'): return
    with open('cloud_config.json', 'r') as f: config = json.load(f)
    os.makedirs(config['common']['local_log_dir'], exist_ok=True)
    
    # 鍵のパスを確定
    key_path = os.path.expanduser("~/.ssh/cloud_onehour_project.pem")
    if not os.path.exists(key_path):
        print(f"CRITICAL ERROR: Key file not found at {key_path}")
        return

    if config['aws']['enable']:
        key_name = run_cmd("aws ec2 describe-key-pairs --query 'KeyPairs[0].KeyName' --output text")
        sg_id = setup_aws_sg(config['aws']['region'], config['common']['security_group_name'])
        for inst in config['aws']['instances']:
            if inst.get('enable'): process_instance('aws', inst, config, key_path, {'region': config['aws']['region'], 'key_name': key_name, 'sg_id': sg_id})

    if config['gcp']['enable']:
        p_id = config['gcp']['project_id']
        if p_id == "AUTO_DETECT": p_id = get_gcp_project()
        if p_id:
            for inst in config['gcp']['instances']:
                if inst.get('enable'): process_instance('gcp', inst, config, key_path, {'project': p_id, 'zone': config['gcp']['zone']})

if __name__ == "__main__":
    main()