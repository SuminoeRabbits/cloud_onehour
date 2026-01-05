#!/usr/bin/env python3
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
from abc import ABC, abstractmethod

# =========================================================================================
# CLOUD EXECUTOR MODULES
# =========================================================================================
#
# This script is modularized into the following components to support parallel execution:
#
# 1. Dashboard Module (Dashboard class):
#    - Manages the real-time console display.
#    - Renders a table of all active instances, their status, current step, and progress.
#    - Handles thread-safe updates from parallel workers.
#
# 2. Logger Module (InstanceLogger class):
#    - Manages individual log files for each instance (logs/<run_id>/<csp>_<instance>.log).
#    - Redirects detailed command output to these files instead of the console.
#    - Sends high-level status updates to the Dashboard.
#
# 3. Cloud Executor Module (main logic):
#    - Orchestrates the parallel execution using ThreadPoolExecutor.
#    - Uses InstanceLogger to log operations and update the Dashboard.
#
# [Execution Model]
# - Parallel: AWS execution flow and GCP execution flow run simultaneously.
# - Sequential: Within an AWS (or GCP) thread, Instance A finishes before Instance B starts.
#   (並列: AWSの処理フロー と GCPの処理フロー は同時に走ります。)
#   (順次: AWSスレッドの中では、Instance A が終わってから Instance B が始まります。)
#
# =========================================================================================

class Dashboard:
    """Manages real-time console dashboard for parallel execution."""
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.instances = {}  # {instance_name: {'status': str, 'step': str, 'last_update': datetime}}
        self.start_time = datetime.now()
        self.log_dir = None
        self._running = False
        self._thread = None
        
        # ANSI colors
        self.HEADER = '\033[95m'
        self.BLUE = '\033[94m'
        self.CYAN = '\033[96m'
        self.GREEN = '\033[92m'
        self.WARNING = '\033[93m'
        self.FAIL = '\033[91m'
        self.ENDC = '\033[0m'
        self.BOLD = '\033[1m'

    def register(self, instance_name, cloud_type, machine_type, cpu_cost=0.0, storage_cost=0.0):
        with self.lock:
            self.instances[instance_name] = {
                'cloud': cloud_type,
                'type': machine_type,
                'status': 'PENDING',
                'step': 'Initializing...',
                'step_start': datetime.now(),
                'last_update': datetime.now(),
                'start_time': datetime.now(),
                'end_time': None,
                'cpu_cost': cpu_cost,
                'storage_cost': storage_cost,
                'history': [],
                'color': self.BLUE
            }

    def update(self, instance_name, status=None, step=None, color=None):
        if not self.enabled: return
        with self.lock:
            if instance_name in self.instances:
                data = self.instances[instance_name]
                if status: 
                    data['status'] = status
                    if status in ['COMPLETED', 'TERMINATED'] and data['end_time'] is None:
                        data['end_time'] = datetime.now()
                if step: 
                    if data['step'] != step:
                        data['step'] = step
                        data['step_start'] = datetime.now()
                if color: data['color'] = color
                data['last_update'] = datetime.now()

    def add_history(self, instance_name, step_name, duration_sec, status="OK"):
        """Record a completed step in history."""
        if not self.enabled: return
        with self.lock:
            if instance_name in self.instances:
                data = self.instances[instance_name]
                
                # Format duration
                if duration_sec < 60:
                    dur_str = f"{int(duration_sec)}s"
                elif duration_sec < 3600:
                    dur_str = f"{int(duration_sec//60)}m{int(duration_sec%60):02}s"
                else:
                    dur_str = f"{int(duration_sec//3600)}h{int((duration_sec%3600)//60):02}m"
                
                # Format status
                stat_str = "OK"
                if status == "TIMEOUT": stat_str = "TO"
                elif status == "ERROR": stat_str = "ERR"
                elif status == "SKIPPED": stat_str = "SKIP"
                
                # Extract simple step name (e.g., "Workload 1/5: build-llvm..." -> "W1: build-llvm")
                # Attempt to parse "Workload X/Y: name"
                simple_name = step_name
                if "Workload" in step_name:
                    try:
                        parts = step_name.split(':')
                        # "Workload 1/5" -> "W1"
                        w_part = parts[0].replace('Workload', 'W').split('/')[0].strip()
                        # " build-llvm..." -> "build-llvm"
                        cmd_part = parts[1].strip().split('...')[0].strip()
                        simple_name = f"{w_part}: {cmd_part}"
                    except:
                        pass
                
                data['history'].append({
                    'status': stat_str,
                    'name': simple_name,
                    'duration': dur_str
                })

    def set_log_dir(self, log_dir):
        self.log_dir = log_dir

    def start(self):
        if not self.enabled: return
        self._running = True
        self._thread = threading.Thread(target=self._render_loop)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._render_once() # Final render

    def _render_loop(self):
        # Initial render immediately
        self._render_once()
        while self._running:
            time.sleep(5)  # Refresh every 5 seconds to reduce flickering
            self._render_once()

    def _render_once(self):
        lines = []
        
        run_duration = datetime.now() - self.start_time
        run_str = str(run_duration).split('.')[0]
        lines.append(f"{self.BOLD}CLOUD BENCHMARKING EXECUTOR (Run: {run_str}){self.ENDC}")
        lines.append("=" * 100)
        # Header only for main row concepts
        lines.append(f"{'INSTANCE (TYPE)':<30} | {'STAT':<4} | {'TIME':<7} | {'COST':<7}")
        lines.append("-" * 100)

        with self.lock:
            # Sort by cloud provider for grouping
            sorted_insts = sorted(self.instances.items())
            
            for name, data in sorted_insts:
                # 1. Instance Row
                
                # Compact Status
                raw_stat = data['status']
                stat_map = {
                    'RUNNING': 'RUN ', 'COMPLETED': 'DONE', 'TERMINATED': 'TERM', 
                    'PENDING': 'WAIT', 'ERROR': 'ERR '
                }
                compact_stat = stat_map.get(raw_stat, raw_stat[:4])
                status_str = f"{data['color']}{compact_stat}{self.ENDC}"
                
                # Compact Name
                short_name = name
                for suffix in ['-amd64', '-arm64', '-vcpu-2', '-vcpu-4', '-vcpu-8', '-vcpu-16']:
                    short_name = short_name.replace(suffix, '')
                
                short_type = data['type'].replace('standard', 'std').replace('large', 'lg')
                display_name = f"{short_name} ({short_type})"
                if len(display_name) > 30:
                    display_name = display_name[:27] + "..."

                # Duration
                end = data['end_time'] if data['end_time'] else datetime.now()
                duration = end - data['start_time']
                duration_str = str(duration).split('.')[0]
                
                # Cost
                hours = duration.total_seconds() / 3600.0
                total_rate = data['cpu_cost'] + data['storage_cost']
                cost = hours * total_rate
                cost_str = f"${cost:.2f}"

                lines.append(f"{display_name:<30} | {status_str:<4} | {duration_str:<7} | {cost_str:<7}")

                # 2. History Rows (Indented)
                for item in data.get('history', []):
                     # Format: "  [OK] W1: name (10m30s)"
                     stat = item['status']
                     # Color code the history status?
                     color = self.GREEN if stat == "OK" else (self.FAIL if stat in ["ERR", "TO"] else self.BOLD)
                     item_str = f"  [{color}{stat}{self.ENDC}] {item['name']} ({item['duration']})"
                     lines.append(f"{item_str}")

                # 3. Current Step Row (Indented)
                if raw_stat not in ['COMPLETED', 'TERMINATED']:
                    step_elapsed = datetime.now() - data.get('step_start', datetime.now())
                    step_time_str = str(step_elapsed).split('.')[0]
                    # Format: "  [>>] W1: name... [05:22]"
                    
                    if step_elapsed.total_seconds() < 3600:
                        mm = int(step_elapsed.total_seconds() // 60)
                        ss = int(step_elapsed.total_seconds() % 60)
                        step_timer = f"[{mm:02}:{ss:02}]"
                    else:
                        step_timer = f"[{step_time_str}]"
                    
                    # Try to parse step name to Wx format to match history
                    step_name = data['step']
                    simple_step_name = step_name
                    if "Workload" in step_name:
                         try:
                            # "Workload X/Y: name" -> "WX: name"
                            parts = step_name.split(':')
                            w_part = parts[0].replace('Workload', 'W').split('/')[0].strip()
                            cmd_part = parts[1].strip()
                            simple_step_name = f"{w_part}: {cmd_part}"
                         except:
                            pass
                    
                    # Truncate if too long (arbitrary limit to avoid wrapping too much)
                    if len(simple_step_name) > 60:
                        simple_step_name = simple_step_name[:57] + "..."
                        
                    current_str = f"  [{self.CYAN}>>{self.ENDC}]   {simple_step_name} {step_timer}"
                    lines.append(f"{current_str}")
                    
                lines.append("-" * 100)
        
        lines.append("=" * 100)
        
        full_output = "\n".join(lines)
        
        # 1. Print to console with clear screen
        print(f"\033[2J\033[H{full_output}")
        sys.stdout.flush()
        
        # 2. Write to dashboard.log (strip ANSI)
        if self.log_dir:
            try:
                # Strip ANSI codes using regex
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                clean_output = ansi_escape.sub('', full_output)
                
                dashboard_file = self.log_dir / "dashboard.log"
                with open(dashboard_file, 'w') as f:
                    f.write(f"Last Update: {datetime.now()}\n")
                    f.write(clean_output + "\n")
            except Exception:
                pass  # Ignore file write errors (don't crash dashboard)

class InstanceLogger:
    """Handles logging to file and updating dashboard for a specific instance."""
    def __init__(self, instance_name, global_dashboard, log_dir):
        self.name = instance_name
        self.dashboard = global_dashboard
        self.log_file = log_dir / f"{instance_name.replace(':', '_')}.log"
        
        # Initialize log file
        with open(self.log_file, 'w') as f:
            f.write(f"=== Log started for {instance_name} ===\n")
            f.write(f"Timestamp: {datetime.now()}\n\n")

    def info(self, message):
        """Log info message to file."""
        self._write(f"[INFO] {message}")

    def error(self, message):
        """Log error message to file and update dashboard."""
        self._write(f"[ERROR] {message}")
        self.dashboard.update(self.name, status="ERROR", color=self.dashboard.FAIL)

    def warn(self, message):
        """Log warning message to file."""
        self._write(f"[WARN] {message}")

    def cmd(self, command):
        """Log command execution."""
        self._write(f"[CMD] {command}")

    def progress(self, step, status="RUNNING"):
        """Update dashboard and log progress."""
        self._write(f"[PROGRESS] {step}")
        color = self.dashboard.GREEN if status == "COMPLETED" else self.dashboard.BLUE
        if status == "ERROR": color = self.dashboard.FAIL
        self.dashboard.update(self.name, status=status, step=step, color=color)

    def _write(self, line):
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            with open(self.log_file, 'a') as f:
                f.write(f"[{timestamp}] {line}\n")
        except Exception:
            pass # Don't crash on logging fail

# Global Dashboard Instance
DASHBOARD = Dashboard(enabled=True)

# Global debug mode - set by load_config
DEBUG_MODE = False  # NO LONGER USED in new Dashboard mode, kept for compatibility

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
                        f"gcloud compute instances delete {instance_id} --project={project} --zone={zone} --quiet",
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
    """Deprecated: Log timestamped message if debug mode is enabled."""
    if DEBUG_MODE == True:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {msg}", flush=True)

def progress(instance_name, step, logger=None):
    """Update progress on dashboard and log to file if logger is available."""
    if logger:
        logger.progress(step)
    else:
        # Fallback for non-threaded execution
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{instance_name}] {step}", flush=True)

def run_cmd(cmd, capture=True, ignore=False, timeout=None, logger=None):
    """Execute shell command and return output or status. Supports logger redirection."""
    try:
        if logger:
            logger.cmd(f"Executing: {cmd[:150]}{'...' if len(cmd) > 150 else ''}")
        elif DEBUG_MODE == True:
            log(f"Executing: {cmd[:100]}{'...' if len(cmd) > 100 else ''}", "CMD")

        start_time = time.time()
        res = subprocess.run(
            cmd, shell=True, capture_output=capture, text=True, check=not ignore, timeout=timeout
        )
        elapsed = time.time() - start_time

        if logger:
            logger.info(f"Command completed in {elapsed:.2f}s")
        elif DEBUG_MODE == True:
            log(f"Command completed in {elapsed:.2f}s", "CMD")

        return res.stdout.strip() if capture else True
    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout} seconds"
        if logger:
            if ignore:
                logger.warn(msg)
            else:
                logger.error(msg)
        elif DEBUG_MODE == True:
            log(msg, "WARN" if ignore else "ERROR")
        elif DEBUG_MODE == False and not logger:
            print(f"[{'Warn' if ignore else 'Error'}] {msg}")
            
        if not ignore:
            raise
        return None
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr if e.stderr else 'No error message'
        msg = f"Command failed: {err_msg}"
        if logger:
            if ignore:
                logger.warn(msg)
            else:
                logger.error(msg)
        elif DEBUG_MODE == True:
            log(msg, "WARN" if ignore else "ERROR")
        elif DEBUG_MODE == False and not logger:
            print(f"[{'Warn' if ignore else 'Error'}] {err_msg}")
            
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
    if not inst.get('extra_150g_storage', False):
        return ""

    if cloud_type == 'aws':
        # AWS: Use block-device-mappings with DeleteOnTermination=true
        config = (
            '--block-device-mappings '
            '\'[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,'
            '"VolumeType":"gp3","DeleteOnTermination":true}}]\' '
        )
        if DEBUG_MODE == True:
            log("Configuring 150GB root volume with auto-delete on termination")
        return config

    elif cloud_type == 'gcp':
        # GCP: Use boot-disk-size (auto-delete is YES by default)
        if DEBUG_MODE == True:
            log("Configuring 150GB boot disk (auto-delete enabled by default)")
        return "--boot-disk-size=150GB "

    elif cloud_type == 'oci':
        # OCI: Use boot-volume-size-in-gbs with auto-delete on termination
        # --is-preserve-boot-volume false ensures boot volume is deleted with instance
        if DEBUG_MODE == True:
            log("Configuring 150GB boot volume with auto-delete on termination")
        return "--boot-volume-size-in-gbs 150 --is-preserve-boot-volume false "

    else:
        if DEBUG_MODE == True:
            log(f"Unknown cloud type: {cloud_type}", "WARN")
        return ""


def get_gcp_project(logger=None):
    """Detect GCP project ID from gcloud config."""
    if logger:
        logger.info("Detecting GCP project ID...")
    elif DEBUG_MODE == True:
        log("Detecting GCP project ID...")

    project = run_cmd("gcloud config get-value project", logger=logger)
    if project and "(unset)" not in project:
        if logger:
            logger.info(f"GCP project: {project}")
        elif DEBUG_MODE == True:
            log(f"GCP project: {project}")
        return project
        
    if logger:
        logger.warn("GCP project not configured")
    elif DEBUG_MODE == True:
        log("GCP project not configured", "WARN")
    return None

def setup_aws_sg(region, sg_name, logger=None):
    """Create/retrieve AWS security group and authorize SSH access from current IP."""
    if logger:
        logger.info(f"Setting up AWS security group: {sg_name} in {region}")
    elif DEBUG_MODE == True:
        log(f"Setting up AWS security group: {sg_name} in {region}")
        log("Checking for existing security group...")

    sg_id = run_cmd(
        f"aws ec2 describe-security-groups --region {region} --group-names {sg_name} "
        f"--query 'SecurityGroups[0].GroupId' --output text",
        ignore=True,
        logger=logger
    )

    if not sg_id or sg_id == "None":
        if logger:
            logger.info("Security group not found, creating new one...")
        elif DEBUG_MODE == True:
            log("Security group not found, creating new one...")
            
        vpc_id = run_cmd(
            f"aws ec2 describe-vpcs --region {region} --query 'Vpcs[0].VpcId' --output text",
            logger=logger
        )
        if logger:
            logger.info(f"Using VPC: {vpc_id}")
        elif DEBUG_MODE == True:
            log(f"Using VPC: {vpc_id}")

        sg_id = run_cmd(
            f"aws ec2 create-security-group --group-name {sg_name} "
            f"--description 'SG for benchmarking' --vpc-id {vpc_id} --region {region} "
            f"--query 'GroupId' --output text",
            logger=logger
        )
        if logger:
            logger.info(f"Created security group: {sg_id}")
        elif DEBUG_MODE == True:
            log(f"Created security group: {sg_id}")
    else:
        if logger:
            logger.info(f"Using existing security group: {sg_id}")
        elif DEBUG_MODE == True:
            log(f"Using existing security group: {sg_id}")

    if logger:
        logger.info("Getting current public IP...")
    elif DEBUG_MODE == True:
        log("Getting current public IP...")
        
    my_ip = run_cmd("curl -s https://checkip.amazonaws.com", logger=logger)
    
    if logger:
        logger.info(f"Current IP: {my_ip}")
        logger.info(f"Authorizing SSH access from {my_ip}/32...")
    elif DEBUG_MODE == True:
        log(f"Current IP: {my_ip}")
        log(f"Authorizing SSH access from {my_ip}/32...")

    run_cmd(
        f"aws ec2 authorize-security-group-ingress --group-id {sg_id} "
        f"--protocol tcp --port 22 --cidr {my_ip}/32 --region {region}",
        ignore=True,
        logger=logger
    )
    if logger:
        logger.info("Security group configured")
    elif DEBUG_MODE == True:
        log("Security group configured")

    return sg_id

def launch_aws_instance(inst, config, region, key_name, sg_id, logger=None):
    """Launch AWS instance and return (instance_id, ip)."""
    if logger:
        logger.info(f"Launching AWS instance: {inst['name']} ({inst['type']})")
    elif DEBUG_MODE == True:
        log(f"Launching AWS instance: {inst['name']} ({inst['type']})")

    os_version = config['common']['os_version']
    version_to_codename = {
        '20.04': 'focal',
        '22.04': 'jammy',
        '24.04': 'noble',
        '25.04': 'plucky'
    }
    codename = version_to_codename.get(os_version, 'jammy')

    if logger:
        logger.info(f"Finding AMI for Ubuntu {os_version} ({codename}) {inst['arch']}...")
    elif DEBUG_MODE == True:
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
        if logger:
            logger.info(f"Trying AMI pattern: {pattern}")
        elif DEBUG_MODE == True:
            log(f"Trying AMI pattern: {pattern}")

        ami = run_cmd(
            f"aws ec2 describe-images --region {region} --owners 099720109477 "
            f"--filters 'Name=name,Values={pattern}' "
            f"--query 'reverse(sort_by(Images, &CreationDate))[:1] | [0].ImageId' --output text",
            logger=logger
        )

        if ami and ami != "None" and ami.strip():
            if logger:
                logger.info(f"Found AMI with pattern '{pattern}': {ami}")
            elif DEBUG_MODE == True:
                log(f"Found AMI with pattern '{pattern}': {ami}")
            break


    if not ami or ami == "None":
        msg = f"No AMI found for Ubuntu {os_version} ({codename}) {inst['arch']} in {region}"
        if logger:
            logger.error(msg)
        elif DEBUG_MODE == True:
            log(msg, "ERROR")
        else:
            print(f"[Error] {msg}")
        return None, None

    if logger:
        logger.info(f"Using AMI: {ami}")
        logger.info("Starting instance...")
    elif DEBUG_MODE == True:
        log(f"Using AMI: {ami}")
        log("Starting instance...")

    # Build storage configuration using centralized helper
    storage_config = build_storage_config(inst, 'aws')

    instance_id = run_cmd(
        f"aws ec2 run-instances --region {region} --image-id {ami} "
        f"--instance-type {inst['type']} --key-name {key_name} "
        f"--security-group-ids {sg_id} {storage_config}"
        f"--query 'Instances[0].InstanceId' --output text",
        logger=logger
    )

    if logger:
        logger.info(f"Instance ID: {instance_id}")
        logger.info("Waiting for instance to be running...")
    elif DEBUG_MODE == True:
        log(f"Instance ID: {instance_id}")
        log("Waiting for instance to be running...")

    run_cmd(f"aws ec2 wait instance-running --region {region} --instance-ids {instance_id}", logger=logger)

    ip = run_cmd(
        f"aws ec2 describe-instances --region {region} --instance-ids {instance_id} "
        f"--query 'Reservations[0].Instances[0].PublicIpAddress' --output text",
        logger=logger
    )

    if logger:
        logger.info(f"Instance running with IP: {ip}")
    elif DEBUG_MODE == True:
        log(f"Instance running with IP: {ip}")

    return instance_id, ip


def launch_gcp_instance(inst, config, project, zone, logger=None):
    """Launch GCP instance and return (instance_id, ip)."""
    name = inst['name']

    if logger:
        logger.info(f"Launching GCP instance: {name} ({inst['type']})")
    elif DEBUG_MODE == True:
        log(f"Launching GCP instance: {name} ({inst['type']})")

    os_version = config['common']['os_version']
    img_arch = "arm64" if inst['arch'] == "arm64" else "amd64"

    version_number = os_version.replace('.', '')
    is_lts = os_version.endswith('.04') and int(os_version.split('.')[0]) % 2 == 0
    lts_suffix = "-lts" if is_lts else ""
    image_family = f"ubuntu-{version_number}{lts_suffix}-{img_arch}"

    if logger:
        logger.info(f"Using image family: {image_family}")
        logger.info("Creating instance...")
    elif DEBUG_MODE == True:
        log(f"Using image family: {image_family}")
        log("Creating instance...")

    # Build storage configuration using centralized helper
    storage_config = build_storage_config(inst, 'gcp')

    ip = run_cmd(
        f"gcloud compute instances create {name} --project={project} "
        f"--zone={zone} --machine-type={inst['type']} "
        f"{storage_config}"
        f"--image-family={image_family} --image-project=ubuntu-os-cloud "
        f"--format='get(networkInterfaces[0].accessConfigs[0].natIP)'",
        logger=logger
    )

    if ip:
        if logger:
            logger.info(f"Instance created with IP: {ip}")
        elif DEBUG_MODE == True:
            log(f"Instance created with IP: {ip}")
    else:
        if logger:
            logger.error("Failed to create instance")
        elif DEBUG_MODE == True:
            log("Failed to create instance", "ERROR")
        else:
            print("[Error] Failed to create GCP instance")

    return name if ip else None, ip


def launch_oci_instance(inst, config, compartment_id, region, logger=None):
    """Launch OCI instance and return (instance_id, ip).
    
    Args:
        inst: Instance configuration dict
        config: Full configuration dict
        compartment_id: OCI compartment ID
        region: OCI region
        logger: InstanceLogger object

    Returns:
        tuple: (instance_id, ip) or (None, None) if not implemented/failed
    """
    name = inst['name']

    if logger:
        logger.info(f"Launching OCI instance: {name} ({inst['type']})")
        logger.warn("OCI launch not yet implemented")
    elif DEBUG_MODE == True:
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


def get_instance_status(cloud, instance_id, region=None, project=None, zone=None, logger=None):
    """Check if instance is running, terminated, or missing."""
    try:
        if cloud == 'aws':
            status = run_cmd(
                f"aws ec2 describe-instances --region {region} --instance-ids {instance_id} "
                f"--query 'Reservations[0].Instances[0].State.Name' --output text",
                capture=True, ignore=True, logger=logger
            )
            return status.strip() if status else "unknown"

        elif cloud == 'gcp':
            # GCP status: PROVISIONING, STAGING, RUNNING, STOPPING, SUSPENDING, SUSPENDED, REPAIRING, and TERMINATED.
            # If instance is deleted, this command usually fails.
            status = run_cmd(
                f"gcloud compute instances describe {instance_id} --project={project} --zone={zone} "
                f"--format='get(status)'",
                capture=True, ignore=True, logger=logger
            )
            return status.strip() if status else "TERMINATED" # Assume terminated if verify fails

        elif cloud == 'oci':
            return "unknown" 

    except Exception:
        return "unknown"
    
    return "unknown"


def verify_ssh_build(ip, ssh_opt, ssh_user, instance_name, auto_rollback=True, logger=None):
    """
    Verify SSH build status after build_openssh.sh execution.

    Args:
        ip: Remote instance IP address
        ssh_opt: SSH options string
        ssh_user: SSH username
        instance_name: Instance name for logging
        auto_rollback: If True, automatically rollback on verification failure
        logger: InstanceLogger object

    Returns:
        bool: True if build succeeded, False otherwise
    """
    try:
        if logger:
            logger.info("Verifying SSH build status...")
        elif DEBUG_MODE == True:
            log("Verifying SSH build status...")

        # Wait for delayed SSH restart to complete
        if logger:
            logger.info("Waiting 10 seconds for SSH service restart...")
        elif DEBUG_MODE == True:
            log("Waiting 10 seconds for SSH service restart...")
        time.sleep(10)

        # Check build status file
        status_cmd = f"ssh {ssh_opt} {ssh_user}@{ip} 'cat /tmp/ssh_build_status.txt 2>/dev/null || echo UNKNOWN'"
        status = run_cmd(status_cmd, capture=True, timeout=10, logger=logger)

        if status == "SUCCESS":
            if logger:
                logger.info("SSH build verified successfully")
            elif DEBUG_MODE == True:
                log("SSH build verified successfully", "INFO")
            elif DEBUG_MODE == False:
                print("  [SSH Build] ✓ Verification successful")
            progress(instance_name, "SSH build verified", logger)
            return True
        else:
            if logger:
                logger.error(f"SSH build verification failed: {status}")
            elif DEBUG_MODE == True:
                log(f"SSH build verification failed: {status}", "ERROR")
            elif DEBUG_MODE == False:
                print(f"  [SSH Build] ✗ Verification failed: {status}")
            progress(instance_name, f"SSH build failed: {status}", logger)

            # Attempt automatic rollback
            if auto_rollback and status == "FAILED":
                if logger:
                    logger.info("Attempting automatic rollback to previous SSH version...")
                elif DEBUG_MODE == True:
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

                rollback_result = run_cmd(rollback_cmd, capture=True, timeout=30, ignore=True, logger=logger)

                if rollback_result == "ROLLBACK_SUCCESS":
                    if logger:
                        logger.info("Rollback successful, SSH restored to previous version")
                    elif DEBUG_MODE == True:
                        log("Rollback successful, SSH restored to previous version", "INFO")
                    elif DEBUG_MODE == False:
                        print("  [SSH Build] ✓ Rollback successful")
                else:
                    if logger:
                        logger.error(f"Rollback failed: {rollback_result}")
                    elif DEBUG_MODE == True:
                        log(f"Rollback failed: {rollback_result}", "ERROR")
                    elif DEBUG_MODE == False:
                        print(f"  [SSH Build] ✗ Rollback failed: {rollback_result}")

            return False

    except Exception as e:
        if logger:
            logger.error(f"SSH build verification error: {e}")
        elif DEBUG_MODE == True:
            log(f"SSH build verification error: {e}", "ERROR")
        elif DEBUG_MODE == False:
            print(f"  [SSH Build] ✗ Verification error: {e}")
        return False


def run_ssh_commands(ip, config, inst, key_path, ssh_strict_host_key_checking, instance_name, logger=None):
    """Execute all commands via SSH sequentially with output displayed."""
    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_connect_timeout = config['common'].get('ssh_timeout', 20)
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk} -o UserKnownHostsFile=/dev/null -o ConnectTimeout={ssh_connect_timeout} -o ServerAliveInterval=300 -o ServerAliveCountMax=3"
    ssh_user = config['common']['ssh_user']
    # Each workload timeout (backward compatible with command_timeout)
    workload_timeout = config['common'].get('workload_timeout', config['common'].get('command_timeout', 10800))

    # -----------------------------------------------------------
    # Determine Command List (Testloads vs Workloads)
    # -----------------------------------------------------------
    # If 'testloads' is True for this instance, run ONLY testloads commands.
    # Otherwise, run the standard workloads.
    workloads = []
    
    if inst and inst.get('testloads', False):
        if logger:
            logger.info(f"Testloads ENABLED for {instance_name}. Running ONLY testloads.")
        workloads = config['common'].get('testloads', [])
    else:
        # Standard workloads execution
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
        if logger:
            logger.warn("No workloads to execute")
        elif DEBUG_MODE == True:
            log("No workloads to execute", "WARNING")
        return False

    total_workloads = len(workloads)
    progress(instance_name, f"Workload execution started ({total_workloads} workloads)", logger)

    if logger:
        logger.info(f"Starting workload execution for {ip} ({total_workloads} workloads)")
    elif DEBUG_MODE == True:
        log(f"Starting workload execution for {ip} ({total_workloads} workloads)")
    elif DEBUG_MODE == False:
        print(f"  [Workloads] Starting execution of {total_workloads} workloads...")

    for i, workload in enumerate(workloads, start=1):
        workload_start = time.time()
        # Format workload with vcpu substitution
        cmd = workload.format(vcpus=inst['vcpus'])

        if not cmd or cmd.strip() == "":
            continue

        progress(instance_name, f"Workload {i}/{total_workloads}", logger)

        if logger:
            logger.info(f"Workload {i}/{total_workloads}: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            logger.info(f"Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")
        elif DEBUG_MODE == True:
            log(f"Workload {i}/{total_workloads}: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            log(f"Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")
        elif DEBUG_MODE == False:
            print(f"  [Workload {i}/{total_workloads}] Executing: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            print(f"  [Workload {i}/{total_workloads}] Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")

        # Detect long-running benchmark commands and run them via nohup
        long_running_indicators = config['common'].get('long_running_indicators', ['pts_regression.py', 'benchmark', 'phoronix-test-suite', 'pts_runner'])
        is_long_running = any(indicator in cmd for indicator in long_running_indicators)

        if is_long_running:
            # Run via nohup to survive SSH disconnections
            if logger:
                logger.info("Detected long-running command, using nohup for robustness")
            elif DEBUG_MODE == True:
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
            # Start the command in background
            try:
                run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{wrapped_cmd}'", capture=False, timeout=60, logger=logger)
            except subprocess.TimeoutExpired:
                # If starting the background command times out, it might have actually started but SSH didn't return.
                # We will proceed to polling to verify.
                if logger:
                    logger.warn("Timeout while starting background command, proceeding to verification...")
                elif DEBUG_MODE:
                    log("Timeout while starting background command, proceeding to verification...", "WARN")

            if logger:
                logger.info("Command started in background, waiting for completion...")
            elif DEBUG_MODE == True:
                log("Command started in background, waiting for completion...")
            elif DEBUG_MODE == False:
                print(f"  [Workload {i}/{total_workloads}] Started in background, monitoring...")

            # Poll for completion with timeout
            start_time = time.time()
            check_count = 0
            last_log_size = 0
            ssh_fail_count = 0

            while time.time() - start_time < workload_timeout:
                # Exponential backoff: 30s -> 45s -> 67s -> 101s -> 151s -> 227s -> 300s (max 5 min)
                poll_interval = min(30 * (1.5 ** check_count), 300)
                time.sleep(poll_interval)
                check_count += 1

                # Check if marker file exists
                marker_check = run_cmd(
                    f"ssh {ssh_opt} {ssh_user}@{ip} 'cat {marker_file} 2>/dev/null || echo RUNNING'",
                    capture=True, timeout=10, ignore=True, logger=logger
                )

                if marker_check:
                    ssh_fail_count = 0
                else:
                    ssh_fail_count += 1
                    if ssh_fail_count >= 3:
                        # consecutive SSH failures, check cloud status
                        if logger:
                            logger.warn(f"SSH failed {ssh_fail_count} times, checking instance status...")
                        elif DEBUG_MODE == True:
                            log(f"SSH failed {ssh_fail_count} times, checking instance status...", "WARN")

                        status = get_instance_status(
                            cloud=inst.get('cloud'),
                            instance_id=inst.get('instance_id'),
                            region=inst.get('region'),
                            project=inst.get('project'),
                            zone=inst.get('zone'),
                            logger=logger
                        )
                        
                        if status in ['terminated', 'TERMINATED', 'stopped', 'STOPPING', 'shutting-down']:
                            msg = f"Instance {instance_name} terminated externally (Status: {status})"
                            if logger: 
                                logger.error(msg)
                            elif DEBUG_MODE == True:
                                log(msg, "ERROR")
                            
                            DASHBOARD.update(instance_name, status='TERMINATED')
                            duration = time.time() - workload_start
                            DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "EXT_TERM")
                            return False
                        else:
                            if logger:
                                logger.warn(f"Instance status is {status}, continuing SSH retries...")
                            elif DEBUG_MODE == True:
                                log(f"Instance status is {status}, continuing SSH retries...", "WARN")


                # Show progress by checking log file size
                log_size_check = run_cmd(
                    f"ssh {ssh_opt} {ssh_user}@{ip} 'wc -c < {log_file} 2>/dev/null || echo 0'",
                    capture=True, timeout=10, ignore=True, logger=logger
                )

                try:
                    current_log_size = int(log_size_check or "0")
                    if current_log_size > last_log_size:
                        if logger:
                            logger.info(f"Progress: Log file size {current_log_size} bytes (+{current_log_size - last_log_size})")
                        elif DEBUG_MODE == True:
                            log(f"Progress: Log file size {current_log_size} bytes (+{current_log_size - last_log_size})")
                        last_log_size = current_log_size
                except:
                    pass

                if marker_check == "SUCCESS":
                    if logger:
                        logger.info(f"Workload {i}/{total_workloads} completed successfully")
                    elif DEBUG_MODE == True:
                        log(f"Workload {i}/{total_workloads} completed successfully")
                    else:
                        print(f"  [Workload {i}/{total_workloads}] ✓ Completed successfully")
                        if i < total_workloads:
                            print(f"  [Progress] {i}/{total_workloads} workloads completed, {total_workloads - i} remaining")
                    
                    duration = time.time() - workload_start
                    DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "OK")
                    break
                elif marker_check == "FAILED":
                    if logger:
                        logger.error(f"Workload {i}/{total_workloads} failed")
                    elif DEBUG_MODE == True:
                        log(f"Workload {i}/{total_workloads} failed", "ERROR")
                    else:
                        print(f"  [Error] Workload {i}/{total_workloads} failed")

                    # Fetch log file for debugging
                    if logger:
                        logger.info(f"Fetching error log from {log_file}...")
                    else:
                        print(f"  [Debug] Fetching error log from {log_file}...")
                    
                    log_output = run_cmd(
                        f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -100 {log_file} 2>/dev/null || echo \"[Error] Could not read log file\"'",
                        capture=True, timeout=30, ignore=True, logger=logger
                    )
                    
                    if logger:
                        if log_output and log_output.strip():
                            logger.info(f"Last 100 lines from {log_file}:")
                            for line in log_output.strip().split('\n'):
                                logger.info(f"  {line}")
                        else:
                            logger.warn("Could not fetch error log")
                    else:
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
                        if logger:
                            logger.info(f"Checking workload output log: {workload_log_path}...")
                        else:
                            print(f"  [Debug] Checking workload output log: {workload_log_path}...")
                            
                        workload_log = run_cmd(
                            f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -50 {workload_log_path} 2>/dev/null || echo \"No workload log found\"'",
                            capture=True, timeout=30, ignore=True, logger=logger
                        )
                        
                        if logger:
                            if workload_log and workload_log.strip() and "No workload log found" not in workload_log:
                                logger.info(f"Last 50 lines from {workload_log_path}:")
                                for line in workload_log.strip().split('\n'):
                                    logger.info(f"  {line}")
                        else:
                            if workload_log and workload_log.strip() and "No workload log found" not in workload_log:
                                print(f"  [Workload Log] Last 50 lines from {workload_log_path}:")
                                print("  " + "="*78)
                                for line in workload_log.strip().split('\n'):
                                    print(f"  {line}")
                                print("  " + "="*78)

                    duration = time.time() - workload_start
                    DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "ERROR")
                    return False
                elif marker_check and marker_check != "RUNNING":
                    if logger:
                        logger.warn(f"Unexpected marker status: {marker_check}")
                    elif DEBUG_MODE == True:
                        log(f"Unexpected marker status: {marker_check}", "WARN")

                # Still running, continue polling
                elapsed = int(time.time() - start_time)

                # Get CPU usage from remote instance
                cpu_usage_cmd = (
                    f"ssh {ssh_opt} {ssh_user}@{ip} "
                    f"'mpstat -P ALL 1 1 | awk \"/^[0-9]/ {{if (\\$2 ~ /^[0-9]+$/) print \\$2,100-\\$NF}}\" | sort -n'"
                )
                cpu_usage_output = run_cmd(cpu_usage_cmd, capture=True, timeout=10, ignore=True, logger=logger)

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

                if logger:
                    logger.info(f"Still running... ({elapsed}s / {workload_timeout}s)")
                    if vcpu_ids:
                        logger.info(f"vCPU usage: {dict(zip(vcpu_ids, vcpu_usage))}")
                elif DEBUG_MODE == True:
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
                if logger:
                    logger.error(f"Workload {i}/{total_workloads} timed out after {workload_timeout}s")
                elif DEBUG_MODE == True:
                    log(f"Workload {i}/{total_workloads} timed out after {workload_timeout}s", "ERROR")
                elif DEBUG_MODE == False:
                    print(f"  [Error] Workload {i}/{total_workloads} timed out")
                
                duration = time.time() - workload_start
                DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "TIMEOUT")
                continue # Proceed to next workload on timeout

        else:
            # Regular command execution (short-running)
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{cmd}'", capture=False, ignore=False, timeout=workload_timeout, logger=logger)
                    
                    # Success
                    if logger:
                        logger.info(f"Workload {i}/{total_workloads} completed")
                    elif DEBUG_MODE == True:
                        log(f"Workload {i}/{total_workloads} completed")
                    else:
                        print(f"  [Workload {i}/{total_workloads}] ✓ Completed")
                        if i < total_workloads:
                            print(f"  [Progress] {i}/{total_workloads} workloads completed, {total_workloads - i} remaining")

                    duration = time.time() - workload_start
                    DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "OK")
                    break # Break retry loop on success

                except subprocess.TimeoutExpired:
                    if attempt < max_retries - 1:
                        msg = f"Workload {i}/{total_workloads} timed out (Attempt {attempt+1}/{max_retries}), retrying..."
                        if logger: logger.warn(msg)
                        else: print(f"  [Warn] {msg}")
                        time.sleep(10)
                        continue

                    msg = f"Workload {i}/{total_workloads} timed out after {workload_timeout}s"
                    if logger: logger.error(msg)
                    elif DEBUG_MODE: log(msg, "ERROR")
                    else: print(f"  [Error] {msg}")

                    duration = time.time() - workload_start
                    DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "TIMEOUT")
                    # No abort on timeout? The original code continued.
                    continue 

                except subprocess.CalledProcessError as e:
                    # Check return code
                    if attempt < max_retries - 1:
                        # Retry on SSH errors (255) or generally any error for setup commands
                        msg = f"Workload {i}/{total_workloads} failed with {e.returncode} (Attempt {attempt+1}/{max_retries}), retrying..."
                        if logger: logger.warn(msg)
                        else: print(f"  [Warn] {msg}")
                        time.sleep(10)
                        continue

                    msg = f"Workload {i}/{total_workloads} failed: {e}"
                    if logger: logger.error(msg)
                    elif DEBUG_MODE: log(msg, "ERROR")
                    else: print(f"  [Error] {msg}")

                    duration = time.time() - workload_start
                    DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "ERROR")
                    return False

        # Special handling: Verify SSH build after SSH-related scripts execution
        # Detects both direct execution and execution via wrapper scripts
        ssh_build_indicators = ['build_openssh.sh', 'prepare_tools.sh']
        if any(indicator in cmd for indicator in ssh_build_indicators):
            # Check if SSH build actually occurred by looking for status file
            try:
                status_check = run_cmd(
                    f"ssh {ssh_opt} {ssh_user}@{ip} 'test -f /tmp/ssh_build_status.txt && echo EXISTS || echo NOTFOUND'",
                    capture=True, timeout=5, ignore=True, logger=logger
                )

                if status_check == "EXISTS":
                    # SSH build was executed, verify it
                    if logger:
                        logger.info("Detected SSH build execution, verifying build status...")
                    elif DEBUG_MODE == True:
                        log("Detected SSH build execution, verifying build status...")
                    elif DEBUG_MODE == False:
                        print("  [SSH Build] Verifying OpenSSH installation...")

                    if not verify_ssh_build(ip, ssh_opt, ssh_user, instance_name, logger=logger):
                        if logger:
                            logger.error("SSH build verification failed, aborting command execution")
                        elif DEBUG_MODE == True:
                            log("SSH build verification failed, aborting command execution", "ERROR")
                        elif DEBUG_MODE == False:
                            print("  [Error] SSH build verification failed")
                        return False
                else:
                    # Status file doesn't exist - SSH build was not executed (skip verification)
                    if logger:
                        logger.info("SSH build script detected but no status file found (build may have been skipped)")
                    elif DEBUG_MODE == True:
                        log("SSH build script detected but no status file found (build may have been skipped)")
            except Exception as e:
                # Verification check failed, but continue (don't block on verification issues)
                if logger:
                    logger.warn(f"SSH build verification check failed: {e}, continuing...")
                elif DEBUG_MODE == True:
                    log(f"SSH build verification check failed: {e}, continuing...", "WARNING")

    progress(instance_name, "All workloads completed", logger)

    if logger:
        logger.info("All workloads completed successfully")
    elif DEBUG_MODE == True:
        log("All workloads completed successfully")

    return True


def collect_results(ip, config, cloud, name, inst, key_path, ssh_strict_host_key_checking, instance_name, logger=None):
    """Collect benchmark results from remote instance."""
    progress(instance_name, "Collecting results", logger)

    if logger:
        logger.info(f"Collecting results from {ip}")
    elif DEBUG_MODE == True:
        log(f"Collecting results from {ip}")

    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk} -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=60 -o ServerAliveCountMax=10"
    ssh_user = config['common']['ssh_user']
    cloud_rep_dir = config['common']['cloud_reports_dir']

    if logger:
        logger.info("Creating tarball on remote instance...")
    elif DEBUG_MODE == True:
        log("Creating tarball on remote instance...")

    run_cmd(
        f"ssh {ssh_opt} {ssh_user}@{ip} "
        f"'tar -czf /tmp/reports.tar.gz -C $(dirname {cloud_rep_dir}) $(basename {cloud_rep_dir})'",
        capture=False,
        logger=logger
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

    if logger:
        logger.info(f"Downloading to {local_f} via SSH (avoiding SCP OpenSSL mismatch)...")
    elif DEBUG_MODE == True:
        log(f"Downloading to {local_f} via SSH (avoiding SCP OpenSSL mismatch)...")

    # Use SSH with stdout redirection instead of SCP to avoid OpenSSL version mismatch
    # This transfers the file via SSH stdout which is more reliable across different OpenSSL versions
    run_cmd(
        f"ssh {ssh_opt} {ssh_user}@{ip} 'cat /tmp/reports.tar.gz' > {local_f}",
        capture=False,
        logger=logger
    )

    progress(instance_name, "Results collected", logger)

    if logger:
        logger.info(f"Results collected: {local_f}")
    elif DEBUG_MODE == True:
        log(f"Results collected: {local_f}")
    else:
        print(f"Collected: {local_f}")


def process_instance(cloud, inst, config, key_path, log_dir):
    """Process a single cloud instance: launch, benchmark, collect, terminate."""
    name = inst['name']
    
    # Global sanitization: replace underscores with hyphens and ensure lowercase
    # This applies to ALL cloud providers (AWS, GCP, OCI) for consistency
    name = name.replace('_', '-').lower()
    inst['name'] = name # Update in place so downstream functions use the sanitized name
    
    instance_name = f"{cloud.upper()}:{name}"
    
    # Calculate costs
    cpu_cost = inst.get('cpu_cost_hour[730h-mo]', 0.0)
    # Extra storage cost is conditional
    storage_cost = 0.0
    if inst.get('extra_150g_storage'):
        storage_cost = inst.get('extra_150g_storage_cost_hour', 0.0)

    # Register with dashboard
    DASHBOARD.register(instance_name, cloud.upper(), inst['type'], cpu_cost=cpu_cost, storage_cost=storage_cost)
    
    # Inject cloud provider into instance dict for downstream use
    inst['cloud'] = cloud
    
    # Initialize logger
    logger = InstanceLogger(instance_name, DASHBOARD, log_dir)
    
    progress(instance_name, "Starting", logger)

    logger.info(f"Starting {cloud.upper()} instance: {name} ({inst['type']})")

    instance_id = None
    ip = None
    region = None
    project = None
    zone = None
    compartment_id = None

    try:
        if cloud == 'aws':
            region = config['aws']['region']

            logger.info(f"AWS Region: {region}")
            logger.info("Getting AWS key pair name...")

            key_name = run_cmd("aws ec2 describe-key-pairs --query 'KeyPairs[0].KeyName' --output text", logger=logger)

            logger.info(f"Key pair: {key_name}")

            sg_id = setup_aws_sg(region, config['common']['security_group_name'], logger=logger)
            instance_id, ip = launch_aws_instance(inst, config, region, key_name, sg_id, logger=logger)
        elif cloud == 'gcp':
            project = config['gcp']['project_id']
            if project == "AUTO_DETECT":
                project = get_gcp_project(logger=logger)
            zone = config['gcp']['zone']

            logger.info(f"GCP Project: {project}, Zone: {zone}")

            instance_id, ip = launch_gcp_instance(inst, config, project, zone, logger=logger)
        elif cloud == 'oci':
            compartment_id = config['oci']['compartment_id']
            region = config['oci']['region']

            logger.info(f"OCI Compartment: {compartment_id}, Region: {region}")

            instance_id, ip = launch_oci_instance(inst, config, compartment_id, region, logger=logger)
        else:
            msg = f"Unknown cloud provider: {cloud}"
            logger.error(msg)
            return

        if not ip or ip == "None":
            msg = f"Failed to get IP for {name}"
            logger.error(msg)
            progress(instance_name, "Launch Failed", logger)
            return

        # Register instance for cleanup on signal/exception
        register_instance(cloud, instance_id, name, region=region, project=project, zone=zone, compartment_id=compartment_id)

        progress(instance_name, f"Instance launched (IP: {ip})", logger)

        logger.info(f"Waiting 60s for SSH to become available (IP: {ip})...")

        time.sleep(60)

        ssh_strict = config['common'].get('ssh_strict_host_key_checking', False)

        # Set hostname if specified in instance configuration
        if 'hostname' in inst and inst['hostname']:
            hostname = inst['hostname']

            # Validate hostname format (alphanumeric and hyphens only, no leading/trailing hyphens)
            import re
            if not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', hostname):
                msg = f"Invalid hostname format: {hostname}. Must contain only lowercase letters, numbers, and hyphens."
                logger.error(msg)
            else:
                progress(instance_name, f"Setting hostname to: {hostname}", logger)
                logger.info(f"Setting hostname to: {hostname}")

                ssh_connect_timeout = config['common'].get('ssh_timeout', 20)
                ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={'yes' if ssh_strict else 'no'} -o UserKnownHostsFile=/dev/null -o ConnectTimeout={ssh_connect_timeout} -o ServerAliveInterval=60 -o ServerAliveCountMax=10"
                ssh_user = config['common']['ssh_user']

                # Set hostname using hostnamectl and update /etc/hosts
                hostname_cmd = f"sudo hostnamectl set-hostname {hostname} && sudo sed -i '/127.0.1.1/d' /etc/hosts && echo '127.0.1.1 {hostname}' | sudo tee -a /etc/hosts > /dev/null"

                try:
                    result = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} '{hostname_cmd}'", capture=False, timeout=30, logger=logger)

                    if result is not None:
                        progress(instance_name, f"Hostname set successfully", logger)
                        logger.info(f"Hostname set to: {hostname}")
                    else:
                        logger.warn(f"Failed to set hostname to: {hostname}")
                except Exception as e:
                    logger.error(f"Error setting hostname: {e}")

        # Run all commands sequentially
        commands_success = run_ssh_commands(ip, config, inst, key_path, ssh_strict, instance_name, logger=logger)
        if not commands_success:
            # Check if instance was terminated externally
            status = get_instance_status(cloud, instance_id, region, project, zone, logger)
            
            # AWS: terminated, shutting-down
            # GCP: TERMINATED (or empty if deleted)
            is_terminated = status in ['terminated', 'shutting-down', 'TERMINATED', 'STOPPING']
            
            if is_terminated:
                msg = f"Instance {name} was terminated externally (Status: {status})"
                logger.error(msg)
                progress(instance_name, "Terminated Externally", logger)
                DASHBOARD.update(instance_name, status="TERMINATED")
            else:
                msg = f"Command execution failed for {name}"
                logger.error(msg)
                progress(instance_name, "Workload Failed", logger)
            return

        # Collect results
        collect_results(ip, config, cloud, name, inst, key_path, ssh_strict, instance_name, logger=logger)

        progress(instance_name, "Completed successfully", logger=logger)
        logger.info(f"Instance {name} completed successfully")
        DASHBOARD.update(instance_name, status="COMPLETED")

    except Exception as e:
        msg = f"{cloud} instance {name}: {e}"
        logger.error(msg)
        import traceback
        logger.error(traceback.format_exc())
    finally:
        if instance_id:
            progress(instance_name, "Terminating", logger)
            logger.info(f"Terminating instance {name}...")

            if cloud == 'aws' and region:
                run_cmd(f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}", logger=logger)
            elif cloud == 'gcp' and project and zone:
                run_cmd(f"gcloud compute instances delete {name} --project={project} --zone={zone} --quiet", logger=logger)
            elif cloud == 'oci' and compartment_id:
                # TODO: Implement OCI instance termination
                logger.warn("OCI instance termination not yet implemented")
                # run_cmd(f"oci compute instance terminate --instance-id {instance_id} --force", logger=logger)

            logger.info(f"Instance {name} terminated")
            DASHBOARD.update(instance_name, status="TERMINATED")

            # Unregister from active instances tracking
            unregister_instance(instance_id)


def process_csp_instances(cloud, instances, config, key_path, log_dir):
    """Process all instances for a single CSP sequentially."""
    csp_name = cloud.upper()
    
    enabled_count = 0
    for inst in instances:
        if inst.get('enable'):
            process_instance(cloud, inst, config, key_path, log_dir)
            enabled_count += 1

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
        key_path_template = config['common']['ssh_key_path']
        key_path = validate_key_path(key_path_template)
        if not key_path:
            return

        # Prepare log directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        log_dir = Path(host_rep_dir) / "logs" / timestamp
        log_dir.mkdir(parents=True, exist_ok=True)
        DASHBOARD.set_log_dir(log_dir)

        # Start Dashboard
        DASHBOARD.start()

        # Prepare CSP tasks for parallel execution
        csp_tasks = []

        if config['aws']['enable'] and config['aws']['instances']:
            csp_tasks.append(('aws', config['aws']['instances']))
        
        if config['gcp']['enable'] and config['gcp']['instances']:
            csp_tasks.append(('gcp', config['gcp']['instances']))

        if config.get('oci', {}).get('enable') and config['oci']['instances']:
            csp_tasks.append(('oci', config['oci']['instances']))

        # Execute CSPs in parallel (max 3 workers: AWS, GCP, and OCI)
        if csp_tasks:
            with ThreadPoolExecutor(max_workers=min(3, len(csp_tasks))) as executor:
                # Submit all CSP tasks
                future_to_csp = {
                    executor.submit(process_csp_instances, cloud, instances, config, key_path, log_dir): cloud
                    for cloud, instances in csp_tasks
                }

                # Wait for all to complete
                for future in as_completed(future_to_csp):
                    cloud = future_to_csp[future]
                    try:
                        future.result()
                    except Exception as exc:
                        # Log error to a general error log since instance context might be lost
                        with open(log_dir / "general_errors.log", "a") as f:
                            f.write(f"[{datetime.now()}] {cloud.upper()} thread failed: {exc}\n")
                        
        # Stop Dashboard
        DASHBOARD.stop()

    except Exception as e:
        DASHBOARD.stop() # Ensure dashboard stops on error
        print(f"\n[Fatal Error] {e}")
        import traceback
        traceback.print_exc()
        # Cleanup any active instances before exiting
        cleanup_active_instances()
        raise


if __name__ == "__main__":
    main()
