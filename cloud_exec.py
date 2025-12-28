import json
import subprocess
import os
import time
import sys
from pathlib import Path

def run_cmd(cmd, capture=True, ignore=False, verbose=False):
    """Execute shell command and return output or status."""
    try:
        if verbose:
            print(f"[CMD] {cmd}")
        res = subprocess.run(
            cmd, shell=True, capture_output=capture, text=True, check=not ignore
        )
        return res.stdout.strip() if capture else True
    except subprocess.CalledProcessError as e:
        if not ignore:
            print(f"[Error] {e.stderr}")
        return None

def get_gcp_project():
    """Detect GCP project ID from gcloud config."""
    project = run_cmd("gcloud config get-value project")
    return project if project and "(unset)" not in project else None

def setup_aws_sg(region, sg_name):
    """Create/retrieve AWS security group and authorize SSH access from current IP."""
    sg_id = run_cmd(
        f"aws ec2 describe-security-groups --region {region} --group-names {sg_name} "
        f"--query 'SecurityGroups[0].GroupId' --output text",
        ignore=True,
    )
    if not sg_id or sg_id == "None":
        vpc_id = run_cmd(
            f"aws ec2 describe-vpcs --region {region} --query 'Vpcs[0].VpcId' --output text"
        )
        sg_id = run_cmd(
            f"aws ec2 create-security-group --group-name {sg_name} "
            f"--description 'SG for benchmarking' --vpc-id {vpc_id} --region {region} "
            f"--query 'GroupId' --output text"
        )
    my_ip = run_cmd("curl -s https://checkip.amazonaws.com")
    run_cmd(
        f"aws ec2 authorize-security-group-ingress --group-id {sg_id} "
        f"--protocol tcp --port 22 --cidr {my_ip}/32 --region {region}",
        ignore=True,
    )
    return sg_id

def launch_aws_instance(inst, config, region, key_name, sg_id):
    """Launch AWS instance and return (instance_id, ip)."""
    ami = run_cmd(
        f"aws ec2 describe-images --region {region} --owners 099720109477 "
        f"--filters 'Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-*-{config['common']['os_version']}-{inst['arch']}-server-*' "
        f"--query 'reverse(sort_by(Images, &CreationDate))[:1] | [0].ImageId' --output text"
    )
    instance_id = run_cmd(
        f"aws ec2 run-instances --region {region} --image-id {ami} "
        f"--instance-type {inst['type']} --key-name {key_name} "
        f"--security-group-ids {sg_id} --query 'Instances[0].InstanceId' --output text"
    )
    run_cmd(f"aws ec2 wait instance-running --region {region} --instance-ids {instance_id}")
    ip = run_cmd(
        f"aws ec2 describe-instances --region {region} --instance-ids {instance_id} "
        f"--query 'Reservations[0].Instances[0].PublicIpAddress' --output text"
    )
    return instance_id, ip


def launch_gcp_instance(inst, config, project, zone):
    """Launch GCP instance and return (instance_id, ip)."""
    name = inst['name']
    img_arch = "arm64" if inst['arch'] == "arm64" else "amd64"
    ip = run_cmd(
        f"gcloud compute instances create {name} --project={project} "
        f"--zone={zone} --machine-type={inst['type']} "
        f"--image-family=ubuntu-2404-lts-{img_arch} --image-project=ubuntu-os-cloud "
        f"--format='get(networkInterfaces[0].accessConfigs[0].natIP)'"
    )
    return name if ip else None, ip


def run_ssh_benchmarks(ip, config, inst, key_path, ssh_strict_host_key_checking):
    """Execute benchmark commands via SSH."""
    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_timeout = config['common'].get('ssh_timeout', 20)
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk} -o ConnectTimeout={ssh_timeout}"
    ssh_user = config['common']['ssh_user']

    for i in range(1, 4):
        cmd = config['common'][f"benchmark_command{i}"].format(vcpus=inst['vcpus'])
        print(f"  [Step {i}] Executing...")
        run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{cmd}'", capture=False)


def collect_results(ip, config, cloud, name, key_path, ssh_strict_host_key_checking):
    """Collect benchmark results from remote instance."""
    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk}"
    ssh_user = config['common']['ssh_user']
    rep_dir = config['common']['reports_dir']

    run_cmd(
        f"ssh {ssh_opt} {ssh_user}@{ip} "
        f"'tar -czf /tmp/reports.tar.gz -C $(dirname {rep_dir}) $(basename {rep_dir})'",
        capture=False,
    )
    local_f = f"{config['common']['local_log_dir']}/{cloud}_{name}.tar.gz"
    run_cmd(f"scp {ssh_opt} {ssh_user}@{ip}:/tmp/reports.tar.gz {local_f}")
    print(f"Collected: {local_f}")


def process_instance(cloud, inst, config, key_path):
    """Process a single cloud instance: launch, benchmark, collect, terminate."""
    name = inst['name']
    print(f"\n>>> Starting {cloud.upper()}: {name} ({inst['type']})")
    instance_id = None
    ip = None
    region = None
    project = None
    zone = None

    try:
        if cloud == 'aws':
            region = config['aws']['region']
            key_name = run_cmd("aws ec2 describe-key-pairs --query 'KeyPairs[0].KeyName' --output text")
            sg_id = setup_aws_sg(region, config['common']['security_group_name'])
            instance_id, ip = launch_aws_instance(inst, config, region, key_name, sg_id)
        elif cloud == 'gcp':
            project = config['gcp']['project_id']
            if project == "AUTO_DETECT":
                project = get_gcp_project()
            zone = config['gcp']['zone']
            instance_id, ip = launch_gcp_instance(inst, config, project, zone)
        else:
            print(f"[Error] Unknown cloud provider: {cloud}")
            return

        if not ip or ip == "None":
            print(f"[Error] Failed to get IP for {name}. Skipping benchmark.")
            return

        print(f"IP: {ip}. Waiting 60s for SSH...")
        time.sleep(60)

        ssh_strict = config['common'].get('ssh_strict_host_key_checking', False)
        run_ssh_benchmarks(ip, config, inst, key_path, ssh_strict)
        collect_results(ip, config, cloud, name, key_path, ssh_strict)

    except Exception as e:
        print(f"[Error] {cloud} instance {name}: {e}")
    finally:
        if instance_id:
            print(f"Terminating {name}...")
            if cloud == 'aws' and region:
                run_cmd(f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}")
            elif cloud == 'gcp' and project and zone:
                run_cmd(f"gcloud compute instances delete {name} --project={project} --zone={zone} --quiet")

def load_config(config_path='cloud_config.json'):
    """Load benchmark configuration from JSON file."""
    if not os.path.exists(config_path):
        print(f"[Error] Configuration file not found: {config_path}")
        print(f"[Info] Copy cloud_config.example.json to {config_path} and update settings.")
        return None
    with open(config_path, 'r') as f:
        return json.load(f)


def validate_key_path(key_path_template):
    """Expand and validate SSH key path."""
    key_path = os.path.expanduser(
        key_path_template.replace('${HOME}', os.environ.get('HOME', '~'))
    )
    if not os.path.exists(key_path):
        print(f"[Error] SSH key not found: {key_path}")
        return None
    return key_path


def main():
    """Main entry point for cloud benchmarking executor."""
    config = load_config()
    if not config:
        return

    os.makedirs(config['common']['local_log_dir'], exist_ok=True)

    # Validate SSH key
    key_path_template = config['common'].get(
        'ssh_key_path', '${HOME}/.ssh/cloud_onehour_project.pem'
    )
    key_path = validate_key_path(key_path_template)
    if not key_path:
        return

    # Process AWS instances
    if config['aws']['enable']:
        for inst in config['aws']['instances']:
            if inst.get('enable'):
                process_instance('aws', inst, config, key_path)

    # Process GCP instances
    if config['gcp']['enable']:
        for inst in config['gcp']['instances']:
            if inst.get('enable'):
                process_instance('gcp', inst, config, key_path)


if __name__ == "__main__":
    main()