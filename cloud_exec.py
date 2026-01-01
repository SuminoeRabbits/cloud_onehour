import json
import subprocess
import os
import time
import sys
import signal
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Global debug mode - set by load_config
DEBUG_MODE = False  # false, true, or other (progress-only)

# Global tracking of active instances for cleanup on signal
active_instances_lock = threading.Lock()
active_instances = []  # List of dicts: {'cloud': 'aws'/'gcp', 'instance_id': str, 'name': str, 'region': str, 'project': str, 'zone': str}

def cleanup_active_instances(signum=None, frame=None):
    """Terminate all active instances on signal or exception."""
    with active_instances_lock:
        if not active_instances:
            return

        if signum:
            signal_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
            print(f"\n[Signal] Received {signal_name}. Cleaning up active instances...", flush=True)
        else:
            print(f"\n[Cleanup] Terminating active instances...", flush=True)

        for inst_info in active_instances:
            try:
                cloud = inst_info['cloud']
                instance_id = inst_info['instance_id']
                name = inst_info['name']

                print(f"[Cleanup] Terminating {cloud}:{name} ({instance_id})...", flush=True)

                if cloud == 'aws':
                    region = inst_info.get('region')
                    subprocess.run(
                        f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}",
                        shell=True, capture_output=True, timeout=30
                    )
                elif cloud == 'gcp':
                    project = inst_info.get('project')
                    zone = inst_info.get('zone')
                    subprocess.run(
                        f"gcloud compute instances delete {name} --project={project} --zone={zone} --quiet",
                        shell=True, capture_output=True, timeout=30
                    )
                elif cloud == 'oci':
                    # TODO: Implement OCI instance termination
                    # subprocess.run(
                    #     f"oci compute instance terminate --instance-id {instance_id} --force",
                    #     shell=True, capture_output=True, timeout=30
                    # )
                    print(f"[Cleanup] OCI termination not yet implemented for {name}", flush=True)

                print(f"[Cleanup] {cloud}:{name} terminated", flush=True)
            except Exception as e:
                print(f"[Cleanup] Failed to terminate {inst_info.get('name', 'unknown')}: {e}", flush=True)

        active_instances.clear()

    if signum:
        print(f"[Signal] Cleanup complete. Exiting.", flush=True)
        sys.exit(1)

def register_instance(cloud, instance_id, name, region=None, project=None, zone=None, compartment_id=None):
    """Register an instance as active for cleanup tracking."""
    with active_instances_lock:
        active_instances.append({
            'cloud': cloud,
            'instance_id': instance_id,
            'name': name,
            'region': region,
            'project': project,
            'zone': zone,
            'compartment_id': compartment_id
        })
        if DEBUG_MODE == True:
            log(f"Registered instance for cleanup: {cloud}:{name} ({instance_id})")

def unregister_instance(instance_id):
    """Remove an instance from active tracking."""
    with active_instances_lock:
        active_instances[:] = [inst for inst in active_instances if inst['instance_id'] != instance_id]
        if DEBUG_MODE == True:
            log(f"Unregistered instance: {instance_id}")

def log(msg, level="INFO"):
    """Print timestamped log message if debug mode is enabled."""
    if DEBUG_MODE == True:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {msg}", flush=True)
    elif DEBUG_MODE not in [False, True]:
        # Progress-only mode - don't print this, handle separately
        pass

def progress(instance_name, step):
    """Print progress indicator for progress-only mode."""
    if DEBUG_MODE not in [False, True]:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{instance_name}] {step}", flush=True)
    elif DEBUG_MODE == True:
        log(f"{instance_name}: {step}", "PROGRESS")

def run_cmd(cmd, capture=True, ignore=False, timeout=None):
    """Execute shell command and return output or status."""
    try:
        if DEBUG_MODE == True:
            log(f"Executing: {cmd[:100]}{'...' if len(cmd) > 100 else ''}", "CMD")

        start_time = time.time()
        res = subprocess.run(
            cmd, shell=True, capture_output=capture, text=True, check=not ignore, timeout=timeout
        )
        elapsed = time.time() - start_time

        if DEBUG_MODE == True:
            log(f"Command completed in {elapsed:.2f}s", "CMD")

        return res.stdout.strip() if capture else True
    except subprocess.TimeoutExpired:
        if DEBUG_MODE == True:
            log(f"Command timed out after {timeout} seconds", "ERROR")
        elif DEBUG_MODE == False:
            print(f"[Error] Command timed out after {timeout} seconds")
        if not ignore:
            raise
        return None
    except subprocess.CalledProcessError as e:
        if DEBUG_MODE == True:
            log(f"Command failed: {e.stderr if e.stderr else 'No error message'}", "ERROR")
        elif DEBUG_MODE == False:
            print(f"[Error] {e.stderr if e.stderr else 'Command failed'}")
        if not ignore:
            raise
        return None

def get_gcp_project():
    """Detect GCP project ID from gcloud config."""
    if DEBUG_MODE == True:
        log("Detecting GCP project ID...")
    project = run_cmd("gcloud config get-value project")
    if project and "(unset)" not in project:
        if DEBUG_MODE == True:
            log(f"GCP project: {project}")
        return project
    if DEBUG_MODE == True:
        log("GCP project not configured", "WARN")
    return None

def setup_aws_sg(region, sg_name):
    """Create/retrieve AWS security group and authorize SSH access from current IP."""
    if DEBUG_MODE == True:
        log(f"Setting up AWS security group: {sg_name} in {region}")
        log("Checking for existing security group...")

    sg_id = run_cmd(
        f"aws ec2 describe-security-groups --region {region} --group-names {sg_name} "
        f"--query 'SecurityGroups[0].GroupId' --output text",
        ignore=True,
    )

    if not sg_id or sg_id == "None":
        if DEBUG_MODE == True:
            log("Security group not found, creating new one...")
        vpc_id = run_cmd(
            f"aws ec2 describe-vpcs --region {region} --query 'Vpcs[0].VpcId' --output text"
        )
        if DEBUG_MODE == True:
            log(f"Using VPC: {vpc_id}")

        sg_id = run_cmd(
            f"aws ec2 create-security-group --group-name {sg_name} "
            f"--description 'SG for benchmarking' --vpc-id {vpc_id} --region {region} "
            f"--query 'GroupId' --output text"
        )
        if DEBUG_MODE == True:
            log(f"Created security group: {sg_id}")
    else:
        if DEBUG_MODE == True:
            log(f"Using existing security group: {sg_id}")

    if DEBUG_MODE == True:
        log("Getting current public IP...")
    my_ip = run_cmd("curl -s https://checkip.amazonaws.com")
    if DEBUG_MODE == True:
        log(f"Current IP: {my_ip}")
        log(f"Authorizing SSH access from {my_ip}/32...")

    run_cmd(
        f"aws ec2 authorize-security-group-ingress --group-id {sg_id} "
        f"--protocol tcp --port 22 --cidr {my_ip}/32 --region {region}",
        ignore=True,
    )
    if DEBUG_MODE == True:
        log("Security group configured")

    return sg_id

def launch_aws_instance(inst, config, region, key_name, sg_id):
    """Launch AWS instance and return (instance_id, ip)."""
    if DEBUG_MODE == True:
        log(f"Launching AWS instance: {inst['name']} ({inst['type']})")

    os_version = config['common']['os_version']
    version_to_codename = {
        '20.04': 'focal',
        '22.04': 'jammy',
        '24.04': 'noble',
        '25.04': 'plucky'
    }
    codename = version_to_codename.get(os_version, 'jammy')

    if DEBUG_MODE == True:
        log(f"Finding AMI for Ubuntu {os_version} ({codename}) {inst['arch']}...")

    # Try multiple AMI patterns (gp3, gp2, standard ssd) to find the latest image
    # Pattern priority: hvm-ssd-gp3 (newest), hvm-ssd (older)
    ami_patterns = [
        f"ubuntu/images/hvm-ssd-gp3/ubuntu-{codename}-{os_version}-{inst['arch']}-server-*",
        f"ubuntu/images/hvm-ssd/ubuntu-{codename}-{os_version}-{inst['arch']}-server-*",
        f"ubuntu/images/*ubuntu-{codename}-{os_version}-{inst['arch']}-server-*"
    ]

    ami = None
    for pattern in ami_patterns:
        if DEBUG_MODE == True:
            log(f"Trying AMI pattern: {pattern}")

        ami = run_cmd(
            f"aws ec2 describe-images --region {region} --owners 099720109477 "
            f"--filters 'Name=name,Values={pattern}' "
            f"--query 'reverse(sort_by(Images, &CreationDate))[:1] | [0].ImageId' --output text"
        )

        if ami and ami != "None" and ami.strip():
            if DEBUG_MODE == True:
                log(f"Found AMI with pattern '{pattern}': {ami}")
            break


    if not ami or ami == "None":
        msg = f"No AMI found for Ubuntu {os_version} ({codename}) {inst['arch']} in {region}"
        if DEBUG_MODE == True:
            log(msg, "ERROR")
        else:
            print(f"[Error] {msg}")
        return None, None

    if DEBUG_MODE == True:
        log(f"Using AMI: {ami}")
        log("Starting instance...")

    instance_id = run_cmd(
        f"aws ec2 run-instances --region {region} --image-id {ami} "
        f"--instance-type {inst['type']} --key-name {key_name} "
        f"--security-group-ids {sg_id} --query 'Instances[0].InstanceId' --output text"
    )

    if DEBUG_MODE == True:
        log(f"Instance ID: {instance_id}")
        log("Waiting for instance to be running...")

    run_cmd(f"aws ec2 wait instance-running --region {region} --instance-ids {instance_id}")

    ip = run_cmd(
        f"aws ec2 describe-instances --region {region} --instance-ids {instance_id} "
        f"--query 'Reservations[0].Instances[0].PublicIpAddress' --output text"
    )

    if DEBUG_MODE == True:
        log(f"Instance running with IP: {ip}")

    return instance_id, ip


def launch_gcp_instance(inst, config, project, zone):
    """Launch GCP instance and return (instance_id, ip)."""
    name = inst['name']

    if DEBUG_MODE == True:
        log(f"Launching GCP instance: {name} ({inst['type']})")

    os_version = config['common']['os_version']
    img_arch = "arm64" if inst['arch'] == "arm64" else "amd64"

    version_number = os_version.replace('.', '')
    is_lts = os_version.endswith('.04') and int(os_version.split('.')[0]) % 2 == 0
    lts_suffix = "-lts" if is_lts else ""
    image_family = f"ubuntu-{version_number}{lts_suffix}-{img_arch}"

    if DEBUG_MODE == True:
        log(f"Using image family: {image_family}")
        log("Creating instance...")

    ip = run_cmd(
        f"gcloud compute instances create {name} --project={project} "
        f"--zone={zone} --machine-type={inst['type']} "
        f"--image-family={image_family} --image-project=ubuntu-os-cloud "
        f"--format='get(networkInterfaces[0].accessConfigs[0].natIP)'"
    )

    if ip:
        if DEBUG_MODE == True:
            log(f"Instance created with IP: {ip}")
    else:
        if DEBUG_MODE == True:
            log("Failed to create instance", "ERROR")
        else:
            print("[Error] Failed to create GCP instance")

    return name if ip else None, ip


def launch_oci_instance(inst, config, compartment_id, region):
    """Launch OCI instance and return (instance_id, ip).

    TODO: Implement OCI instance launch using OCI CLI
    Reference: oci compute instance launch --compartment-id <id> --shape <type> ...
    """
    name = inst['name']

    if DEBUG_MODE == True:
        log(f"Launching OCI instance: {name} ({inst['type']})")
        log("OCI launch not yet implemented", "WARN")
    else:
        print(f"[Warning] OCI instance launch not yet implemented for {name}")

    # TODO: Implement OCI instance creation logic
    # instance_id = run_cmd(f"oci compute instance launch ...")
    # ip = run_cmd(f"oci compute instance get --instance-id {instance_id} ...")

    return None, None


def verify_ssh_build(ip, ssh_opt, ssh_user, instance_name, auto_rollback=True):
    """
    Verify SSH build status after build_openssh.sh execution.

    Args:
        ip: Remote instance IP address
        ssh_opt: SSH options string
        ssh_user: SSH username
        instance_name: Instance name for logging
        auto_rollback: If True, automatically rollback on verification failure

    Returns:
        bool: True if build succeeded, False otherwise
    """
    try:
        if DEBUG_MODE == True:
            log("Verifying SSH build status...")

        # Wait for delayed SSH restart to complete
        if DEBUG_MODE == True:
            log("Waiting 10 seconds for SSH service restart...")
        time.sleep(10)

        # Check build status file
        status_cmd = f"ssh {ssh_opt} {ssh_user}@{ip} 'cat /tmp/ssh_build_status.txt 2>/dev/null || echo UNKNOWN'"
        status = run_cmd(status_cmd, capture=True, timeout=10)

        if status == "SUCCESS":
            if DEBUG_MODE == True:
                log("SSH build verified successfully", "INFO")
            elif DEBUG_MODE == False:
                print("  [SSH Build] ✓ Verification successful")
            progress(instance_name, "SSH build verified")
            return True
        else:
            if DEBUG_MODE == True:
                log(f"SSH build verification failed: {status}", "ERROR")
            elif DEBUG_MODE == False:
                print(f"  [SSH Build] ✗ Verification failed: {status}")
            progress(instance_name, f"SSH build failed: {status}")

            # Attempt automatic rollback
            if auto_rollback and status == "FAILED":
                if DEBUG_MODE == True:
                    log("Attempting automatic rollback to previous SSH version...")
                elif DEBUG_MODE == False:
                    print("  [SSH Build] Attempting rollback to previous version...")

                rollback_cmd = (
                    f"ssh {ssh_opt} {ssh_user}@{ip} '"
                    f"BACKUP_DIR=$(ls -td /var/backups/ssh-pre-upgrade-* 2>/dev/null | head -1) && "
                    f"[ -n \"$BACKUP_DIR\" ] && [ -f \"$BACKUP_DIR/sshd.orig\" ] && "
                    f"sudo mv \"$BACKUP_DIR/sshd.orig\" /usr/sbin/sshd && "
                    f"sudo systemctl restart ssh && "
                    f"echo ROLLBACK_SUCCESS || echo ROLLBACK_FAILED'"
                )

                rollback_result = run_cmd(rollback_cmd, capture=True, timeout=30, ignore=True)

                if rollback_result == "ROLLBACK_SUCCESS":
                    if DEBUG_MODE == True:
                        log("Rollback successful, SSH restored to previous version", "INFO")
                    elif DEBUG_MODE == False:
                        print("  [SSH Build] ✓ Rollback successful")
                else:
                    if DEBUG_MODE == True:
                        log(f"Rollback failed: {rollback_result}", "ERROR")
                    elif DEBUG_MODE == False:
                        print(f"  [SSH Build] ✗ Rollback failed: {rollback_result}")

            return False

    except Exception as e:
        if DEBUG_MODE == True:
            log(f"SSH build verification error: {e}", "ERROR")
        elif DEBUG_MODE == False:
            print(f"  [SSH Build] ✗ Verification error: {e}")
        return False


def run_ssh_commands(ip, config, inst, key_path, ssh_strict_host_key_checking, instance_name):
    """Execute all commands via SSH sequentially with output displayed."""
    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_connect_timeout = config['common'].get('ssh_timeout', 20)
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk} -o UserKnownHostsFile=/dev/null -o ConnectTimeout={ssh_connect_timeout} -o ServerAliveInterval=60 -o ServerAliveCountMax=10"
    ssh_user = config['common']['ssh_user']
    command_timeout = config['common'].get('command_timeout', 10800)

    # Support both old format (setup_command1, etc.) and new format (commands array)
    commands = []
    if 'commands' in config['common']:
        # New format: array of commands
        commands = config['common']['commands']
    else:
        # Old format: fallback for backward compatibility
        for i in range(1, 10):  # Support up to 9 setup commands
            cmd_key = f"setup_command{i}"
            if cmd_key in config['common']:
                cmd = config['common'][cmd_key]
                if cmd and cmd.strip():
                    commands.append(cmd)
        for i in range(1, 10):  # Support up to 9 benchmark commands
            cmd_key = f"benchmark_command{i}"
            if cmd_key in config['common']:
                cmd = config['common'][cmd_key]
                if cmd and cmd.strip():
                    commands.append(cmd)

    if not commands:
        if DEBUG_MODE == True:
            log("No commands to execute", "WARNING")
        return False

    total_commands = len(commands)
    progress(instance_name, f"Command execution started ({total_commands} commands)")

    if DEBUG_MODE == True:
        log(f"Starting command execution for {ip} ({total_commands} commands)")
    elif DEBUG_MODE == False:
        print(f"  [Commands] Starting execution of {total_commands} commands...")

    for i, cmd in enumerate(commands, start=1):
        # Format command with vcpu substitution
        cmd = cmd.format(vcpus=inst['vcpus'])

        if not cmd or cmd.strip() == "":
            continue

        progress(instance_name, f"Command {i}/{total_commands}")

        if DEBUG_MODE == True:
            log(f"Command {i}/{total_commands}: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            log(f"Timeout: {command_timeout}s ({command_timeout//60} minutes)")
        elif DEBUG_MODE == False:
            print(f"  [Command {i}/{total_commands}] Executing: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            print(f"  [Command {i}/{total_commands}] Timeout: {command_timeout}s ({command_timeout//60} minutes)")

        result = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{cmd}'", capture=False, timeout=command_timeout)

        if result is None:
            if DEBUG_MODE == True:
                log(f"Command {i}/{total_commands} failed or timed out", "ERROR")
            elif DEBUG_MODE == False:
                print(f"  [Warning] Command {i}/{total_commands} failed or timed out")
            return False

        if DEBUG_MODE == True:
            log(f"Command {i}/{total_commands} completed")
        elif DEBUG_MODE == False:
            print(f"  [Command {i}/{total_commands}] Completed")

        # Special handling: Verify SSH build after SSH-related scripts execution
        # Detects both direct execution and execution via wrapper scripts
        ssh_build_indicators = ['build_openssh.sh', 'prepare_tools.sh']
        if any(indicator in cmd for indicator in ssh_build_indicators):
            # Check if SSH build actually occurred by looking for status file
            try:
                status_check = run_cmd(
                    f"ssh {ssh_opt} {ssh_user}@{ip} 'test -f /tmp/ssh_build_status.txt && echo EXISTS || echo NOTFOUND'",
                    capture=True, timeout=5, ignore=True
                )

                if status_check == "EXISTS":
                    # SSH build was executed, verify it
                    if DEBUG_MODE == True:
                        log("Detected SSH build execution, verifying build status...")
                    elif DEBUG_MODE == False:
                        print("  [SSH Build] Verifying OpenSSH installation...")

                    if not verify_ssh_build(ip, ssh_opt, ssh_user, instance_name):
                        if DEBUG_MODE == True:
                            log("SSH build verification failed, aborting command execution", "ERROR")
                        elif DEBUG_MODE == False:
                            print("  [Error] SSH build verification failed")
                        return False
                else:
                    # Status file doesn't exist - SSH build was not executed (skip verification)
                    if DEBUG_MODE == True:
                        log("SSH build script detected but no status file found (build may have been skipped)")
            except Exception as e:
                # Verification check failed, but continue (don't block on verification issues)
                if DEBUG_MODE == True:
                    log(f"SSH build verification check failed: {e}, continuing...", "WARNING")

    progress(instance_name, "All commands completed")

    if DEBUG_MODE == True:
        log("All commands completed successfully")

    return True


def collect_results(ip, config, cloud, name, key_path, ssh_strict_host_key_checking, instance_name):
    """Collect benchmark results from remote instance."""
    progress(instance_name, "Collecting results")

    if DEBUG_MODE == True:
        log(f"Collecting results from {ip}")

    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk} -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=60 -o ServerAliveCountMax=10"
    ssh_user = config['common']['ssh_user']
    cloud_rep_dir = config['common']['cloud_reports_dir']

    if DEBUG_MODE == True:
        log("Creating tarball on remote instance...")

    run_cmd(
        f"ssh {ssh_opt} {ssh_user}@{ip} "
        f"'tar -czf /tmp/reports.tar.gz -C $(dirname {cloud_rep_dir}) $(basename {cloud_rep_dir})'",
        capture=False,
    )

    host_rep_dir = config['common']['host_reports_dir']
    local_f = f"{host_rep_dir}/{cloud}_{name}.tar.gz"

    if DEBUG_MODE == True:
        log(f"Downloading to {local_f}...")

    run_cmd(f"scp {ssh_opt} {ssh_user}@{ip}:/tmp/reports.tar.gz {local_f}")

    progress(instance_name, "Results collected")

    if DEBUG_MODE == True:
        log(f"Results collected: {local_f}")
    else:
        print(f"Collected: {local_f}")


def process_instance(cloud, inst, config, key_path):
    """Process a single cloud instance: launch, benchmark, collect, terminate."""
    name = inst['name']
    instance_name = f"{cloud.upper()}:{name}"

    progress(instance_name, "Starting")

    if DEBUG_MODE == True:
        log(f"Starting {cloud.upper()} instance: {name} ({inst['type']})", "INFO")
    else:
        print(f"\n>>> Starting {cloud.upper()}: {name} ({inst['type']})")

    instance_id = None
    ip = None
    region = None
    project = None
    zone = None
    compartment_id = None

    try:
        if cloud == 'aws':
            region = config['aws']['region']

            if DEBUG_MODE == True:
                log(f"AWS Region: {region}")
                log("Getting AWS key pair name...")

            key_name = run_cmd("aws ec2 describe-key-pairs --query 'KeyPairs[0].KeyName' --output text")

            if DEBUG_MODE == True:
                log(f"Key pair: {key_name}")

            sg_id = setup_aws_sg(region, config['common']['security_group_name'])
            instance_id, ip = launch_aws_instance(inst, config, region, key_name, sg_id)
        elif cloud == 'gcp':
            project = config['gcp']['project_id']
            if project == "AUTO_DETECT":
                project = get_gcp_project()
            zone = config['gcp']['zone']

            if DEBUG_MODE == True:
                log(f"GCP Project: {project}, Zone: {zone}")

            instance_id, ip = launch_gcp_instance(inst, config, project, zone)
        elif cloud == 'oci':
            compartment_id = config['oci']['compartment_id']
            region = config['oci']['region']

            if DEBUG_MODE == True:
                log(f"OCI Compartment: {compartment_id}, Region: {region}")

            instance_id, ip = launch_oci_instance(inst, config, compartment_id, region)
        else:
            msg = f"Unknown cloud provider: {cloud}"
            if DEBUG_MODE == True:
                log(msg, "ERROR")
            else:
                print(f"[Error] {msg}")
            return

        if not ip or ip == "None":
            msg = f"Failed to get IP for {name}"
            if DEBUG_MODE == True:
                log(msg, "ERROR")
            else:
                print(f"[Error] {msg}. Skipping benchmark.")
            return

        # Register instance for cleanup on signal/exception
        register_instance(cloud, instance_id, name, region=region, project=project, zone=zone, compartment_id=compartment_id)

        progress(instance_name, f"Instance launched (IP: {ip})")

        if DEBUG_MODE == True:
            log(f"Waiting 60s for SSH to become available...")
        elif DEBUG_MODE == False:
            print(f"IP: {ip}. Waiting 60s for SSH...")

        time.sleep(60)

        ssh_strict = config['common'].get('ssh_strict_host_key_checking', False)

        # Set hostname if specified in instance configuration
        if 'hostname' in inst and inst['hostname']:
            hostname = inst['hostname']

            # Validate hostname format (alphanumeric and hyphens only, no leading/trailing hyphens)
            import re
            if not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', hostname):
                msg = f"Invalid hostname format: {hostname}. Must contain only lowercase letters, numbers, and hyphens."
                if DEBUG_MODE == True:
                    log(msg, "ERROR")
                else:
                    print(f"[Error] {msg}")
            else:
                progress(instance_name, f"Setting hostname to: {hostname}")

                if DEBUG_MODE == True:
                    log(f"Setting hostname to: {hostname}")
                elif DEBUG_MODE == False:
                    print(f"  [Hostname] Setting to: {hostname}")

                ssh_connect_timeout = config['common'].get('ssh_timeout', 20)
                ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={'yes' if ssh_strict else 'no'} -o UserKnownHostsFile=/dev/null -o ConnectTimeout={ssh_connect_timeout} -o ServerAliveInterval=60 -o ServerAliveCountMax=10"
                ssh_user = config['common']['ssh_user']

                # Set hostname using hostnamectl and update /etc/hosts
                hostname_cmd = f"sudo hostnamectl set-hostname {hostname} && sudo sed -i '/127.0.1.1/d' /etc/hosts && echo '127.0.1.1 {hostname}' | sudo tee -a /etc/hosts > /dev/null"

                try:
                    result = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{hostname_cmd}'", capture=False, timeout=30)

                    if result is not None:
                        progress(instance_name, f"Hostname set successfully")

                        if DEBUG_MODE == True:
                            log(f"Hostname set to: {hostname}")
                        elif DEBUG_MODE == False:
                            print(f"  [Hostname] Successfully set to: {hostname}")
                    else:
                        msg = f"Failed to set hostname to: {hostname}"
                        if DEBUG_MODE == True:
                            log(msg, "WARN")
                        else:
                            print(f"  [Warning] {msg}")
                except Exception as e:
                    msg = f"Error setting hostname: {e}"
                    if DEBUG_MODE == True:
                        log(msg, "ERROR")
                    else:
                        print(f"  [Error] {msg}")

        # Run all commands sequentially
        commands_success = run_ssh_commands(ip, config, inst, key_path, ssh_strict, instance_name)
        if not commands_success:
            msg = f"Command execution failed for {name}"
            if DEBUG_MODE == True:
                log(msg, "ERROR")
            else:
                print(f"[Error] {msg}. Skipping result collection.")
            return

        # Collect results
        collect_results(ip, config, cloud, name, key_path, ssh_strict, instance_name)

        progress(instance_name, "Completed successfully")

        if DEBUG_MODE == True:
            log(f"Instance {name} completed successfully", "SUCCESS")

    except Exception as e:
        msg = f"{cloud} instance {name}: {e}"
        if DEBUG_MODE == True:
            log(msg, "ERROR")
            import traceback
            traceback.print_exc()
        else:
            print(f"[Error] {msg}")
    finally:
        if instance_id:
            # Unregister from active instances tracking
            unregister_instance(instance_id)

            progress(instance_name, "Terminating")

            if DEBUG_MODE == True:
                log(f"Terminating instance {name}...")
            elif DEBUG_MODE == False:
                print(f"Terminating {name}...")

            if cloud == 'aws' and region:
                run_cmd(f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}")
            elif cloud == 'gcp' and project and zone:
                run_cmd(f"gcloud compute instances delete {name} --project={project} --zone={zone} --quiet")
            elif cloud == 'oci' and compartment_id:
                # TODO: Implement OCI instance termination
                if DEBUG_MODE == True:
                    log("OCI instance termination not yet implemented", "WARN")
                else:
                    print(f"[Warning] OCI instance termination not yet implemented for {name}")
                # run_cmd(f"oci compute instance terminate --instance-id {instance_id} --force")

            if DEBUG_MODE == True:
                log(f"Instance {name} terminated")

def process_csp_instances(cloud, instances, config, key_path):
    """Process all instances for a single CSP sequentially."""
    csp_name = cloud.upper()
    if DEBUG_MODE == True:
        log(f"{csp_name} instances enabled")

    enabled_count = 0
    for inst in instances:
        if inst.get('enable'):
            process_instance(cloud, inst, config, key_path)
            enabled_count += 1

    if enabled_count == 0:
        msg = f"{csp_name} enabled but no instances are enabled"
        if DEBUG_MODE == True:
            log(msg, "WARN")
        else:
            print(f"[Info] {msg} in the configuration.")

    return enabled_count

def load_config(config_path='cloud_config.json'):
    """Load benchmark configuration from JSON file."""
    if not os.path.exists(config_path):
        print(f"[Error] Configuration file not found: {config_path}")
        print(f"[Info] Copy cloud_config.example.json to {config_path} and update settings.")
        return None

    with open(config_path, 'r') as f:
        config = json.load(f)

    # Set global debug mode
    global DEBUG_MODE
    DEBUG_MODE = config['common'].get('debug_stdout', False)

    if DEBUG_MODE == True:
        log("Configuration loaded successfully")

    return config


def validate_key_path(key_path_template):
    """Expand and validate SSH key path."""
    key_path = os.path.expanduser(
        key_path_template.replace('${HOME}', os.environ.get('HOME', '~'))
    )

    if DEBUG_MODE == True:
        log(f"Validating SSH key: {key_path}")

    if not os.path.exists(key_path):
        msg = f"SSH key not found: {key_path}"
        if DEBUG_MODE == True:
            log(msg, "ERROR")
        else:
            print(f"[Error] {msg}")
        return None

    if DEBUG_MODE == True:
        log("SSH key validated")

    return key_path


def main():
    """Main entry point for cloud benchmarking executor."""
    # Register signal handlers for cleanup
    signal.signal(signal.SIGINT, cleanup_active_instances)   # Ctrl+C
    signal.signal(signal.SIGTERM, cleanup_active_instances)  # kill command

    try:
        if DEBUG_MODE == True:
            log("=" * 60)
            log("CLOUD BENCHMARKING EXECUTOR")
            log("=" * 60)

        config = load_config()
        if not config:
            return

        # Create results directory
        host_rep_dir = config['common']['host_reports_dir']
        os.makedirs(host_rep_dir, exist_ok=True)

        if DEBUG_MODE == True:
            log(f"Results directory: {host_rep_dir}")

        # Validate SSH key
        key_path_template = config['common'].get(
            'ssh_key_path', '${HOME}/.ssh/cloud_onehour_project.pem'
        )
        key_path = validate_key_path(key_path_template)
        if not key_path:
            return

        # Prepare CSP tasks for parallel execution
        csp_tasks = []

        if config['aws']['enable']:
            if config['aws']['instances']:
                csp_tasks.append(('aws', config['aws']['instances']))
            else:
                msg = "AWS is enabled but has no instances configured"
                if DEBUG_MODE == True:
                    log(msg, "WARN")
                else:
                    print(f"[Info] {msg}")
        else:
            msg = "AWS is disabled"
            if DEBUG_MODE == True:
                log(msg, "INFO")
            else:
                print(f"[Info] {msg} in the configuration (aws.enable = false).")

        if config['gcp']['enable']:
            if config['gcp']['instances']:
                csp_tasks.append(('gcp', config['gcp']['instances']))
            else:
                msg = "GCP is enabled but has no instances configured"
                if DEBUG_MODE == True:
                    log(msg, "WARN")
                else:
                    print(f"[Info] {msg}")
        else:
            msg = "GCP is disabled"
            if DEBUG_MODE == True:
                log(msg, "INFO")
            else:
                print(f"[Info] {msg} in the configuration (gcp.enable = false).")

        if config.get('oci', {}).get('enable'):
            if config['oci']['instances']:
                csp_tasks.append(('oci', config['oci']['instances']))
            else:
                msg = "OCI is enabled but has no instances configured"
                if DEBUG_MODE == True:
                    log(msg, "WARN")
                else:
                    print(f"[Info] {msg}")
        else:
            msg = "OCI is disabled"
            if DEBUG_MODE == True:
                log(msg, "INFO")
            else:
                print(f"[Info] {msg} in the configuration (oci.enable = false).")

        # Execute CSPs in parallel (max 3 workers: AWS, GCP, and OCI)
        if csp_tasks:
            if DEBUG_MODE == True:
                log(f"Starting parallel execution of {len(csp_tasks)} CSP(s)")

            with ThreadPoolExecutor(max_workers=min(3, len(csp_tasks))) as executor:
                # Submit all CSP tasks
                future_to_csp = {
                    executor.submit(process_csp_instances, cloud, instances, config, key_path): cloud
                    for cloud, instances in csp_tasks
                }

                # Wait for all to complete
                for future in as_completed(future_to_csp):
                    cloud = future_to_csp[future]
                    try:
                        result = future.result()
                        if DEBUG_MODE == True:
                            log(f"{cloud.upper()} completed with {result} instance(s) processed")
                    except Exception as exc:
                        msg = f"{cloud.upper()} generated an exception: {exc}"
                        if DEBUG_MODE == True:
                            log(msg, "ERROR")
                        else:
                            print(f"[Error] {msg}")

        if DEBUG_MODE == True:
            log("=" * 60)
            log("ALL INSTANCES COMPLETED")
            log("=" * 60)

    except Exception as e:
        msg = f"Fatal error in main: {e}"
        if DEBUG_MODE == True:
            log(msg, "ERROR")
            import traceback
            traceback.print_exc()
        else:
            print(f"[Error] {msg}")
        # Cleanup any active instances before exiting
        cleanup_active_instances()
        raise


if __name__ == "__main__":
    main()
