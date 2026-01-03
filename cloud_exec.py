import json
import subprocess
import os
import time
import sys
import signal
import threading
import re
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

def build_storage_config(inst, cloud_type):
    """Build storage configuration arguments for different cloud providers.

    Args:
        inst: Instance configuration dict
        cloud_type: 'aws', 'gcp', or 'oci'

    Returns:
        str: Cloud-specific storage configuration arguments

    This function centralizes storage configuration logic to make it easier
    to maintain consistency across cloud providers and add new ones.
    """
    if not inst.get('extra_50g_storage', False):
        return ""

    if cloud_type == 'aws':
        # AWS: Use block-device-mappings with DeleteOnTermination=true
        config = (
            '--block-device-mappings '
            '\'[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":50,'
            '"VolumeType":"gp3","DeleteOnTermination":true}}]\' '
        )
        if DEBUG_MODE == True:
            log("Configuring 50GB root volume with auto-delete on termination")
        return config

    elif cloud_type == 'gcp':
        # GCP: Use boot-disk-size (auto-delete is YES by default)
        if DEBUG_MODE == True:
            log("Configuring 50GB boot disk (auto-delete enabled by default)")
        return "--boot-disk-size=50GB "

    elif cloud_type == 'oci':
        # OCI: Use boot-volume-size-in-gbs with auto-delete on termination
        # --is-preserve-boot-volume false ensures boot volume is deleted with instance
        if DEBUG_MODE == True:
            log("Configuring 50GB boot volume with auto-delete on termination")
        return "--boot-volume-size-in-gbs 50 --is-preserve-boot-volume false "

    else:
        if DEBUG_MODE == True:
            log(f"Unknown cloud type: {cloud_type}", "WARN")
        return ""


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

    # Build storage configuration using centralized helper
    storage_config = build_storage_config(inst, 'aws')

    instance_id = run_cmd(
        f"aws ec2 run-instances --region {region} --image-id {ami} "
        f"--instance-type {inst['type']} --key-name {key_name} "
        f"--security-group-ids {sg_id} {storage_config}"
        f"--query 'Instances[0].InstanceId' --output text"
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

    # Build storage configuration using centralized helper
    storage_config = build_storage_config(inst, 'gcp')

    ip = run_cmd(
        f"gcloud compute instances create {name} --project={project} "
        f"--zone={zone} --machine-type={inst['type']} "
        f"{storage_config}"
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

    Args:
        inst: Instance configuration dict
        config: Full configuration dict
        compartment_id: OCI compartment ID
        region: OCI region

    Returns:
        tuple: (instance_id, ip) or (None, None) if not implemented/failed

    TODO: Implement OCI instance launch using OCI CLI
    Reference commands:
        - Launch: oci compute instance launch --compartment-id <id> --shape <type> ...
        - Get IP: oci compute instance get --instance-id <id> --query 'data."primary-public-ip"'
        - Storage: --boot-volume-size-in-gbs 50 (for extra_50g_storage)
        - Delete: oci compute instance terminate --instance-id <id> --force
    """
    name = inst['name']

    if DEBUG_MODE == True:
        log(f"Launching OCI instance: {name} ({inst['type']})")
        log("OCI launch not yet implemented", "WARN")
    else:
        print(f"[Warning] OCI instance launch not yet implemented for {name}")

    # TODO: When implementing, follow this structure:
    #
    # 1. Find image OCID for Ubuntu
    # os_version = config['common']['os_version']
    # image_ocid = get_oci_ubuntu_image(region, os_version, inst['arch'])
    #
    # 2. Configure boot volume size using centralized helper
    # storage_config = build_storage_config(inst, 'oci')
    #
    # 3. Launch instance
    # instance_id = run_cmd(
    #     f"oci compute instance launch "
    #     f"--compartment-id {compartment_id} "
    #     f"--availability-domain <AD> "
    #     f"--shape {inst['type']} "
    #     f"--image-id {image_ocid} "
    #     f"{storage_config}"
    #     f"--display-name {name} "
    #     f"--query 'data.id' --raw-output"
    # )
    #
    # 4. Wait for instance to be running
    # run_cmd(f"oci compute instance action --instance-id {instance_id} --action start --wait-for-state RUNNING")
    #
    # 5. Get public IP
    # ip = run_cmd(
    #     f"oci compute instance get --instance-id {instance_id} "
    #     f"--query 'data.\"primary-public-ip\"' --raw-output"
    # )
    #
    # return instance_id, ip

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
    # Each workload timeout (backward compatible with command_timeout)
    workload_timeout = config['common'].get('workload_timeout', config['common'].get('command_timeout', 10800))

    # Support both new format (workloads array), old format (commands array), and legacy format
    workloads = []
    if 'workloads' in config['common']:
        # New format: array of workloads
        workloads = config['common']['workloads']
    elif 'commands' in config['common']:
        # Old format: array of commands (backward compatibility)
        workloads = config['common']['commands']
    else:
        # Legacy format: fallback for backward compatibility
        for i in range(1, 10):  # Support up to 9 setup commands
            cmd_key = f"setup_command{i}"
            if cmd_key in config['common']:
                cmd = config['common'][cmd_key]
                if cmd and cmd.strip():
                    workloads.append(cmd)
        for i in range(1, 10):  # Support up to 9 benchmark commands
            cmd_key = f"benchmark_command{i}"
            if cmd_key in config['common']:
                cmd = config['common'][cmd_key]
                if cmd and cmd.strip():
                    workloads.append(cmd)

    if not workloads:
        if DEBUG_MODE == True:
            log("No workloads to execute", "WARNING")
        return False

    total_workloads = len(workloads)
    progress(instance_name, f"Workload execution started ({total_workloads} workloads)")

    if DEBUG_MODE == True:
        log(f"Starting workload execution for {ip} ({total_workloads} workloads)")
    elif DEBUG_MODE == False:
        print(f"  [Workloads] Starting execution of {total_workloads} workloads...")

    for i, workload in enumerate(workloads, start=1):
        # Format workload with vcpu substitution
        cmd = workload.format(vcpus=inst['vcpus'])

        if not cmd or cmd.strip() == "":
            continue

        progress(instance_name, f"Workload {i}/{total_workloads}")

        if DEBUG_MODE == True:
            log(f"Workload {i}/{total_workloads}: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            log(f"Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")
        elif DEBUG_MODE == False:
            print(f"  [Workload {i}/{total_workloads}] Executing: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            print(f"  [Workload {i}/{total_workloads}] Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")

        # Detect long-running benchmark commands and run them via nohup
        long_running_indicators = ['pts_regression.py', 'benchmark', 'phoronix-test-suite']
        is_long_running = any(indicator in cmd for indicator in long_running_indicators)

        if is_long_running:
            # Run via nohup to survive SSH disconnections
            if DEBUG_MODE == True:
                log("Detected long-running command, using nohup for robustness")
            elif DEBUG_MODE == False:
                print(f"  [Workload {i}/{total_workloads}] Using nohup for long-running task")

            # Create unique marker file for this command
            marker_file = f"/tmp/cloud_exec_cmd_{i}_done.marker"
            log_file = f"/tmp/cloud_exec_cmd_{i}.log"

            # Wrap command with nohup and marker file creation
            # Use double quotes for outer command to avoid nested single quote issues
            # Escape double quotes and dollar signs in the command
            escaped_cmd = cmd.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$')
            wrapped_cmd = f'nohup sh -c "{escaped_cmd} && echo SUCCESS > {marker_file} || echo FAILED > {marker_file}" > {log_file} 2>&1 &'

            # Start the command in background
            run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{wrapped_cmd}'", capture=False, timeout=30)

            if DEBUG_MODE == True:
                log("Command started in background, waiting for completion...")
            elif DEBUG_MODE == False:
                print(f"  [Workload {i}/{total_workloads}] Started in background, monitoring...")

            # Poll for completion with timeout
            start_time = time.time()
            check_count = 0
            last_log_size = 0

            while time.time() - start_time < workload_timeout:
                # Exponential backoff: 30s -> 45s -> 67s -> 101s -> 151s -> 227s -> 300s (max 5 min)
                poll_interval = min(30 * (1.5 ** check_count), 300)
                time.sleep(poll_interval)
                check_count += 1

                # Check if marker file exists
                marker_check = run_cmd(
                    f"ssh {ssh_opt} {ssh_user}@{ip} 'cat {marker_file} 2>/dev/null || echo RUNNING'",
                    capture=True, timeout=10, ignore=True
                )

                # Show progress by checking log file size
                log_size_check = run_cmd(
                    f"ssh {ssh_opt} {ssh_user}@{ip} 'wc -c < {log_file} 2>/dev/null || echo 0'",
                    capture=True, timeout=10, ignore=True
                )

                try:
                    current_log_size = int(log_size_check or "0")
                    if current_log_size > last_log_size:
                        if DEBUG_MODE == True:
                            log(f"Progress: Log file size {current_log_size} bytes (+{current_log_size - last_log_size})")
                        last_log_size = current_log_size
                except:
                    pass

                if marker_check == "SUCCESS":
                    if DEBUG_MODE == True:
                        log(f"Workload {i}/{total_workloads} completed successfully")
                    else:
                        print(f"  [Workload {i}/{total_workloads}] ✓ Completed successfully (took {elapsed}s)")
                        if i < total_workloads:
                            print(f"  [Progress] {i}/{total_workloads} workloads completed, {total_workloads - i} remaining")
                    break
                elif marker_check == "FAILED":
                    if DEBUG_MODE == True:
                        log(f"Workload {i}/{total_workloads} failed", "ERROR")
                    else:
                        print(f"  [Error] Workload {i}/{total_workloads} failed")

                    # Fetch log file for debugging
                    print(f"  [Debug] Fetching error log from {log_file}...")
                    log_output = run_cmd(
                        f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -100 {log_file} 2>/dev/null || echo \"[Error] Could not read log file\"'",
                        capture=True, timeout=30, ignore=True
                    )
                    if log_output and log_output.strip():
                        print(f"  [Error Log] Last 100 lines from {log_file}:")
                        print("  " + "="*78)
                        for line in log_output.strip().split('\n'):
                            print(f"  {line}")
                        print("  " + "="*78)
                    else:
                        print(f"  [Warning] Could not fetch error log")

                    # Also check if there's any useful info in the workload output log
                    # Extract log file path from the workload command (e.g., "> /tmp/xxx.log")
                    workload_log_match = re.search(r'>\s*(/tmp/[^\s]+\.log)', cmd)
                    if workload_log_match:
                        workload_log_path = workload_log_match.group(1)
                        print(f"  [Debug] Checking workload output log: {workload_log_path}...")
                        workload_log = run_cmd(
                            f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -50 {workload_log_path} 2>/dev/null || echo \"No workload log found\"'",
                            capture=True, timeout=30, ignore=True
                        )
                        if workload_log and workload_log.strip() and "No workload log found" not in workload_log:
                            print(f"  [Workload Log] Last 50 lines from {workload_log_path}:")
                            print("  " + "="*78)
                            for line in workload_log.strip().split('\n'):
                                print(f"  {line}")
                            print("  " + "="*78)

                    return False
                elif marker_check and marker_check != "RUNNING":
                    if DEBUG_MODE == True:
                        log(f"Unexpected marker status: {marker_check}", "WARN")

                # Still running, continue polling
                elapsed = int(time.time() - start_time)

                # Get CPU usage from remote instance
                cpu_usage_cmd = (
                    f"ssh {ssh_opt} {ssh_user}@{ip} "
                    f"'mpstat -P ALL 1 1 | awk \"/^[0-9]/ {{if (\\$2 ~ /^[0-9]+$/) print \\$2,100-\\$NF}}\" | sort -n'"
                )
                cpu_usage_output = run_cmd(cpu_usage_cmd, capture=True, timeout=10, ignore=True)

                vcpu_ids = []
                vcpu_usage = []
                if cpu_usage_output:
                    for line in cpu_usage_output.strip().split('\n'):
                        parts = line.split()
                        if len(parts) == 2:
                            try:
                                vcpu_ids.append(int(parts[0]))
                                vcpu_usage.append(f"{float(parts[1]):.1f}%")
                            except:
                                pass

                if DEBUG_MODE == True:
                    log(f"Still running... ({elapsed}s / {workload_timeout}s)")
                    if vcpu_ids:
                        log(f"vCPU usage: {dict(zip(vcpu_ids, vcpu_usage))}")
                else:
                    # Show progress update every check (with exponential backoff timing)
                    progress_msg = f"  [Workload {i}/{total_workloads}] Still running... ({elapsed}s / {workload_timeout}s, log size: {current_log_size} bytes)"
                    if vcpu_ids:
                        progress_msg += f"\n    vCPU ID: {vcpu_ids}\n    vCPU usage: {vcpu_usage}"
                    print(progress_msg)

            else:
                # Timeout reached
                if DEBUG_MODE == True:
                    log(f"Workload {i}/{total_workloads} timed out after {workload_timeout}s", "ERROR")
                elif DEBUG_MODE == False:
                    print(f"  [Error] Workload {i}/{total_workloads} timed out")
                return False

        else:
            # Regular command execution (short-running)
            result = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{cmd}'", capture=False, timeout=workload_timeout)

            if result is None:
                if DEBUG_MODE == True:
                    log(f"Workload {i}/{total_workloads} failed or timed out", "ERROR")
                elif DEBUG_MODE == False:
                    print(f"  [Warning] Workload {i}/{total_workloads} failed or timed out")
                return False

            if DEBUG_MODE == True:
                log(f"Workload {i}/{total_workloads} completed")
            else:
                print(f"  [Workload {i}/{total_workloads}] ✓ Completed")
                if i < total_workloads:
                    print(f"  [Progress] {i}/{total_workloads} workloads completed, {total_workloads - i} remaining")

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

    progress(instance_name, "All workloads completed")

    if DEBUG_MODE == True:
        log("All workloads completed successfully")

    return True


def collect_results(ip, config, cloud, name, inst, key_path, ssh_strict_host_key_checking, instance_name):
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
    # Use hostname if specified, otherwise fallback to cloud_name format
    file_basename = inst.get('hostname', f"{cloud}_{name}")
    # Format OS version: "25.04" -> "ubuntu25_04"
    os_version = config['common']['os_version'].replace('.', '_')
    os_label = f"ubuntu{os_version}"
    # Add timestamp to filename (yymmdd_HHMMSS format)
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    local_f = f"{host_rep_dir}/{file_basename}_{os_label}_{timestamp}.tar.gz"

    if DEBUG_MODE == True:
        log(f"Downloading to {local_f} via SSH (avoiding SCP OpenSSL mismatch)...")

    # Use SSH with stdout redirection instead of SCP to avoid OpenSSL version mismatch
    # This transfers the file via SSH stdout which is more reliable across different OpenSSL versions
    run_cmd(
        f"ssh {ssh_opt} {ssh_user}@{ip} 'cat /tmp/reports.tar.gz' > {local_f}",
        capture=False,
    )

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
        collect_results(ip, config, cloud, name, inst, key_path, ssh_strict, instance_name)

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
