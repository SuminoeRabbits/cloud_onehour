#!/usr/bin/env python3
"""
Cloud Executor with Parallel Execution (within same CSP)

This script enables parallel execution of multiple instances within the same CSP,
extending the capabilities of cloud_exec.py which only parallelizes across different CSPs.

Key Features:
- Parallel execution within a single CSP (AWS, GCP, or OCI)
- CloudProvider abstraction for common/CSP-specific logic separation
- API rate limit handling with exponential backoff retry
- Guaranteed cleanup on interruption (Ctrl+C) to prevent ongoing charges
- Compatible with existing cloud_config.json and cloud_instances.json

Usage:
    ./cloud_exec_para.py --csp aws                    # Run AWS instances only
    ./cloud_exec_para.py --csp gcp --max-workers 3    # Run GCP with custom parallelism
    ./cloud_exec_para.py --csp oci --dry-run          # Show execution plan only

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
"""

import json
import py_compile
import subprocess
import os
import time
import sys
import signal
import threading
import re
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple, List, Callable

# =========================================================================================
# GLOBAL VARIABLES
# =========================================================================================

# Active instances tracking (for cleanup on interruption)
active_instances = []  # List of {'cloud': str, 'instance_id': str, 'name': str, ...}
active_instances_lock = threading.Lock()

# Dashboard instance (shared across threads)
DASHBOARD = None

# =========================================================================================
# CLOUD PROVIDER ABSTRACT BASE CLASS
# =========================================================================================

class CloudProvider(ABC):
    """
    Abstract base class for CSP-specific logic.

    Each CSP (AWS, GCP, OCI) implements this interface to provide:
    - Resource initialization (Security Groups, Subnets, etc.)
    - Instance lifecycle management (launch, terminate, status check)
    - Name validation and existence checking
    - Error classification for retry logic
    - CSP-specific rate limits and recommendations
    """

    def __init__(self, config: Dict[str, Any], csp_config: Dict[str, Any]):
        """
        Initialize CloudProvider.

        Args:
            config: Full cloud_config.json content
            csp_config: CSP-specific config section (e.g., config['aws'])
        """
        self.config = config
        self.csp_config = csp_config
        self.shared_resources = None  # Set by initialize_shared_resources()

    # ========================================
    # Abstract Methods (must be implemented)
    # ========================================

    @abstractmethod
    def initialize_shared_resources(self, logger: Optional['InstanceLogger'] = None) -> Dict[str, Any]:
        """
        Initialize CSP-shared resources before parallel execution.

        Called once before launching any instances to set up shared infrastructure
        like Security Groups, Subnets, etc.

        Returns:
            Dictionary of shared resources (e.g., {'sg_id': 'sg-xxx', 'region': 'us-east-1'})
        """
        pass

    @abstractmethod
    def validate_instance_name(self, name: str) -> None:
        """
        Validate instance name against CSP-specific constraints.

        Args:
            name: Instance name to validate

        Raises:
            ValueError: If name violates CSP constraints
        """
        pass

    @abstractmethod
    def check_instance_exists(
        self,
        instance_name: str,
        inst: Optional[Dict[str, Any]] = None,
        logger: Optional['InstanceLogger'] = None
    ) -> bool:
        """
        Check if an instance with the given name already exists.

        Args:
            instance_name: Name to check
            inst: Instance definition (optional; some CSPs may use it)
            logger: Logger for status messages

        Returns:
            True if instance exists, False otherwise
        """
        pass

    @abstractmethod
    def is_rate_limit_error(self, exception: Exception) -> bool:
        """
        Check if exception is a rate limit error.

        Args:
            exception: Exception to classify

        Returns:
            True if rate limit error (should retry)
        """
        pass

    @abstractmethod
    def is_retryable_error(self, exception: Exception) -> bool:
        """
        Check if exception is retryable (rate limit + temporary errors).

        Args:
            exception: Exception to classify

        Returns:
            True if retryable, False if should fail immediately
        """
        pass

    @abstractmethod
    def get_recommended_max_workers(self) -> int:
        """
        Get CSP-recommended parallel worker count.

        Returns:
            Recommended max_workers (e.g., 3 for AWS, 5 for GCP, 2 for OCI)
        """
        pass

    @abstractmethod
    def get_launch_delay_between_instances(self) -> float:
        """
        Get delay (in seconds) between instance launches for rate limiting.

        Returns:
            Delay in seconds (e.g., 0.5 for AWS, 0.2 for GCP, 1.0 for OCI)
        """
        pass

    @abstractmethod
    def launch_instance(
        self,
        inst: Dict[str, Any],
        logger: Optional['InstanceLogger'] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Launch a cloud instance.

        Args:
            inst: Instance definition from cloud_instances.json
            logger: Logger for progress messages

        Returns:
            (instance_id, ip_address) on success, (None, None) on failure
        """
        pass

    @abstractmethod
    def terminate_instance(
        self,
        instance_id: str,
        inst: Dict[str, Any],
        logger: Optional['InstanceLogger'] = None
    ) -> bool:
        """
        Terminate a cloud instance.

        Args:
            instance_id: Instance ID to terminate
            inst: Instance definition (for metadata)
            logger: Logger for status messages

        Returns:
            True on success, False on failure
        """
        pass

    @abstractmethod
    def get_instance_status(
        self,
        instance_id: str,
        inst: Dict[str, Any],
        logger: Optional['InstanceLogger'] = None
    ) -> str:
        """
        Get current instance status.

        Args:
            instance_id: Instance ID to check
            inst: Instance definition (for metadata)
            logger: Logger for status messages

        Returns:
            Status string ('running', 'terminated', 'unknown', etc.)
        """
        pass


# =========================================================================================
# COMMON UTILITY FUNCTIONS
# =========================================================================================

def sanitize_instance_name(name: str) -> str:
    """
    Sanitize instance name (replace underscores with hyphens, lowercase).

    This is the same logic from cloud_exec.py:1558

    Args:
        name: Original instance name (e.g., "aws_m8a_2xlarge")

    Returns:
        Sanitized name (e.g., "aws-m8a-2xlarge")
    """
    return name.replace('_', '-').lower()


def is_apt_setup_command(cmd: str) -> bool:
    """
    Detect apt setup commands that are prone to transient failures.

    We only wrap update+install flows to avoid altering unrelated workloads.
    """
    cmd_lower = cmd.lower()
    return "apt-get" in cmd_lower and "update" in cmd_lower and "install" in cmd_lower


def wrap_apt_command_with_retries(cmd: str) -> str:
    """
    Wrap apt setup commands with lock waits and retries to reduce transient failures.

    Preserves any trailing redirection (e.g., > /tmp/apt_setup.log 2>&1).
    """
    redir_match = re.search(r'(\s*>\s*/tmp/\S+\s*2>&1)\s*$', cmd)
    redir = redir_match.group(1) if redir_match else ""
    cmd_core = cmd[:redir_match.start()].strip() if redir_match else cmd.strip()

    escaped_cmd = cmd_core.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    wrapped = (
        'bash -lc "'
        'set -e; '
        'if command -v cloud-init >/dev/null 2>&1; then sudo cloud-init status --wait || true; fi; '
        'for attempt in 1 2 3; do '
        'while sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1 || '
        'sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || '
        'sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1 || '
        'sudo fuser /var/cache/apt/archives/lock >/dev/null 2>&1; do '
        'sleep 5; '
        'done; '
        f'{escaped_cmd} && exit 0; '
        'sleep $((attempt*10)); '
        'done; '
        'exit 100"'
    )

    if redir:
        wrapped = f"{wrapped} {redir}"
    return wrapped


def check_instance_name_conflict(
    provider: CloudProvider,
    instance_name: str,
    inst: Optional[Dict[str, Any]] = None,
    logger: Optional['InstanceLogger'] = None
) -> bool:
    """
    Check if instance name is already in use (common logic).

    Args:
        provider: CloudProvider instance
        instance_name: Name to check
        logger: Logger for error messages

    Returns:
        True if name conflict exists (cannot launch), False if safe to proceed
    """
    try:
        exists = provider.check_instance_exists(instance_name, inst, logger)

        if exists:
            msg = (f"Instance name '{instance_name}' is already in use. "
                   f"Another instance or script may be running. "
                   f"Please terminate the existing instance or wait for completion.")

            if logger:
                logger.error(msg)
            else:
                print(f"[ERROR] {msg}")

            return True  # Conflict exists

        return False  # No conflict

    except Exception as e:
        # Existence check error - warn but proceed with launch
        warn_msg = f"Failed to check instance name: {e}. Proceeding with launch..."
        if logger:
            logger.warn(warn_msg)
        else:
            print(f"[WARN] {warn_msg}")

        return False  # Treat error as no conflict


def retry_with_exponential_backoff(
    func: Callable,
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    logger: Optional['InstanceLogger'] = None,
    error_classifier: Optional[Callable[[Exception], bool]] = None
) -> Any:
    """
    Retry a function with exponential backoff on errors.

    Backoff formula: min(base_delay * (2 ** attempt), max_delay)
    - attempt 0: 2.0s
    - attempt 1: 4.0s
    - attempt 2: 8.0s
    - attempt 3: 16.0s
    - attempt 4: 32.0s
    - attempt 5+: 60.0s (capped)

    Args:
        func: Function to execute
        max_retries: Maximum retry attempts (default: 5)
        base_delay: Initial delay in seconds (default: 2.0)
        max_delay: Maximum delay in seconds (default: 60.0)
        logger: Logger for retry messages
        error_classifier: Function to check if error is retryable (None = retry all)

    Returns:
        Result of func()

    Raises:
        Exception from last retry attempt
    """
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            # Check if error is retryable
            if error_classifier and not error_classifier(e):
                # Non-retryable error - fail immediately
                raise

            if attempt == max_retries - 1:
                # Last attempt failed
                raise

            # Calculate exponential backoff delay
            delay = min(base_delay * (2 ** attempt), max_delay)

            if logger:
                logger.warn(f"Retry {attempt+1}/{max_retries} after {delay:.1f}s: {e}")

            time.sleep(delay)


# =========================================================================================
# ACTIVE INSTANCES MANAGEMENT
# =========================================================================================

def register_instance(cloud: str, instance_id: str, name: str, **kwargs) -> None:
    """
    Register a launched instance for cleanup tracking.

    Args:
        cloud: CSP name ('aws', 'gcp', 'oci')
        instance_id: Instance ID
        name: Instance name
        **kwargs: CSP-specific metadata (region, project, zone, etc.)
    """
    with active_instances_lock:
        inst_info = {
            'cloud': cloud,
            'instance_id': instance_id,
            'name': name,
            **kwargs
        }
        active_instances.append(inst_info)
        print(f"[REGISTER] Added to active_instances: {cloud}:{name} ({instance_id})")


def unregister_instance(instance_id: str) -> None:
    """
    Unregister an instance after successful termination.

    Args:
        instance_id: Instance ID to remove
    """
    with active_instances_lock:
        active_instances[:] = [
            inst for inst in active_instances
            if inst['instance_id'] != instance_id
        ]
        print(f"[UNREGISTER] Removed from active_instances: {instance_id}")



# =========================================================================================
# SYNTAX VERIFICATION (Pre-flight checks)
# =========================================================================================

def verify_syntax() -> bool:
    """
    Verify Python syntax of cloud_exec_para.py before execution.

    Returns:
        True: Syntax OK
        False: Syntax errors found
    """
    try:
        script_path = Path(__file__).resolve()
        py_compile.compile(str(script_path), doraise=True)
        print(f"[✓] Syntax check passed: {script_path.name}")
        return True

    except py_compile.PyCompileError as e:
        print(f"[✗] Syntax error in {script_path.name}:")
        print(f"    Line {e.exc_value.lineno}: {e.exc_value.msg}")
        if e.exc_value.text:
            print(f"    {e.exc_value.text.rstrip()}")
        return False

    except Exception as e:
        print(f"[WARN] Syntax check failed: {e}")
        return True  # Proceed with caution


def verify_pts_runner_syntax() -> List[str]:
    """
    Verify pts_runner scripts syntax (optional check).

    Returns:
        List of files with syntax errors (empty if all OK)
    """
    errors = []
    pts_dir = Path("pts_runner")

    if not pts_dir.exists():
        return errors

    for script in pts_dir.glob("pts_runner_*.py"):
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"{script.name}: Line {e.exc_value.lineno}")
        except Exception:
            pass  # Ignore other errors

    return errors


def verify_json_files(config_path: str) -> bool:
    """
    Verify JSON syntax for cloud_config.json and cloud_instances.json.
    """
    try:
        with open(config_path, 'r') as f:
            json.load(f)
    except json.JSONDecodeError as e:
        print(f"[✗] JSON syntax error in {config_path}: Line {e.lineno} Col {e.colno}")
        print(f"    {e.msg}")
        return False
    except Exception as e:
        print(f"[✗] Failed to read {config_path}: {e}")
        return False

    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        inst_def_file = config.get('instance_definitions_file', 'cloud_instances.json')
        inst_def_path = Path(config_path).parent / inst_def_file
        with open(inst_def_path, 'r') as f:
            json.load(f)
    except json.JSONDecodeError as e:
        print(f"[✗] JSON syntax error in {inst_def_path}: Line {e.lineno} Col {e.colno}")
        print(f"    {e.msg}")
        return False
    except Exception as e:
        print(f"[✗] Failed to read {inst_def_path}: {e}")
        return False

    print("[✓] JSON syntax check passed: cloud_config.json / cloud_instances.json")
    return True

def cleanup_active_instances(signum=None, frame=None):
    """
    Emergency cleanup on interruption (Ctrl+C or kill signal).

    Terminates all active instances to prevent ongoing charges.
    Continues attempting all terminations even if individual ones fail.

    Args:
        signum: Signal number (SIGINT or SIGTERM)
        frame: Stack frame (unused)
    """
    print("\n[CLEANUP] Interrupt received. Terminating all active instances...")

    with active_instances_lock:
        if not active_instances:
            print("[CLEANUP] No active instances to terminate.")
            if signum:
                sys.exit(1)
            return

        print(f"[CLEANUP] Found {len(active_instances)} active instance(s). Starting termination...")

        for inst_info in active_instances:
            cloud = inst_info['cloud']
            instance_id = inst_info['instance_id']
            instance_name = inst_info.get('name', instance_id)

            print(f"[CLEANUP] Terminating {cloud} instance: {instance_name} ({instance_id})")

            try:
                if cloud == 'aws':
                    region = inst_info.get('region', 'ap-northeast-1')
                    subprocess.run(
                        f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}",
                        shell=True,
                        timeout=30,
                        capture_output=True
                    )
                    print(f"[CLEANUP] AWS instance {instance_id} termination initiated")

                elif cloud == 'gcp':
                    project = inst_info.get('project')
                    zone = inst_info.get('region') or inst_info.get('zone')
                    subprocess.run(
                        f"gcloud compute instances delete {instance_name} "
                        f"--project={project} --zone={zone} --quiet",
                        shell=True,
                        timeout=30,
                        capture_output=True
                    )
                    print(f"[CLEANUP] GCP instance {instance_name} termination initiated")

                elif cloud == 'oci':
                    region = inst_info.get('region')
                    cmd_prefix = f"OCI_REGION={region} " if region else ""
                    subprocess.run(
                        f"{cmd_prefix}oci compute instance terminate --instance-id {instance_id} --force",
                        shell=True,
                        timeout=300,  # OCI takes longer
                        capture_output=True
                    )
                    print(f"[CLEANUP] OCI instance {instance_id} termination initiated")

            except subprocess.TimeoutExpired:
                print(f"[CLEANUP] Warning: Termination timeout for {instance_id}. "
                      f"Instance may still be running. Please check manually.")
                # Continue to next instance

            except Exception as e:
                print(f"[CLEANUP] Error terminating {instance_id}: {e}")
                # Display manual cleanup command
                if cloud == 'aws':
                    print(f"[CLEANUP] Manual cleanup: aws ec2 terminate-instances --region {inst_info.get('region')} --instance-ids {instance_id}")
                elif cloud == 'gcp':
                    zone = inst_info.get('region') or inst_info.get('zone')
                    print(f"[CLEANUP] Manual cleanup: gcloud compute instances delete {instance_name} --project={inst_info.get('project')} --zone={zone}")
                elif cloud == 'oci':
                    region = inst_info.get('region')
                    prefix = f"OCI_REGION={region} " if region else ""
                    print(f"[CLEANUP] Manual cleanup: {prefix}oci compute instance terminate --instance-id {instance_id} --force")
                # Continue to next instance

        # Clear list after all termination attempts
        active_instances.clear()
        print("[CLEANUP] All termination requests sent. Exiting.")

    if signum:
        sys.exit(1)


def get_manual_cleanup_command(
    cloud: str,
    instance_id: str,
    name: str,
    shared_resources: Dict[str, Any],
    region: Optional[str] = None,
    project: Optional[str] = None,
    zone: Optional[str] = None
) -> str:
    """
    Generate manual cleanup command for failed termination.

    Args:
        cloud: CSP name
        instance_id: Instance ID
        name: Instance name
        shared_resources: CSP shared resources (for region, project, zone)

    Returns:
        CLI command string for manual cleanup
    """
    if cloud == 'aws':
        region = region or shared_resources.get('region', 'ap-northeast-1')
        return f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}"
    elif cloud == 'gcp':
        project = project or shared_resources.get('project')
        zone = zone or shared_resources.get('region') or shared_resources.get('zone')
        return f"gcloud compute instances delete {name} --project={project} --zone={zone}"
    elif cloud == 'oci':
        prefix = f"OCI_REGION={region} " if region else ""
        return f"{prefix}oci compute instance terminate --instance-id {instance_id} --force"
    else:
        return f"Unknown cloud type: {cloud}"


# =========================================================================================
# DASHBOARD AND LOGGER CLASSES (from cloud_exec.py)
# =========================================================================================

class Dashboard:
    """Manages real-time console dashboard for parallel execution."""
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.instances = {}
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
        self.WARN = self.WARNING  # Alias for compatibility

    def register(self, instance_name, cloud_type, machine_type, cpu_cost=0.0, storage_cost=0.0, region=None):
        with self.lock:
            self.instances[instance_name] = {
                'cloud': cloud_type,
                'region': region,
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
                    if status in ['COMPLETED', 'TERMINATED', 'ERROR', 'TERM_TIMEOUT', 'TERM_FAILED'] and data['end_time'] is None:
                        data['end_time'] = datetime.now()
                if step:
                    if data['step'] != step:
                        data['step'] = step
                        data['step_start'] = datetime.now()
                        # Reset instance timer at actual launch time
                        if step.startswith("Instance launched"):
                            data['start_time'] = datetime.now()
                if color: data['color'] = color
                data['last_update'] = datetime.now()

    def remove(self, instance_name):
        """Remove instance from dashboard (e.g., failed before instance_id)."""
        if not self.enabled:
            return
        with self.lock:
            if instance_name in self.instances:
                del self.instances[instance_name]

    def add_history(self, instance_name, step_name, duration_sec, status="OK"):
        """Record a completed step in history."""
        if not self.enabled: return
        with self.lock:
            if instance_name in self.instances:
                data = self.instances[instance_name]

                # Track total workloads if available in step name
                try:
                    m = re.search(r'Workload\s+(\d+)\s*/\s*(\d+)', step_name)
                    if m:
                        total = int(m.group(2))
                        prev_total = data.get('workload_total')
                        if not prev_total or total > prev_total:
                            data['workload_total'] = total
                except Exception:
                    pass

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

                # Extract simple step name
                simple_name = step_name
                if "Workload" in step_name:
                    try:
                        parts = step_name.split(':')
                        w_part = parts[0].replace('Workload', 'W').split('/')[0].strip()
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
        self._render_once()

    def _render_loop(self):
        self._render_once()
        while self._running:
            time.sleep(5)
            self._render_once()

    def _render_once(self):
        lines = []

        run_duration = datetime.now() - self.start_time
        run_str = str(run_duration).split('.')[0]
        lines.append(f"{self.BOLD}CLOUD BENCHMARKING EXECUTOR (Run: {run_str}){self.ENDC}")
        lines.append("=" * 100)
        lines.append(f"{'INSTANCE (TYPE)':<30} | {'STAT':<4} | {'TIME':<7} | {'COST':<7}")
        lines.append("-" * 100)

        with self.lock:
            sorted_insts = sorted(
                self.instances.items(),
                key=lambda item: (
                    item[1].get('cloud') or '',
                    item[1].get('region') or '',
                    item[0]
                )
            )

            current_group = None
            for name, data in sorted_insts:
                group_key = f"{data.get('cloud', 'UNKNOWN')} / {data.get('region', 'unknown-region')}"
                if group_key != current_group:
                    lines.append(f"{self.BOLD}{group_key}{self.ENDC}")
                    lines.append("-" * 100)
                    current_group = group_key
                # Status
                raw_stat = data['status']
                stat_map = {
                    'RUNNING': 'RUN ', 'COMPLETED': 'DONE', 'TERMINATED': 'TERM',
                    'PENDING': 'WAIT', 'ERROR': 'ERR ', 'TERM_TIMEOUT': 'T/O ',
                    'TERM_FAILED': 'TFAL'
                }
                compact_stat = stat_map.get(raw_stat, raw_stat[:4])
                status_str = f"{data['color']}{compact_stat}{self.ENDC}"

                # Name
                short_name = name
                for suffix in ['-amd64', '-arm64', '-vcpu-2', '-vcpu-4', '-vcpu-8', '-vcpu-16']:
                    short_name = short_name.replace(suffix, '')

                short_type = data['type'].replace('standard', 'std').replace('large', 'lg')
                display_name = f"{short_name} ({short_type})"
                if len(display_name) > 30:
                    display_name = display_name[:27] + "..."

                # Duration and Cost
                end = data['end_time'] if data['end_time'] else datetime.now()
                duration = end - data['start_time']
                duration_str = str(duration).split('.')[0]

                hours = duration.total_seconds() / 3600.0
                total_rate = data['cpu_cost'] + data['storage_cost']
                cost = hours * total_rate
                cost_str = f"${cost:.2f}"

                lines.append(f"{display_name:<30} | {status_str:<4} | {duration_str:<7} | {cost_str:<7}")

                # History
                history_items = data.get('history', [])
                workload_total = data.get('workload_total')
                if workload_total and workload_total <= 10:
                    display_history = history_items
                else:
                    display_history = history_items[-5:]

                for item in display_history:
                    stat = item['status']
                    color = self.GREEN if stat == "OK" else (self.FAIL if stat in ["ERR", "TO"] else self.BOLD)
                    item_str = f"  [{color}{stat}{self.ENDC}] {item['name']} ({item['duration']})"
                    lines.append(f"{item_str}")

                # Current step
                if raw_stat not in ['COMPLETED', 'TERMINATED', 'TERM_TIMEOUT', 'TERM_FAILED']:
                    step_elapsed = datetime.now() - data.get('step_start', datetime.now())

                    if step_elapsed.total_seconds() < 3600:
                        mm = int(step_elapsed.total_seconds() // 60)
                        ss = int(step_elapsed.total_seconds() % 60)
                        step_timer = f"[{mm:02}:{ss:02}]"
                    else:
                        step_timer = f"[{str(step_elapsed).split('.')[0]}]"

                    step_name = data['step']
                    simple_step_name = step_name
                    if "Workload" in step_name:
                        try:
                            parts = step_name.split(':')
                            w_part = parts[0].replace('Workload', 'W').split('/')[0].strip()
                            cmd_part = parts[1].strip()
                            simple_step_name = f"{w_part}: {cmd_part}"
                        except:
                            pass

                    if len(simple_step_name) > 60:
                        simple_step_name = simple_step_name[:57] + "..."

                    current_str = f"  [{self.CYAN}>>{self.ENDC}]   {simple_step_name} {step_timer}"
                    lines.append(f"{current_str}")

                lines.append("-" * 100)

        lines.append("=" * 100)

        full_output = "\n".join(lines)

        # Print to console
        print(f"\033[2J\033[H{full_output}")
        sys.stdout.flush()

        # Write to dashboard.log
        if self.log_dir:
            try:
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                clean_output = ansi_escape.sub('', full_output)

                dashboard_file = self.log_dir / "dashboard.log"
                with open(dashboard_file, 'w') as f:
                    f.write(f"Last Update: {datetime.now()}\n")
                    f.write(clean_output + "\n")
            except Exception:
                pass


class InstanceLogger:
    """Handles logging to file and updating dashboard for a specific instance."""
    def __init__(self, instance_name, global_dashboard, log_dir):
        self.name = instance_name
        self.dashboard = global_dashboard
        self.log_file = log_dir / f"{instance_name.replace(':', '_')}.log"

        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.log_file, 'w') as f:
            f.write(f"=== Log started for {instance_name} ===\n")
            f.write(f"Timestamp: {datetime.now()}\n\n")

    def info(self, message):
        """Log info message to file."""
        self._write(f"[INFO] {message}")

    def error(self, message, fatal=True):
        """Log error message to file and optionally update dashboard."""
        self._write(f"[ERROR] {message}")
        if fatal:
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
            pass


def progress(instance_name, step, logger=None):
    """Update progress on dashboard and log to file if logger is available."""
    if logger:
        logger.progress(step)
    else:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{instance_name}] {step}", flush=True)


# =========================================================================================
# HELPER FUNCTIONS (from cloud_exec.py)
# =========================================================================================

def run_cmd(cmd, capture=True, ignore=False, timeout=None, logger=None):
    """Execute shell command and return output or status."""
    try:
        if logger:
            logger.cmd(f"Executing: {cmd[:150]}{'...' if len(cmd) > 150 else ''}")

        start_time = time.time()
        res = subprocess.run(
            cmd,
            shell=True,
            capture_output=capture,
            text=True,
            check=not ignore,
            timeout=timeout,
            stdin=subprocess.DEVNULL
        )
        elapsed = time.time() - start_time

        if logger:
            logger.info(f"Command completed in {elapsed:.2f}s")

        return res.stdout.strip() if capture else True
    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout} seconds"
        if logger:
            if ignore:
                logger.warn(msg)
            else:
                logger.error(msg)
        else:
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
        else:
            print(f"[{'Warn' if ignore else 'Error'}] {err_msg}")

        if not ignore:
            raise
        return None


def build_storage_config(inst, cloud_type):
    """Build storage configuration arguments for different cloud providers."""
    if not inst.get('extra_150g_storage', False):
        return ""

    if cloud_type == 'aws':
        return (
            '--block-device-mappings '
            '\'[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,'
            '"VolumeType":"gp3","DeleteOnTermination":true}}]\' '
        )
    elif cloud_type == 'gcp':
        return "--boot-disk-size=150GB "
    elif cloud_type == 'oci':
        # Note: OCI boot volumes are automatically deleted when instance is terminated
        # No explicit preserve flag needed in newer OCI CLI versions
        return "--boot-volume-size-in-gbs 150 "
    else:
        return ""


def get_gcp_project(logger=None):
    """Detect GCP project ID from gcloud config."""
    if logger:
        logger.info("Detecting GCP project ID...")

    project = run_cmd("gcloud config get-value project", logger=logger)
    if project and "(unset)" not in project:
        if logger:
            logger.info(f"GCP project: {project}")
        return project

    if logger:
        logger.warn("GCP project not configured")
    return None


# Note: The following large functions from cloud_exec.py are imported inline.
# These include: setup_aws_sg, launch_aws_instance, launch_gcp_instance, 
# launch_oci_instance, run_ssh_commands, collect_results
# For brevity in this implementation, we reference them as external dependencies
# that will be copied from cloud_exec.py lines 541-1600

# Placeholder note: In production, copy these functions from cloud_exec.py:
# - setup_aws_sg (lines 541-612)
# - launch_aws_instance (lines 614-713)
# - launch_gcp_instance (lines 716-852) 
# - launch_oci_instance (lines 854-1051)
# - run_ssh_commands (lines 1053-1491)
# - collect_results (lines 1493-1600)

def setup_aws_sg(region, sg_name, logger=None):
    """Create/retrieve AWS security group and authorize SSH access from current IP."""
    if logger:
        logger.info(f"Setting up AWS security group: {sg_name} in {region}")
    elif False:
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
        elif False:
            log("Security group not found, creating new one...")
            
        vpc_id = run_cmd(
            f"aws ec2 describe-vpcs --region {region} --query 'Vpcs[0].VpcId' --output text",
            logger=logger
        )
        if logger:
            logger.info(f"Using VPC: {vpc_id}")
        elif False:
            log(f"Using VPC: {vpc_id}")

        sg_id = run_cmd(
            f"aws ec2 create-security-group --group-name {sg_name} "
            f"--description 'SG for benchmarking' --vpc-id {vpc_id} --region {region} "
            f"--query 'GroupId' --output text",
            logger=logger
        )
        if logger:
            logger.info(f"Created security group: {sg_id}")
        elif False:
            log(f"Created security group: {sg_id}")
    else:
        if logger:
            logger.info(f"Using existing security group: {sg_id}")
        elif False:
            log(f"Using existing security group: {sg_id}")

    if logger:
        logger.info("Getting current public IP...")
    elif False:
        log("Getting current public IP...")
        
    my_ip = run_cmd("curl -s https://checkip.amazonaws.com", logger=logger)
    
    if logger:
        logger.info(f"Current IP: {my_ip}")
        logger.info(f"Authorizing SSH access from {my_ip}/32...")
    elif False:
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
    elif False:
        log("Security group configured")

    return sg_id

def launch_aws_instance(inst, config, region, key_name, sg_id, logger=None):
    """Launch AWS instance and return (instance_id, ip)."""
    if logger:
        logger.info(f"Launching AWS instance: {inst['name']} ({inst['type']})")
    elif False:
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
    elif False:
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
        elif False:
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
            elif False:
                log(f"Found AMI with pattern '{pattern}': {ami}")
            break


    if not ami or ami == "None":
        msg = f"No AMI found for Ubuntu {os_version} ({codename}) {inst['arch']} in {region}"
        if logger:
            logger.error(msg)
        elif False:
            log(msg, "ERROR")
        else:
            print(f"[Error] {msg}")
        return None, None

    if logger:
        logger.info(f"Using AMI: {ami}")
        logger.info("Starting instance...")
    elif False:
        log(f"Using AMI: {ami}")
        log("Starting instance...")

    # Build storage configuration using centralized helper
    storage_config = build_storage_config(inst, 'aws')

    tag_spec = f"--tag-specifications 'ResourceType=instance,Tags=[{{Key=Name,Value={inst['name']}}}]' "
    instance_id = run_cmd(
        f"aws ec2 run-instances --region {region} --image-id {ami} "
        f"--instance-type {inst['type']} --key-name {key_name} "
        f"--security-group-ids {sg_id} {storage_config}"
        f"{tag_spec}"
        f"--query 'Instances[0].InstanceId' --output text",
        logger=logger
    )

    if logger:
        logger.info(f"Instance ID: {instance_id}")
        logger.info("Waiting for instance to be running...")
    elif False:
        log(f"Instance ID: {instance_id}")
        log("Waiting for instance to be running...")

    run_cmd(f"aws ec2 wait instance-running --region {region} --instance-ids {instance_id}", logger=logger, timeout=600)

    ip = run_cmd(
        f"aws ec2 describe-instances --region {region} --instance-ids {instance_id} "
        f"--query 'Reservations[0].Instances[0].PublicIpAddress' --output text",
        logger=logger
    )

    if logger:
        logger.info(f"Instance running with IP: {ip}")
    elif False:
        log(f"Instance running with IP: {ip}")

    return instance_id, ip


def launch_gcp_instance(inst, config, project, zone, logger=None):
    """Launch GCP instance and return (instance_id, ip)."""
    name = inst['name']

    if logger:
        logger.info(f"Launching GCP instance: {name} ({inst['type']})")
    elif False:
        log(f"Launching GCP instance: {name} ({inst['type']})")

    os_version = config['common']['os_version']
    img_arch = "arm64" if inst['arch'] == "arm64" else "amd64"

    version_number = os_version.replace('.', '')
    is_lts = os_version.endswith('.04') and int(os_version.split('.')[0]) % 2 == 0
    lts_suffix = "-lts" if is_lts else ""
    # GCP Ubuntu families changed for some releases (e.g., 24.04 requires arch suffix).
    # Probe candidates and pick the first family that exists.
    arch_suffix = img_arch
    # Try several known family naming variants across releases.
    # Examples:
    # 24.04: ubuntu-2404-lts-amd64 / ubuntu-2404-lts-arm64
    # 22.04: ubuntu-2204-lts (amd64), ubuntu-2204-lts-arm64
    base = f"ubuntu-{version_number}"
    candidates = [
        f"{base}{lts_suffix}-{arch_suffix}",
        f"{base}{lts_suffix}",
        f"{base}-{arch_suffix}",
        f"{base}",
    ]
    # De-duplicate while preserving order
    seen = set()
    family_candidates = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            family_candidates.append(c)
    image_family = None
    for candidate in family_candidates:
        # Use list+filter for better compatibility with gcloud versions
        exists = run_cmd(
            f"gcloud compute images list --project=ubuntu-os-cloud "
            f"--filter=\"family={candidate}\" --limit=1 --format='get(name)'",
            logger=logger,
            ignore=True
        )
        if exists:
            image_family = candidate
            break

    if not image_family:
        msg = f"No Ubuntu image family found for {os_version} ({img_arch}) in ubuntu-os-cloud"
        if logger:
            logger.error(msg)
        else:
            print(f"[Error] {msg}")
        return None, None

    if logger:
        logger.info(f"Using image family: {image_family}")
        logger.info("Creating instance...")
    elif False:
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
        logger=logger,
        timeout=600
    )

    if ip:
        if logger:
            logger.info(f"Instance created with IP: {ip}")
        elif False:
            log(f"Instance created with IP: {ip}")
    else:
        if logger:
            logger.error("Failed to create instance")
        elif False:
            log("Failed to create instance", "ERROR")
        else:
            print("[Error] Failed to create GCP instance")

    return name if ip else None, ip


    # 1. Find Subnet ID (from config or auto-detect public subnet)
    subnet_id = config['oci'].get('subnet_id')

    if not subnet_id:
        if logger:
            logger.info("Subnet ID not configured, auto-detecting public subnet in compartment...")
        #elif DEBUG_MODE:
            log("Subnet ID not configured, auto-detecting public subnet in compartment...")
        
        # Find VCNs first
        vcns_json = run_cmd(f"oci network vcn list --compartment-id {compartment_id} --query 'data[0].id' --raw-output", logger=logger)
        if vcns_json and vcns_json != "None":
            # List subnets in the VCN (assuming first VCN is correct one if multiple)
            # We want a public subnet (prohibit-public-ip-on-vnic = false)
            subnet_cmd = (
                f"oci network subnet list --compartment-id {compartment_id} --vcn-id {vcns_json} "
                f"--query 'data[?\"prohibit-public-ip-on-vnic\"==`false`] | [0].id' --raw-output"
            )
            subnet_id = run_cmd(subnet_cmd, logger=logger)

    if not subnet_id or subnet_id == "None":
        msg = "Failed to detect a valid public subnet. Please specify 'subnet_id' in config.yaml or create a public subnet."
        if logger: logger.error(msg)
        else: print(f"[Error] {msg}")
        return None, None

    if logger:
        logger.info(f"Using Subnet ID: {subnet_id}")

    # 2. Find Availability Domain (pick first one)
    ad = run_cmd(f"oci iam availability-domain list --compartment-id {compartment_id} --query 'data[0].name' --raw-output", logger=logger)
    if not ad:
        if logger: logger.error("Failed to get Availability Domain")
        return None, None

    # 3. Find Ubuntu Image
    os_version = config['common']['os_version'] # e.g. "22.04"
    arch_filter = "Canonical Ubuntu" 
    # For OCI, we need to search carefully. Canonical images usually have "Canonical-Ubuntu-22.04-..." names.
    # Architecture: "VM.Standard.A1.Flex", "VM.Standard.A4.Flex" -> we need aarch64 image. "VM.Standard.E*" -> x86_64.
    # OCI ARM shapes have ".A" in the type name (e.g., VM.Standard.A1.Flex, VM.Standard.A4.Flex)
    # Use regex to match ".A" followed by digit to catch current and future ARM shapes (A1, A4, A5, etc.)
    is_arm = bool(re.search(r'\.A\d', inst['type'])) or "Ampere" in inst['type'] or inst.get('arch') == 'arm64'
    op_sys = "Canonical Ubuntu"
    
    # OCI image search is tricky via CLI with just flags. Use list and grep/query.
    # Query for latest image matching OS and Version.
    # operating-system "Canonical Ubuntu", operating-system-version "22.04"
    
    # We strip patch version if present (e.g. 22.04.1 -> 22.04)
    os_ver_major = os_version.split('.')[0] + "." + os_version.split('.')[1]
    
    if logger:
        logger.info(f"Searching for {op_sys} {os_ver_major} image...")

    # Different shapes might need different images (Specialty limits), but generally Standard/Flex use standard images.
    # Note: OCI image listing can be slow.
    image_query = (
        f"oci compute image list --compartment-id {compartment_id} "
        f"--operating-system \"{op_sys}\" --operating-system-version \"{os_ver_major}\" "
        f"--shape {inst['type']} --sort-by TIMECREATED --sort-order DESC "
        f"--query 'data[0].id' --raw-output"
    )
    
    image_id = run_cmd(image_query, logger=logger)
    
    if not image_id or image_id == "None":
        # Fallback: try searching without shape filter but with architecture filter
        # For ARM shapes (A1, A4), we need aarch64 images
        if is_arm:
            # Search for aarch64 images specifically
            image_query_fallback = (
                f"oci compute image list --compartment-id {compartment_id} "
                f"--operating-system \"{op_sys}\" --operating-system-version \"{os_ver_major}\" "
                f"--sort-by TIMECREATED --sort-order DESC "
                f"--query \"data[?contains(\\\"display-name\\\", 'aarch64')] | [0].id\" --raw-output"
            )
        else:
            image_query_fallback = (
                f"oci compute image list --compartment-id {compartment_id} "
                f"--operating-system \"{op_sys}\" --operating-system-version \"{os_ver_major}\" "
                f"--sort-by TIMECREATED --sort-order DESC "
                f"--query 'data[0].id' --raw-output"
            )
        image_id = run_cmd(image_query_fallback, logger=logger)

    if not image_id or image_id == "None":
        if logger: logger.error(f"Could not find Ubuntu {os_version} image for {inst['type']}")
        return None, None
        
    if logger:
        logger.info(f"Using Image ID: {image_id}")
        logger.info(f"Using Availability Domain: {ad}")

    # 4. Prepare SSH Key
    # AWS/GCP logic usually assumes the key exists. OCI needs the public key CONTENT or FILE.
    key_path = Path(config['common']['ssh_key_path'])
    pub_key_path = key_path.with_suffix('.pub') # Assume .pub exists, or key_path IS .pub? Commonly key_path is private.
    
    # If key_path is private (no extension or .pem), try finding .pub
    if not pub_key_path.exists():
        # Maybe key_path is the public key? Unlikely for SSH execution.
        # Try appending .pub
        pub_key_path = Path(str(key_path) + ".pub")
        
    if not pub_key_path.exists():
        msg = f"Public key file not found at {pub_key_path} (needed for OCI)"
        if logger: logger.error(msg)
        else: print(f"[Error] {msg}")
        return None, None

    # 5. Launch Instance
    storage_config = build_storage_config(inst, 'oci')
    
    if logger:
        logger.info("Launching OCI instance...")
        
    launch_cmd = (
        f"oci compute instance launch "
        f"--compartment-id {compartment_id} "
        f"--availability-domain \"{ad}\" "
        f"--shape \"{inst['type']}\" "
        f"--subnet-id {subnet_id} "
        f"--image-id {image_id} "
        f"{storage_config}"
        f"--display-name \"{name}\" "
        f"--ssh-authorized-keys-file \"{pub_key_path}\" "
        f"--assign-public-ip true "
        f"--wait-for-state RUNNING "
        f"--query 'data.id' --raw-output"
    )
    
    # Run Launch
    # Note: OCI CLI wait-for-state blocks until running.
    instance_id = run_cmd(launch_cmd, logger=logger, timeout=600)
    
    if not instance_id or "opc-request-id" in instance_id: # Error output sometimes contains request id
        if logger: logger.error(f"Launch failed or returned invalid ID: {instance_id}")
        return None, None

    # 6. Get Public IP
    # Instance is running, but we need to fetch the IP explicitly involved in the VNIC.
    if logger:
        logger.info(f"Instance launched ({instance_id}), fetching IP...")
        
    # Get Primary VNIC attachment
    vnic_att_query = (
        f"oci compute vnic-attachment list --compartment-id {compartment_id} --instance-id {instance_id} "
        f"--query 'data[0].\"vnic-id\"' --raw-output"
    )
    vnic_id = run_cmd(vnic_att_query, logger=logger)
    
    if vnic_id and vnic_id != "None":
        ip_query = (
            f"oci network vnic get --vnic-id {vnic_id} "
            f"--query 'data.\"public-ip\"' --raw-output"
        )
        ip = run_cmd(ip_query, logger=logger)
        return instance_id, ip
    
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
            zone = zone or region
            status = run_cmd(
                f"gcloud compute instances describe {instance_id} --project={project} --zone={zone} "
                f"--format='get(status)'",
                capture=True, ignore=True, logger=logger
            )
            return status.strip() if status else "TERMINATED" # Assume terminated if verify fails

        elif cloud == 'oci':
            # OCI status: PROVISIONING, STAGING, RUNNING, STOPPING, STOPPED, TERMINATING, TERMINATED
            cmd_prefix = f"OCI_REGION={region} " if region else ""
            status = run_cmd(
                f"{cmd_prefix}oci compute instance get --instance-id {instance_id} --query 'data.\"lifecycle-state\"' --raw-output",
                capture=True, ignore=True, logger=logger
            )
            return status.strip() if status else "TERMINATED"

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
        elif False:
            log("Verifying SSH build status...")

        # Wait for delayed SSH restart to complete
        if logger:
            logger.info("Waiting 10 seconds for SSH service restart...")
        elif False:
            log("Waiting 10 seconds for SSH service restart...")
        time.sleep(10)

        # Check build status file
        status_cmd = f"ssh {ssh_opt} {ssh_user}@{ip} 'cat /tmp/ssh_build_status.txt 2>/dev/null || echo UNKNOWN'"
        status = run_cmd(status_cmd, capture=True, timeout=10, logger=logger)

        if status == "SUCCESS":
            if logger:
                logger.info("SSH build verified successfully")
            elif False:
                log("SSH build verified successfully", "INFO")
            #elif DEBUG_MODE == False:
                print("  [SSH Build] ✓ Verification successful")
            progress(instance_name, "SSH build verified", logger)
            return True
        else:
            if logger:
                logger.error(f"SSH build verification failed: {status}")
            elif False:
                log(f"SSH build verification failed: {status}", "ERROR")
            #elif DEBUG_MODE == False:
                print(f"  [SSH Build] ✗ Verification failed: {status}")
            progress(instance_name, f"SSH build failed: {status}", logger)

            # Attempt automatic rollback
            if auto_rollback and status == "FAILED":
                if logger:
                    logger.info("Attempting automatic rollback to previous SSH version...")
                elif False:
                    log("Attempting automatic rollback to previous SSH version...")
                #elif DEBUG_MODE == False:
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
                    elif False:
                        log("Rollback successful, SSH restored to previous version", "INFO")
                    #elif DEBUG_MODE == False:
                        print("  [SSH Build] ✓ Rollback successful")
                else:
                    if logger:
                        logger.error(f"Rollback failed: {rollback_result}")
                    elif False:
                        log(f"Rollback failed: {rollback_result}", "ERROR")
                    #elif DEBUG_MODE == False:
                        print(f"  [SSH Build] ✗ Rollback failed: {rollback_result}")

            return False

    except Exception as e:
        if logger:
            logger.error(f"SSH build verification error: {e}")
        elif False:
            log(f"SSH build verification error: {e}", "ERROR")
        #elif DEBUG_MODE == False:
            print(f"  [SSH Build] ✗ Verification error: {e}")
        return False


def run_ssh_commands(ip, config, inst, key_path, ssh_strict_host_key_checking, instance_name, logger=None):
    """Execute all commands via SSH sequentially with output displayed."""
    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_connect_timeout = config['common'].get('ssh_timeout', 20)
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk} -o UserKnownHostsFile=/dev/null -o ConnectTimeout={ssh_connect_timeout} -o ServerAliveInterval=300 -o ServerAliveCountMax=3 -o BatchMode=yes -o NumberOfPasswordPrompts=0"
    ssh_user = config['common']['ssh_user']
    # Each workload timeout (backward compatible with command_timeout)
    workload_timeout = config['common'].get('workload_timeout', config['common'].get('command_timeout', 10800))

    # -----------------------------------------------------------
    # Determine Command List (Testloads vs Workloads)
    # -----------------------------------------------------------
    # Priority:
    # 1. Instance-level testloads flag (inst.testloads)
    # 2. Global --test flag (config._testloads_mode)
    # 3. Default: run workloads
    workloads = []

    # Check if instance has explicit testloads setting
    if 'testloads' in inst:
        # Instance-level setting takes highest priority
        testloads_mode = inst['testloads']
    else:
        # Fall back to global --test flag, or default to False
        testloads_mode = config.get('_testloads_mode', False)

    if testloads_mode:
        if logger:
            logger.info(f"Testloads mode ENABLED for {instance_name}. Running ONLY testloads.")
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
        elif False:
            log("No workloads to execute", "WARNING")
        return False

    total_workloads = len(workloads)
    progress(instance_name, f"Workload execution started ({total_workloads} workloads)", logger)

    if logger:
        logger.info(f"Starting workload execution for {ip} ({total_workloads} workloads)")
    elif False:
        log(f"Starting workload execution for {ip} ({total_workloads} workloads)")
    #elif DEBUG_MODE == False:
        print(f"  [Workloads] Starting execution of {total_workloads} workloads...")

    for i, workload in enumerate(workloads, start=1):
        workload_start = time.time()
        # Format workload with vcpu substitution
        cmd = workload.format(vcpus=inst['vcpus'])

        if not cmd or cmd.strip() == "":
            continue

        if is_apt_setup_command(cmd):
            cmd = wrap_apt_command_with_retries(cmd)
            if logger:
                logger.info("Detected apt setup command, enabling lock wait/retry wrapper")
            else:
                print("  [INFO] Detected apt setup command, enabling lock wait/retry wrapper")

        progress(instance_name, f"Workload {i}/{total_workloads}", logger)

        if logger:
            logger.info(f"Workload {i}/{total_workloads}: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            logger.info(f"Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")
        elif False:
            log(f"Workload {i}/{total_workloads}: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            log(f"Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")
        #elif DEBUG_MODE == False:
            print(f"  [Workload {i}/{total_workloads}] Executing: {cmd[:80]}{'...' if len(cmd) > 80 else ''}")
            print(f"  [Workload {i}/{total_workloads}] Timeout: {workload_timeout}s ({workload_timeout//60} minutes)")

        # Detect long-running benchmark commands and run them via nohup
        long_running_indicators = config['common'].get('long_running_indicators', ['pts_regression.py', 'benchmark', 'phoronix-test-suite', 'pts_runner'])
        is_long_running = any(indicator in cmd for indicator in long_running_indicators)

        if is_long_running:
            # Run via nohup to survive SSH disconnections
            if logger:
                logger.info("Detected long-running command, using nohup for robustness")
            elif False:
                log("Detected long-running command, using nohup for robustness")
            #elif DEBUG_MODE == False:
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
                #elif DEBUG_MODE:
                    log("Timeout while starting background command, proceeding to verification...", "WARN")

            if logger:
                logger.info("Command started in background, waiting for completion...")
            elif False:
                log("Command started in background, waiting for completion...")
            #elif DEBUG_MODE == False:
                print(f"  [Workload {i}/{total_workloads}] Started in background, monitoring...")

            # Poll for completion with timeout
            start_time = time.time()
            check_count = 0
            last_log_size = 0
            ssh_fail_count = 0
            warned_90_percent = False  # Track if we've issued 90% warning

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
                        elif False:
                            log(f"SSH failed {ssh_fail_count} times, checking instance status...", "WARN")

                        status = get_instance_status(
                            cloud=inst.get('cloud'),
                            instance_id=inst.get('instance_id') or inst.get('name'),
                            region=inst.get('region'),
                            project=inst.get('project'),
                            zone=inst.get('region') or inst.get('zone'),
                            logger=logger
                        )
                        
                        if status in ['terminated', 'TERMINATED', 'stopped', 'STOPPING', 'shutting-down']:
                            msg = f"Instance {instance_name} terminated externally (Status: {status})"
                            if logger: 
                                logger.error(msg)
                            elif False:
                                log(msg, "ERROR")
                            
                            DASHBOARD.update(instance_name, status='TERMINATED')
                            duration = time.time() - workload_start
                            DASHBOARD.add_history(instance_name, f"Workload {i}/{total_workloads}: {cmd}", duration, "EXT_TERM")
                            return False
                        else:
                            if logger:
                                logger.warn(f"Instance status is {status}, continuing SSH retries...")
                            elif False:
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
                        elif False:
                            log(f"Progress: Log file size {current_log_size} bytes (+{current_log_size - last_log_size})")
                        last_log_size = current_log_size
                except:
                    pass

                if marker_check == "SUCCESS":
                    if logger:
                        logger.info(f"Workload {i}/{total_workloads} completed successfully")
                    elif False:
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
                    elif False:
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
                            f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -200 {workload_log_path} 2>/dev/null || echo \"No workload log found\"'",
                            capture=True, timeout=30, ignore=True, logger=logger
                        )
                        
                        if logger:
                            if workload_log and workload_log.strip() and "No workload log found" not in workload_log:
                                logger.info(f"Last 200 lines from {workload_log_path}:")
                                for line in workload_log.strip().split('\n'):
                                    logger.info(f"  {line}")
                        else:
                            if workload_log and workload_log.strip() and "No workload log found" not in workload_log:
                                print(f"  [Workload Log] Last 200 lines from {workload_log_path}:")
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
                    elif False:
                        log(f"Unexpected marker status: {marker_check}", "WARN")

                # Still running, continue polling
                elapsed = int(time.time() - start_time)

                # Check if we've reached 90% of timeout - issue warning and dump diagnostic info
                if not warned_90_percent and elapsed >= workload_timeout * 0.9:
                    warned_90_percent = True
                    if logger:
                        logger.warn(f"Workload {i}/{total_workloads} approaching timeout (90%: {elapsed}s / {workload_timeout}s)")
                        logger.warn("Collecting diagnostic information...")
                    else:
                        print(f"  [WARNING] Workload {i}/{total_workloads} approaching timeout (90%: {elapsed}s / {workload_timeout}s)")
                        print(f"  [WARNING] Collecting diagnostic information...")

                    # Dump diagnostic info at 90%
                    try:
                        # Get process tree
                        ps_output = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'ps auxf | head -100'",
                                           capture=True, ignore=True, timeout=30, logger=logger)
                        if logger and ps_output:
                            logger.warn(f"Process tree (top 100 processes):\n{ps_output}")
                        elif ps_output:
                            print(f"  [DIAG] Process tree (top 100 processes):\n{ps_output}")

                        # Get last 50 lines of log
                        log_tail = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -200 {remote_log_path} 2>/dev/null || echo \"[No log available]\"'",
                                          capture=True, ignore=True, timeout=30, logger=logger)
                        if logger and log_tail:
                            logger.warn(f"Last 200 lines of wrapper log ({remote_log_path}):\n{log_tail}")
                        elif log_tail:
                            print(f"  [DIAG] Last 200 lines of wrapper log:\n{log_tail}")

                        # Get last 50 lines of workload log if available
                        workload_log_match = re.search(r'>\s*(/tmp/[^\s]+\.log)', cmd)
                        if workload_log_match:
                            workload_log_path = workload_log_match.group(1)
                            wl_tail = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -200 {workload_log_path} 2>/dev/null || echo \"[No workload log]\"'",
                                             capture=True, ignore=True, timeout=30, logger=logger)
                            if logger and wl_tail:
                                logger.warn(f"Last 200 lines of workload log ({workload_log_path}):\n{wl_tail}")
                            elif wl_tail:
                                print(f"  [DIAG] Last 200 lines of workload log:\n{wl_tail}")

                        # Get memory/disk info
                        mem_info = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'free -h'",
                                          capture=True, ignore=True, timeout=30, logger=logger)
                        if logger and mem_info:
                            logger.warn(f"Memory status:\n{mem_info}")
                        elif mem_info:
                            print(f"  [DIAG] Memory status:\n{mem_info}")

                    except Exception as e:
                        if logger:
                            logger.warn(f"Failed to collect 90% diagnostic info: {e}")
                        else:
                            print(f"  [WARN] Failed to collect 90% diagnostic info: {e}")

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
                elif False:
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
                # Timeout reached - Collect final diagnostic information
                if logger:
                    logger.error(f"Workload {i}/{total_workloads} timed out after {workload_timeout}s")
                    logger.error("Collecting final diagnostic information...")
                else:
                    print(f"  [ERROR] Workload {i}/{total_workloads} timed out after {workload_timeout}s")
                    print(f"  [ERROR] Collecting final diagnostic information...")

                # Dump comprehensive diagnostic info at timeout
                try:
                    # Get full process tree
                    ps_output = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'ps auxf'",
                                       capture=True, ignore=True, timeout=30, logger=logger)
                    if logger and ps_output:
                        logger.error(f"Full process tree at timeout:\n{ps_output}")
                    elif ps_output:
                        print(f"  [TIMEOUT-DIAG] Full process tree:\n{ps_output}")

                    # Get last 100 lines of wrapper log
                    log_tail = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -100 {remote_log_path} 2>/dev/null || echo \"[No log available]\"'",
                                      capture=True, ignore=True, timeout=30, logger=logger)
                    if logger and log_tail:
                        logger.error(f"Last 100 lines of wrapper log ({remote_log_path}):\n{log_tail}")
                    elif log_tail:
                        print(f"  [TIMEOUT-DIAG] Last 100 lines of wrapper log:\n{log_tail}")

                    # Get last 100 lines of workload log if available
                    workload_log_match = re.search(r'>\s*(/tmp/[^\s]+\.log)', cmd)
                    if workload_log_match:
                        workload_log_path = workload_log_match.group(1)
                        wl_tail = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'tail -100 {workload_log_path} 2>/dev/null || echo \"[No workload log]\"'",
                                         capture=True, ignore=True, timeout=30, logger=logger)
                        if logger and wl_tail:
                            logger.error(f"Last 100 lines of workload log ({workload_log_path}):\n{wl_tail}")
                        elif wl_tail:
                            print(f"  [TIMEOUT-DIAG] Last 100 lines of workload log:\n{wl_tail}")

                    # Get memory and disk info
                    sys_info = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'free -h && echo \"===DISK===\" && df -h'",
                                      capture=True, ignore=True, timeout=30, logger=logger)
                    if logger and sys_info:
                        logger.error(f"System resources at timeout:\n{sys_info}")
                    elif sys_info:
                        print(f"  [TIMEOUT-DIAG] System resources:\n{sys_info}")

                    # Get running pts/python processes
                    pts_procs = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'ps aux | grep -E \"phoronix|python|pts_runner\" | grep -v grep'",
                                       capture=True, ignore=True, timeout=30, logger=logger)
                    if logger and pts_procs:
                        logger.error(f"PTS/Python processes at timeout:\n{pts_procs}")
                    elif pts_procs:
                        print(f"  [TIMEOUT-DIAG] PTS/Python processes:\n{pts_procs}")

                    # Try to get strace of any long-running process (if available)
                    strace_check = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'which strace'",
                                          capture=True, ignore=True, timeout=10, logger=logger)
                    if strace_check and strace_check.strip():
                        # Find the main python process PID
                        pid_check = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'pgrep -f pts_runner | head -1'",
                                           capture=True, ignore=True, timeout=10, logger=logger)
                        if pid_check and pid_check.strip():
                            main_pid = pid_check.strip()
                            # Get strace for 5 seconds to see what it's waiting on
                            strace_out = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'timeout 5 strace -p {main_pid} 2>&1 || true'",
                                               capture=True, ignore=True, timeout=10, logger=logger)
                            if logger and strace_out:
                                logger.error(f"strace of main process (PID {main_pid}):\n{strace_out}")
                            elif strace_out:
                                print(f"  [TIMEOUT-DIAG] strace of main process (PID {main_pid}):\n{strace_out}")

                except Exception as e:
                    if logger:
                        logger.warn(f"Failed to collect timeout diagnostic info: {e}")
                    else:
                        print(f"  [WARN] Failed to collect timeout diagnostic info: {e}")

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
                    elif False:
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

                    # Final timeout - collect diagnostic information
                    msg = f"Workload {i}/{total_workloads} timed out after {workload_timeout}s"
                    if logger:
                        logger.error(msg)
                        logger.error("Collecting diagnostic information for regular command timeout...")
                    else:
                        print(f"  [ERROR] {msg}")
                        print(f"  [ERROR] Collecting diagnostic information...")

                    # Dump diagnostic info for regular command timeout
                    try:
                        # Get process tree
                        ps_output = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'ps auxf | head -100'",
                                           capture=True, ignore=True, timeout=30, logger=logger)
                        if logger and ps_output:
                            logger.error(f"Process tree at timeout:\n{ps_output}")
                        elif ps_output:
                            print(f"  [TIMEOUT-DIAG] Process tree:\n{ps_output}")

                        # Get system info
                        sys_info = run_cmd(f"ssh {ssh_opt} {ssh_user}@{ip} 'free -h && echo \"===UPTIME===\" && uptime'",
                                          capture=True, ignore=True, timeout=30, logger=logger)
                        if logger and sys_info:
                            logger.error(f"System info at timeout:\n{sys_info}")
                        elif sys_info:
                            print(f"  [TIMEOUT-DIAG] System info:\n{sys_info}")

                    except Exception as e:
                        if logger:
                            logger.warn(f"Failed to collect diagnostic info: {e}")
                        else:
                            print(f"  [WARN] Failed to collect diagnostic info: {e}")

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
                    #elif DEBUG_MODE: log(msg, "ERROR")
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
                    elif False:
                        log("Detected SSH build execution, verifying build status...")
                    #elif DEBUG_MODE == False:
                        print("  [SSH Build] Verifying OpenSSH installation...")

                    if not verify_ssh_build(ip, ssh_opt, ssh_user, instance_name, logger=logger):
                        if logger:
                            logger.error("SSH build verification failed, aborting command execution")
                        elif False:
                            log("SSH build verification failed, aborting command execution", "ERROR")
                        #elif DEBUG_MODE == False:
                            print("  [Error] SSH build verification failed")
                        return False
                else:
                    # Status file doesn't exist - SSH build was not executed (skip verification)
                    if logger:
                        logger.info("SSH build script detected but no status file found (build may have been skipped)")
                    elif False:
                        log("SSH build script detected but no status file found (build may have been skipped)")
            except Exception as e:
                # Verification check failed, but continue (don't block on verification issues)
                if logger:
                    logger.warn(f"SSH build verification check failed: {e}, continuing...")
                elif False:
                    log(f"SSH build verification check failed: {e}, continuing...", "WARNING")

    progress(instance_name, "All workloads completed", logger)

    if logger:
        logger.info("All workloads completed successfully")
    elif False:
        log("All workloads completed successfully")

    return True


def collect_results(ip, config, cloud, name, inst, key_path, ssh_strict_host_key_checking, instance_name, logger=None):
    """Collect benchmark results from remote instance."""
    progress(instance_name, "Collecting results", logger)

    if logger:
        logger.info(f"Collecting results from {ip}")
    elif False:
        log(f"Collecting results from {ip}")

    strict_hk = "yes" if ssh_strict_host_key_checking else "no"
    ssh_opt = f"-i {key_path} -o StrictHostKeyChecking={strict_hk} -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=60 -o ServerAliveCountMax=10 -o BatchMode=yes -o NumberOfPasswordPrompts=0"
    ssh_user = config['common']['ssh_user']
    cloud_rep_dir = config['common']['cloud_reports_dir']

    if logger:
        logger.info("Creating tarball on remote instance...")
    elif False:
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
    elif False:
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
    elif False:
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
    DASHBOARD.register(instance_name, cloud.upper(), inst['type'], cpu_cost=cpu_cost, storage_cost=storage_cost, region=inst.get('region'))
    
    # Inject cloud provider into instance dict for downstream use
    inst['cloud'] = cloud
# =========================================================================================
# CSP PROVIDER IMPLEMENTATIONS
# =========================================================================================

class AWSProvider(CloudProvider):
    """AWS-specific implementation of CloudProvider."""

    def initialize_shared_resources(self, logger=None) -> Dict[str, Any]:
        """Initialize AWS shared resources (Security Group, KeyPair)."""
        region = self.csp_config['region']
        sg_name = self.config['common']['security_group_name']

        # Per-region shared resources (for multi-region support)
        self._region_resources = {}
        self._region_lock = threading.Lock()

        # Setup Security Group
        sg_id = setup_aws_sg(region, sg_name, logger)

        # Get KeyPair name
        key_name = run_cmd(
            f"aws ec2 describe-key-pairs --region {region} --query 'KeyPairs[0].KeyName' --output text",
            logger=logger
        )
        if not key_name or key_name == "None":
            msg = f"AWS key pair not found in region {region}. Create a key pair before launching instances."
            if logger:
                logger.error(msg)
            raise ValueError(msg)

        self._region_resources[region] = {
            'sg_id': sg_id,
            'key_name': key_name
        }

        self.shared_resources = {
            'sg_id': sg_id,
            'key_name': key_name,
            'region': region,
            'sg_name': sg_name
        }

        return self.shared_resources

    def _get_region_for_instance(self, inst: Dict[str, Any]) -> str:
        """Resolve region for an instance (instance override > CSP default)."""
        return inst.get('region') or self.shared_resources.get('region') or self.csp_config.get('region')

    def _get_region_resources(self, region: str, logger=None) -> Dict[str, Any]:
        """Get or initialize region-specific AWS resources."""
        resources = self._region_resources.get(region)
        if resources:
            return resources

        with self._region_lock:
            resources = self._region_resources.get(region)
            if resources:
                return resources

            sg_name = self.shared_resources.get('sg_name') or self.config['common']['security_group_name']
            sg_id = setup_aws_sg(region, sg_name, logger)
            key_name = run_cmd(
                f"aws ec2 describe-key-pairs --region {region} --query 'KeyPairs[0].KeyName' --output text",
                logger=logger
            )
            if not key_name or key_name == "None":
                msg = f"AWS key pair not found in region {region}. Create a key pair before launching instances."
                if logger:
                    logger.error(msg)
                raise ValueError(msg)
            resources = {'sg_id': sg_id, 'key_name': key_name}
            self._region_resources[region] = resources

        return resources

    def validate_instance_name(self, name: str) -> None:
        """AWS has no name constraints (uses tags)."""
        pass  # No validation needed

    def check_instance_exists(self, instance_name: str, inst: Optional[Dict[str, Any]] = None, logger=None) -> bool:
        """Check if AWS instance with Name tag exists."""
        region = self._get_region_for_instance(inst or {})

        result = run_cmd(
            f"aws ec2 describe-instances --region {region} "
            f"--filters 'Name=tag:Name,Values={instance_name}' "
            f"'Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down' "
            f"--query 'Reservations[*].Instances[*].InstanceId' "
            f"--output text",
            capture=True,
            ignore=True,
            logger=logger
        )

        if result and result.strip() and result != "None":
            if logger:
                logger.warn(f"Found existing AWS instance(s) with name '{instance_name}': {result.strip()}")
            return True

        return False

    def is_rate_limit_error(self, exception: Exception) -> bool:
        """Check if exception is AWS rate limit error."""
        error_msg = str(exception)
        return any(kw in error_msg for kw in [
            'RequestLimitExceeded',
            'Throttling',
            'Rate exceeded'
        ])

    def is_retryable_error(self, exception: Exception) -> bool:
        """Check if exception is retryable for AWS."""
        if self.is_rate_limit_error(exception):
            return True

        error_msg = str(exception)
        return any(kw in error_msg for kw in [
            'InternalError',
            'ServiceUnavailable',
            'RequestTimeout',
            'Connection reset'
        ])

    def get_recommended_max_workers(self) -> int:
        """AWS recommended parallelism: 3."""
        return 3

    def get_launch_delay_between_instances(self) -> float:
        """AWS launch delay: 0.5 seconds."""
        return 0.5

    def launch_instance(self, inst: Dict[str, Any], logger=None) -> Tuple[Optional[str], Optional[str]]:
        """Launch AWS instance."""
        region = self._get_region_for_instance(inst)
        resources = self._get_region_resources(region, logger)
        return launch_aws_instance(
            inst,
            self.config,
            region,
            resources['key_name'],
            resources['sg_id'],
            logger
        )

    def terminate_instance(self, instance_id: str, inst: Dict[str, Any], logger=None) -> bool:
        """Terminate AWS instance."""
        region = self._get_region_for_instance(inst)
        try:
            run_cmd(
                f"aws ec2 terminate-instances --region {region} --instance-ids {instance_id}",
                timeout=600,
                logger=logger
            )
            return True
        except Exception as e:
            if logger:
                logger.error(f"Termination failed: {e}")
            return False

    def get_instance_status(self, instance_id: str, inst: Dict[str, Any], logger=None) -> str:
        """Get AWS instance status."""
        region = self._get_region_for_instance(inst)
        try:
            status = run_cmd(
                f"aws ec2 describe-instances --region {region} --instance-ids {instance_id} "
                f"--query 'Reservations[0].Instances[0].State.Name' --output text",
                capture=True,
                ignore=True,
                logger=logger
            )
            return status.strip() if status else "unknown"
        except:
            return "unknown"


class GCPProvider(CloudProvider):
    """GCP-specific implementation of CloudProvider."""

    def initialize_shared_resources(self, logger=None) -> Dict[str, Any]:
        """Initialize GCP shared resources (Project, Region-as-Zone)."""
        project = get_gcp_project(logger)
        region = self.csp_config.get('region') or self.csp_config.get('zone') or 'us-central1-a'

        self.shared_resources = {
            'project': project,
            # Default region-as-zone (can be overridden per instance)
            'region': region,
            'zone': region
        }

        return self.shared_resources

    def _get_zone_for_instance(self, inst: Dict[str, Any]) -> str:
        """Resolve region-as-zone for an instance (instance override > CSP default > fallback)."""
        return (
            inst.get('region')
            or inst.get('zone')
            or self.shared_resources.get('region')
            or self.shared_resources.get('zone')
            or self.csp_config.get('region')
            or self.csp_config.get('zone')
            or 'us-central1-a'
        )

    def validate_instance_name(self, name: str) -> None:
        """Validate GCP instance name (1-63 chars, lowercase, starts with letter)."""
        if len(name) > 63:
            raise ValueError(f"GCP instance name exceeds 63 chars: {name}")
        if not re.match(r'^[a-z]([a-z0-9-]*[a-z0-9])?$', name):
            raise ValueError(f"Invalid GCP name format: {name}")

    def check_instance_exists(self, instance_name: str, inst: Optional[Dict[str, Any]] = None, logger=None) -> bool:
        """Check if GCP instance exists."""
        project = self.shared_resources['project']
        zone = self._get_zone_for_instance(inst or {})

        result = run_cmd(
            f"gcloud compute instances describe {instance_name} "
            f"--project={project} --zone={zone} "
            f"--format='get(name)'",
            capture=True,
            ignore=True,
            logger=logger
        )

        if result and result.strip() == instance_name:
            if logger:
                logger.error(f"GCP instance '{instance_name}' already exists in {zone}")
            return True

        return False

    def is_rate_limit_error(self, exception: Exception) -> bool:
        """Check if exception is GCP rate limit error."""
        error_msg = str(exception)
        return any(kw in error_msg for kw in [
            'Quota exceeded',
            'Rate Limit Exceeded',
            'rateLimitExceeded',
            'quotaExceeded'
        ])

    def is_retryable_error(self, exception: Exception) -> bool:
        """Check if exception is retryable for GCP."""
        if self.is_rate_limit_error(exception):
            return True

        error_msg = str(exception)
        return any(kw in error_msg for kw in [
            'INTERNAL',
            'UNAVAILABLE',
            'backendError'
        ])

    def get_recommended_max_workers(self) -> int:
        """GCP recommended parallelism: 5."""
        return 5

    def get_launch_delay_between_instances(self) -> float:
        """GCP launch delay: 0.2 seconds."""
        return 0.2

    def launch_instance(self, inst: Dict[str, Any], logger=None) -> Tuple[Optional[str], Optional[str]]:
        """Launch GCP instance."""
        return launch_gcp_instance(
            inst,
            self.config,
            self.shared_resources['project'],
            self._get_zone_for_instance(inst),
            logger
        )

    def terminate_instance(self, instance_id: str, inst: Dict[str, Any], logger=None) -> bool:
        """Terminate GCP instance."""
        project = self.shared_resources['project']
        zone = self._get_zone_for_instance(inst)
        # Note: GCP uses instance name, not ID
        name = inst.get('name', instance_id)
        try:
            run_cmd(
                f"gcloud compute instances delete {name} --project={project} --zone={zone} --quiet",
                timeout=600,
                logger=logger
            )
            return True
        except Exception as e:
            if logger:
                logger.error(f"Termination failed: {e}")
            return False

    def get_instance_status(self, instance_id: str, inst: Dict[str, Any], logger=None) -> str:
        """Get GCP instance status."""
        project = self.shared_resources['project']
        zone = self._get_zone_for_instance(inst)
        name = inst.get('name', instance_id)
        try:
            status = run_cmd(
                f"gcloud compute instances describe {name} --project={project} --zone={zone} "
                f"--format='get(status)'",
                capture=True,
                ignore=True,
                logger=logger
            )
            return status.strip().lower() if status else "unknown"
        except:
            return "unknown"


class OCIProvider(CloudProvider):
    """OCI-specific implementation of CloudProvider."""

    # OCI supported Ubuntu versions
    # Update this list when OCI adds support for new Ubuntu versions
    OCI_SUPPORTED_UBUNTU_VERSIONS = ['22.04', '24.04']

    def _normalize_ubuntu_version_for_oci(self, os_version: str, logger=None) -> str:
        """
        Normalize Ubuntu version to OCI-supported version.

        OCI currently only supports Ubuntu 22.04 and 24.04.
        - Ubuntu 25+ will fall back to 24.04
        - Ubuntu 23 will fall back to 22.04
        - Ubuntu 22 and below will fall back to 22.04

        Args:
            os_version: Original OS version (e.g., "25.04", "23.10", "22.04")
            logger: Optional logger for warnings

        Returns:
            Normalized version supported by OCI (e.g., "24.04" or "22.04")
        """
        try:
            # Extract major.minor version
            version_parts = os_version.split('.')
            if len(version_parts) < 2:
                if logger:
                    logger.warn(f"Invalid OS version format: {os_version}, defaulting to 24.04")
                return '24.04'

            major = int(version_parts[0])
            minor = int(version_parts[1])
            os_ver_major_minor = f"{major}.{minor:02d}"

            # Check if already supported
            if os_ver_major_minor in self.OCI_SUPPORTED_UBUNTU_VERSIONS:
                return os_ver_major_minor

            # Apply fallback logic
            if major >= 25:
                # Ubuntu 25+ -> 24.04
                normalized = '24.04'
                if logger:
                    logger.warn(
                        f"OCI does not support Ubuntu {os_ver_major_minor}. "
                        f"Falling back to Ubuntu {normalized}."
                    )
            else:
                # Ubuntu 23 and below -> 22.04
                normalized = '22.04'
                if logger:
                    logger.warn(
                        f"OCI does not support Ubuntu {os_ver_major_minor}. "
                        f"Falling back to Ubuntu {normalized}."
                    )

            return normalized

        except (ValueError, IndexError) as e:
            if logger:
                logger.warn(f"Error parsing OS version {os_version}: {e}, defaulting to 24.04")
            return '24.04'

    def initialize_shared_resources(self, logger=None) -> Dict[str, Any]:
        """Initialize OCI shared resources (Compartment, Subnet, AD) from environment variables."""
        import os

        # Priority: environment variables > config file (except region: config file > env var)
        # This allows for secure credential management without hardcoding
        compartment_id = os.getenv('OCI_COMPARTMENT_ID') or self.csp_config.get('compartment_id')
        subnet_id = os.getenv('OCI_SUBNET_ID') or self.csp_config.get('subnet_id')
        availability_domain = os.getenv('OCI_AVAILABILITY_DOMAIN') or self.csp_config.get('availability_domain')
        region = self.csp_config.get('region') or os.getenv('OCI_REGION') or 'us-ashburn-1'

        # Validate required parameters
        if not compartment_id:
            raise ValueError(
                "OCI compartment_id not found. Set OCI_COMPARTMENT_ID environment variable or "
                "specify compartment_id in cloud_instances.json"
            )

        if logger:
            logger.info(f"OCI Configuration:")
            logger.info(f"  Region: {region}")
            logger.info(f"  Compartment ID: {compartment_id[:20]}...")
            if subnet_id:
                logger.info(f"  Subnet ID: {subnet_id[:20]}...")
            if availability_domain:
                logger.info(f"  Availability Domain: {availability_domain}")

        self.shared_resources = {
            'compartment_id': compartment_id,
            'subnet_id': subnet_id,
            'availability_domain': availability_domain,
            'region': region
        }

        return self.shared_resources

    def _get_region_for_instance(self, inst: Dict[str, Any]) -> str:
        """Resolve OCI region (instance override > CSP default > env > fallback)."""
        import os
        return (
            inst.get('region')
            or self.shared_resources.get('region')
            or self.csp_config.get('region')
            or os.getenv('OCI_REGION')
            or 'us-ashburn-1'
        )

    def _get_compartment_id_for_instance(self, inst: Dict[str, Any]) -> str:
        """Resolve compartment OCID (instance override > shared)."""
        return inst.get('compartment_id') or self.shared_resources.get('compartment_id')

    def _get_subnet_id_for_instance(self, inst: Dict[str, Any]) -> Optional[str]:
        """Resolve subnet OCID (instance override > shared)."""
        return inst.get('subnet_id') or self.shared_resources.get('subnet_id')

    def _get_availability_domain_for_instance(self, inst: Dict[str, Any]) -> Optional[str]:
        """Resolve availability domain (instance override > shared)."""
        return inst.get('availability_domain') or self.shared_resources.get('availability_domain')

    def _oci_cmd_prefix(self, inst: Dict[str, Any]) -> str:
        """Prefix to force OCI region per command."""
        region = self._get_region_for_instance(inst)
        return f"OCI_REGION={region} " if region else ""

    def _find_existing_instance(self, inst: Dict[str, Any], logger=None) -> Optional[Dict[str, str]]:
        """Find existing instance by display-name in any non-terminated state."""
        name = inst.get('name')
        if not name:
            return None

        compartment_id = self._get_compartment_id_for_instance(inst)
        cmd_prefix = self._oci_cmd_prefix(inst)

        query = (
            f"{cmd_prefix}oci compute instance list "
            f"--compartment-id {compartment_id} "
            f"--query 'data[?\"display-name\"==\"{name}\"] | [0].{{id:id,state:\"lifecycle-state\"}}' "
            f"--output json"
        )
        result = run_cmd(query, capture=True, ignore=True, logger=logger)
        if not result:
            return None
        try:
            data = json.loads(result)
            if isinstance(data, dict) and data.get("id"):
                state = data.get("state", "UNKNOWN")
                if state != "TERMINATED":
                    return {"id": data["id"], "state": state}
        except Exception:
            return None
        return None

    def _wait_for_instance_running(self, inst: Dict[str, Any], instance_id: str, logger=None, timeout=600) -> bool:
        """Wait until instance reaches RUNNING or timeout."""
        cmd_prefix = self._oci_cmd_prefix(inst)
        start = time.time()
        while time.time() - start < timeout:
            status = run_cmd(
                f"{cmd_prefix}oci compute instance get --instance-id {instance_id} "
                f"--query 'data.\"lifecycle-state\"' --raw-output",
                capture=True,
                ignore=True,
                logger=logger
            )
            state = status.strip().upper() if status else "UNKNOWN"
            if state == "RUNNING":
                return True
            if state == "TERMINATED":
                return False
            time.sleep(10)
        return False

    def _get_instance_ip(self, inst: Dict[str, Any], instance_id: str, logger=None) -> Optional[str]:
        """Fetch public IP for an existing instance."""
        cmd_prefix = self._oci_cmd_prefix(inst)
        vnic_att_query = (
            f"{cmd_prefix}oci compute vnic-attachment list --compartment-id {self._get_compartment_id_for_instance(inst)} "
            f"--instance-id {instance_id} --query 'data[0].\"vnic-id\"' --raw-output"
        )
        vnic_id = run_cmd(vnic_att_query, logger=logger)
        if vnic_id and vnic_id != "None":
            ip_query = (
                f"{cmd_prefix}oci network vnic get --vnic-id {vnic_id} "
                f"--query 'data.\"public-ip\"' --raw-output"
            )
            return run_cmd(ip_query, logger=logger)
        return None

    def validate_instance_name(self, name: str) -> None:
        """Validate OCI instance name (1-255 chars)."""
        if len(name) > 255:
            raise ValueError(f"OCI instance name exceeds 255 chars: {name}")
        if not re.match(r'^[a-zA-Z0-9._-]+$', name):
            raise ValueError(f"Invalid OCI name format: {name}")

    def check_instance_exists(self, instance_name: str, inst: Optional[Dict[str, Any]] = None, logger=None) -> bool:
        """Check if OCI instance exists."""
        inst = inst or {}
        existing = self._find_existing_instance(inst, logger=logger)
        if existing:
            if logger:
                logger.warn(f"Found existing OCI instance with name '{instance_name}' (state={existing['state']})")
            return True
        return False

    def is_rate_limit_error(self, exception: Exception) -> bool:
        """Check if exception is OCI rate limit error."""
        error_msg = str(exception)
        return any(kw in error_msg for kw in [
            'TooManyRequests',
            '429',
            'Rate limit'
        ])

    def is_retryable_error(self, exception: Exception) -> bool:
        """Check if exception is retryable for OCI."""
        if self.is_rate_limit_error(exception):
            return True

        error_msg = str(exception)
        return any(kw in error_msg for kw in [
            'InternalServerError',
            'ServiceUnavailable',
            '500',
            '503'
        ])

    def get_recommended_max_workers(self) -> int:
        """OCI recommended parallelism: 2."""
        return 2

    def get_launch_delay_between_instances(self) -> float:
        """OCI launch delay: 1.0 seconds."""
        return 1.0

    def launch_instance(self, inst: Dict[str, Any], logger=None) -> Tuple[Optional[str], Optional[str]]:
        """Launch OCI instance and return (instance_id, ip)."""
        region = self._get_region_for_instance(inst)
        if logger:
            logger.info(f"OCI target region: {region}")
        if not region:
            msg = "OCI region is not set; aborting launch to prevent cross-region creation."
            if logger:
                logger.error(msg)
            return None, None

        compartment_id = self._get_compartment_id_for_instance(inst)
        cmd_prefix = self._oci_cmd_prefix(inst)
        name = inst['name']

        if logger:
            logger.info(f"Launching OCI instance: {name} ({inst['type']})")

        # If instance already exists in non-terminated state, do not create a new one
        existing = self._find_existing_instance(inst, logger=logger)
        if existing:
            if logger:
                logger.warn(f"Existing OCI instance found (state={existing['state']}), skipping create.")
            if existing["state"].upper() != "RUNNING":
                if logger:
                    logger.info("Waiting for existing instance to reach RUNNING...")
                if not self._wait_for_instance_running(inst, existing["id"], logger=logger):
                    return None, None
            ip = self._get_instance_ip(inst, existing["id"], logger=logger)
            return existing["id"], ip

        # 1. Find Subnet ID (from config or auto-detect public subnet)
        subnet_id = self._get_subnet_id_for_instance(inst)

        if not subnet_id:
            if logger:
                logger.info("Subnet ID not configured, auto-detecting public subnet in compartment...")

            # Find VCNs first
            vcns_json = run_cmd(
                f"{cmd_prefix}oci network vcn list --compartment-id {compartment_id} --query 'data[0].id' --raw-output",
                logger=logger
            )
            if vcns_json and vcns_json != "None":
                # List subnets in the VCN (we want a public subnet)
                subnet_cmd = (
                    f"{cmd_prefix}oci network subnet list --compartment-id {compartment_id} --vcn-id {vcns_json} "
                    f"--query 'data[?\"prohibit-public-ip-on-vnic\"==`false`] | [0].id' --raw-output"
                )
                subnet_id = run_cmd(subnet_cmd, logger=logger)

        if not subnet_id or subnet_id == "None":
            msg = "Failed to detect a valid public subnet. Please specify 'subnet_id' in cloud_instances.json or create a public subnet."
            if logger:
                logger.error(msg)
            return None, None

        if logger:
            logger.info(f"Using Subnet ID: {subnet_id}")

        # 2. Find Availability Domain (pick first one)
        ad = self._get_availability_domain_for_instance(inst)
        if not ad:
            ad = run_cmd(
                f"{cmd_prefix}oci iam availability-domain list --compartment-id {compartment_id} --query 'data[0].name' --raw-output",
                logger=logger
            )
        if not ad:
            if logger:
                logger.error("Failed to get Availability Domain")
            return None, None

        # 3. Find Ubuntu Image
        os_version = self.config['common']['os_version']

        # Normalize Ubuntu version for OCI (OCI-specific fallback)
        os_ver_major = self._normalize_ubuntu_version_for_oci(os_version, logger)
        op_sys = "Canonical Ubuntu"

        if logger:
            if os_version.startswith(os_ver_major):
                logger.info(f"Searching for {op_sys} {os_ver_major} image...")
            else:
                logger.info(f"Searching for {op_sys} {os_ver_major} image (normalized from {os_version})...")

        image_query = (
            f"{cmd_prefix}oci compute image list --compartment-id {compartment_id} "
            f"--operating-system \"{op_sys}\" --operating-system-version \"{os_ver_major}\" "
            f"--shape {inst['type']} --sort-by TIMECREATED --sort-order DESC "
            f"--query 'data[0].id' --raw-output"
        )

        image_id = run_cmd(image_query, logger=logger)

        if not image_id or image_id == "None":
            # Fallback: try searching without shape filter
            image_query_fallback = (
                f"{cmd_prefix}oci compute image list --compartment-id {compartment_id} "
                f"--operating-system \"{op_sys}\" --operating-system-version \"{os_ver_major}\" "
                f"--sort-by TIMECREATED --sort-order DESC "
                f"--query 'data[0].id' --raw-output"
            )
            image_id = run_cmd(image_query_fallback, logger=logger)

        if not image_id or image_id == "None":
            if logger:
                logger.error(f"Could not find Ubuntu {os_ver_major} image for {inst['type']}")
            return None, None

        if logger:
            logger.info(f"Using Image ID: {image_id}")
            logger.info(f"Using Availability Domain: {ad}")

        # 4. Prepare SSH Key
        from pathlib import Path
        key_path = Path(self.config['common']['ssh_key_path'])
        pub_key_path = key_path.with_suffix('.pub')

        if not pub_key_path.exists():
            pub_key_path = Path(str(key_path) + ".pub")

        if not pub_key_path.exists():
            msg = f"Public key file not found at {pub_key_path} (needed for OCI)"
            if logger:
                logger.error(msg)
            return None, None

        # 5. Build shape-specific configuration
        shape_config = ""
        if "Flex" in inst['type']:
            # Flex shapes require OCPU and memory configuration
            ocpus = inst.get('ocpus', 1)
            memory_gb = inst.get('memory_gb', ocpus * 8)  # Default: 8GB per OCPU
            shape_config = f"--shape-config '{{\"ocpus\":{ocpus},\"memoryInGBs\":{memory_gb}}}' "

        # 6. Launch Instance
        storage_config = build_storage_config(inst, 'oci')

        if logger:
            logger.info("Launching OCI instance...")

        launch_cmd = (
            f"{cmd_prefix}oci compute instance launch "
            f"--compartment-id {compartment_id} "
            f"--availability-domain \"{ad}\" "
            f"--shape \"{inst['type']}\" "
            f"{shape_config}"
            f"--subnet-id {subnet_id} "
            f"--image-id {image_id} "
            f"{storage_config}"
            f"--display-name \"{name}\" "
            f"--ssh-authorized-keys-file \"{pub_key_path}\" "
            f"--assign-public-ip true "
            f"--wait-for-state RUNNING "
            f"--query 'data.id' --raw-output"
        )

        # Run Launch (OCI CLI wait-for-state blocks until running)
        instance_id = run_cmd(launch_cmd, logger=logger, timeout=600)

        if not instance_id or "opc-request-id" in instance_id:
            if logger:
                logger.error(f"Launch failed or returned invalid ID: {instance_id}")
            # Re-check existing instance by name before giving up (retry-safe)
            existing = self._find_existing_instance(inst, logger=logger)
            if existing:
                if existing["state"].upper() != "RUNNING":
                    if not self._wait_for_instance_running(inst, existing["id"], logger=logger):
                        return None, None
                ip = self._get_instance_ip(inst, existing["id"], logger=logger)
                return existing["id"], ip
            return None, None

        # 7. Get Public IP
        if logger:
            logger.info(f"Instance launched ({instance_id}), fetching IP...")

        ip = self._get_instance_ip(inst, instance_id, logger=logger)
        if ip:
            if logger:
                logger.info(f"Instance ready: {name} @ {ip}")
            return instance_id, ip

        return None, None

    def terminate_instance(self, instance_id: str, inst: Dict[str, Any], logger=None) -> bool:
        """Terminate OCI instance."""
        cmd_prefix = self._oci_cmd_prefix(inst)
        try:
            run_cmd(
                f"{cmd_prefix}oci compute instance terminate --instance-id {instance_id} --force",
                timeout=600,
                logger=logger
            )
            return True
        except Exception as e:
            if logger:
                logger.error(f"Termination failed: {e}")
            return False

    def get_instance_status(self, instance_id: str, inst: Dict[str, Any], logger=None) -> str:
        """Get OCI instance status."""
        cmd_prefix = self._oci_cmd_prefix(inst)
        try:
            status = run_cmd(
                f"{cmd_prefix}oci compute instance get --instance-id {instance_id} "
                f"--query 'data.\"lifecycle-state\"' --raw-output",
                capture=True,
                ignore=True,
                logger=logger
            )
            return status.strip().lower() if status else "unknown"
        except:
            return "unknown"


# =========================================================================================
# MAIN EXECUTION LOGIC
# =========================================================================================

def cleanup_instance_safely(provider: CloudProvider, instance_id: str, inst: Dict[str, Any], logger):
    """
    Safely cleanup instance with retry (for use in finally blocks).

    Args:
        provider: CloudProvider instance
        instance_id: Instance ID to terminate
        inst: Instance definition
        logger: Logger for output
    """
    try:
        retry_with_exponential_backoff(
            lambda: provider.terminate_instance(instance_id, inst, logger),
            max_retries=3,
            base_delay=2.0,
            logger=logger,
            error_classifier=provider.is_retryable_error
        )
        logger.info(f"Instance {inst['name']} terminated successfully")
        DASHBOARD.update(f"{provider.csp_config.get('name', 'unknown').upper()}:{inst['name']}", status="TERMINATED")
    except Exception as term_error:
        logger.error(f"Failed to terminate instance after retries: {term_error}")

        # Display manual cleanup command
        manual_cmd = get_manual_cleanup_command(
            provider.csp_config.get('name', 'unknown'),
            instance_id,
            inst['name'],
            provider.shared_resources,
            region=inst.get('region'),
            project=provider.shared_resources.get('project'),
            zone=inst.get('region') or inst.get('zone')
        )
        logger.error(f"MANUAL CLEANUP REQUIRED:")
        logger.error(f"  {manual_cmd}")

        DASHBOARD.update(
            f"{provider.csp_config.get('name', 'unknown').upper()}:{inst['name']}",
            status="TERM_FAILED",
            color=DASHBOARD.FAIL
        )
    finally:
        unregister_instance(instance_id)


def process_instance(
    provider: CloudProvider,
    inst: Dict[str, Any],
    config: Dict[str, Any],
    key_path: str,
    log_dir: Path
) -> None:
    """
    Process a single instance: launch, run workloads, collect results, terminate.

    Args:
        provider: CloudProvider instance
        inst: Instance definition from cloud_instances.json
        config: Full cloud_config.json
        key_path: SSH private key path
        log_dir: Log directory
    """
    # Sanitize instance name
    original_name = inst['name']
    sanitized_name = sanitize_instance_name(original_name)
    inst['name'] = sanitized_name

    # Validate name against CSP constraints
    provider.validate_instance_name(sanitized_name)

    # Create instance display name
    csp_name = provider.csp_config.get('name', 'unknown').upper()
    instance_name = f"{csp_name}:{sanitized_name}"

    # Initialize logger
    logger = InstanceLogger(instance_name, DASHBOARD, log_dir)

    # Check for name conflicts
    if check_instance_name_conflict(provider, sanitized_name, inst, logger):
        logger.error(f"Instance name conflict detected. Skipping {sanitized_name}")
        DASHBOARD.update(instance_name, status="ERROR", step="Name conflict", color=DASHBOARD.FAIL)
        return

    if isinstance(provider, AWSProvider):
        inst['region'] = provider._get_region_for_instance(inst)
    elif isinstance(provider, GCPProvider):
        inst['region'] = provider._get_zone_for_instance(inst)
    elif isinstance(provider, OCIProvider):
        inst['region'] = provider._get_region_for_instance(inst)

    # Register instance on dashboard
    cpu_cost = inst.get('cpu_cost_hour[730h-mo]', 0.0)
    storage_cost = inst.get('extra_150g_storage_cost_hour', 0.0) if inst.get('extra_150g_storage') else 0.0
    DASHBOARD.register(instance_name, csp_name, inst['type'], cpu_cost, storage_cost, region=inst.get('region'))

    logger.info(f"Processing instance: {sanitized_name}")

    instance_id = None
    ip = None
    commands_success = False

    try:
        # Launch instance
        progress(instance_name, "Launching instance", logger)

        instance_id, ip = retry_with_exponential_backoff(
            lambda: provider.launch_instance(inst, logger),
            max_retries=5,
            base_delay=2.0,
            logger=logger,
            error_classifier=provider.is_retryable_error
        )

        if not ip or ip == "None":
            logger.error(f"Failed to get IP for {sanitized_name}")
            if not instance_id:
                DASHBOARD.remove(instance_name)
            return

        inst['instance_id'] = instance_id

        # Register for cleanup
        register_instance(
            cloud=provider.csp_config.get('name', 'unknown'),
            instance_id=instance_id,
            name=sanitized_name,
            region=inst.get('region') or provider.shared_resources.get('region'),
            project=provider.shared_resources.get('project'),
            zone=inst.get('region') or provider.shared_resources.get('region')
        )

        progress(instance_name, f"Instance launched (IP: {ip})", logger)

        # Wait for SSH
        logger.info(f"Waiting 60s for SSH (IP: {ip})...")
        time.sleep(60)

        # Set hostname if specified
        if 'hostname' in inst and inst['hostname']:
            hostname = inst['hostname']
            logger.info(f"Setting hostname to: {hostname}")
            ssh_strict = config['common'].get('ssh_strict_host_key_checking', False)
            try:
                run_cmd(
                    f"ssh {'-o StrictHostKeyChecking=no' if not ssh_strict else ''} "
                    f"-i {key_path} ubuntu@{ip} 'sudo hostnamectl set-hostname {hostname}'",
                    timeout=30,
                    logger=logger
                )
            except Exception as e:
                logger.warn(f"Failed to set hostname: {e}")

        # Run workloads
        try:
            ssh_strict = config['common'].get('ssh_strict_host_key_checking', False)
            commands_success = run_ssh_commands(ip, config, inst, key_path, ssh_strict, instance_name, logger)
        except Exception as workload_error:
            logger.error(f"Workload execution failed: {workload_error}")
            commands_success = False

        # Collect results
        try:
            collect_results(ip, config, provider.csp_config.get('name', 'unknown'), sanitized_name, inst, key_path, ssh_strict, instance_name, logger)
        except Exception as collect_error:
            logger.error(f"Result collection failed: {collect_error}")

        # Update status
        if commands_success:
            DASHBOARD.update(instance_name, status="COMPLETED")
        else:
            DASHBOARD.update(instance_name, status="COMPLETED", color=DASHBOARD.WARNING)

    except Exception as e:
        logger.error(f"Instance processing failed: {e}")
        import traceback
        logger.error(traceback.format_exc(), fatal=False)
        if not instance_id:
            DASHBOARD.remove(instance_name)
        DASHBOARD.update(instance_name, status="ERROR", color=DASHBOARD.FAIL)

    finally:
        # Guaranteed cleanup
        if instance_id:
            progress(instance_name, "Terminating instance", logger)
            cleanup_instance_safely(provider, instance_id, inst, logger)


def execute_instances_parallel(
    provider: CloudProvider,
    instances: List[Dict[str, Any]],
    config: Dict[str, Any],
    key_path: str,
    log_dir: Path,
    max_workers: int
) -> None:
    """
    Execute instances in parallel with rate limiting.

    Args:
        provider: CloudProvider instance
        instances: List of instance definitions (enable=true only)
        config: Full cloud_config.json
        key_path: SSH private key path
        log_dir: Log directory
        max_workers: Number of parallel workers
    """
    launch_delay = provider.get_launch_delay_between_instances()

    def process_with_delay(inst):
        """Process instance with rate limiting delay."""
        time.sleep(launch_delay)
        return process_instance(provider, inst, config, key_path, log_dir)

    executor = None

    try:
        executor = ThreadPoolExecutor(max_workers=max_workers)

        futures = {
            executor.submit(process_with_delay, inst): inst
            for inst in instances
        }

        for future in as_completed(futures):
            inst = futures[future]
            try:
                future.result()
            except Exception as exc:
                # Log to general errors file
                error_log = log_dir / "general_errors.log"
                with open(error_log, "a") as f:
                    f.write(f"[{datetime.now()}] {inst['name']} failed: {exc}\n")
                    import traceback
                    f.write(traceback.format_exc() + "\n")

    except KeyboardInterrupt:
        print("\n[INTERRUPT] KeyboardInterrupt detected in main thread")

    finally:
        if executor:
            print("[SHUTDOWN] Waiting for active threads to complete cleanup...")
            executor.shutdown(wait=True, cancel_futures=False)
            print("[SHUTDOWN] All threads finished.")


def validate_instance_definitions(instances_def: Dict[str, Any], csp_filter: Optional[str] = None) -> None:
    """
    Validate cloud_instances.json for duplicate names and other conflicts.

    Args:
        instances_def: Instance definitions from cloud_instances.json
        csp_filter: Optional CSP name to validate (if None, validates all CSPs)

    Raises:
        ValueError: If validation fails with detailed error message
    """
    errors = []
    warnings = []

    # Determine which CSPs to validate
    csps_to_check = [csp_filter] if csp_filter else instances_def.keys()

    for csp in csps_to_check:
        if csp not in instances_def:
            continue

        csp_config = instances_def[csp]
        if not isinstance(csp_config, dict):
            continue

        instances, enabled_regions = collect_instances_for_csp(csp_config)
        if csp_config.get('enable', False) and not enabled_regions:
            warnings.append(
                f"[{csp.upper()}] WARNING: CSP enabled but no enabled regions found."
            )
        if not instances:
            continue

        # Get only enabled instances for validation
        enabled_instances = [inst for inst in instances if inst.get('enable', False)]

        if not enabled_instances:
            continue

        # Check for duplicate names (CRITICAL)
        names = [inst.get('name', '') for inst in enabled_instances]
        name_counts = {}
        for name in names:
            if name:
                name_counts[name] = name_counts.get(name, 0) + 1

        duplicates = {name: count for name, count in name_counts.items() if count > 1}
        if duplicates:
            errors.append(
                f"[{csp.upper()}] CRITICAL: Duplicate instance names found!\n"
                + "\n".join([f"  - '{name}' appears {count} times" for name, count in duplicates.items()])
                + "\n  Instance names must be unique within a CSP to avoid conflicts."
            )

        # Check for duplicate hostnames (WARNING - may be intentional for same machine type)
        hostnames = [inst.get('hostname', '') for inst in enabled_instances]
        hostname_counts = {}
        for hostname in hostnames:
            if hostname:
                hostname_counts[hostname] = hostname_counts.get(hostname, 0) + 1

        duplicate_hostnames = {hn: count for hn, count in hostname_counts.items() if count > 1}
        if duplicate_hostnames:
            warnings.append(
                f"[{csp.upper()}] WARNING: Duplicate hostnames found:\n"
                + "\n".join([f"  - '{hn}' appears {count} times" for hn, count in duplicate_hostnames.items()])
                + "\n  This may be intentional if using same machine type with different configs."
            )

        # Check required fields
        required_fields = ['name', 'type', 'enable']
        for i, inst in enumerate(enabled_instances, 1):
            missing = [field for field in required_fields if field not in inst or not inst[field]]
            if missing:
                errors.append(
                    f"[{csp.upper()}] Instance #{i} is missing required fields: {', '.join(missing)}"
                )

        # CSP-specific validations
        if csp == 'oci':
            for i, inst in enumerate(enabled_instances, 1):
                # OCI Flex shapes require ocpus and memory_gb
                if 'Flex' in inst.get('type', ''):
                    if 'ocpus' not in inst:
                        errors.append(
                            f"[{csp.upper()}] Instance '{inst.get('name', f'#{i}')}': "
                            f"Flex shape requires 'ocpus' field"
                        )
                    if 'memory_gb' not in inst:
                        errors.append(
                            f"[{csp.upper()}] Instance '{inst.get('name', f'#{i}')}': "
                            f"Flex shape requires 'memory_gb' field"
                        )

    # Print warnings
    if warnings:
        print(f"\n{'='*80}")
        print("VALIDATION WARNINGS:")
        print(f"{'='*80}")
        for warning in warnings:
            print(warning)
        print(f"{'='*80}\n")

    # Raise errors if any
    if errors:
        error_msg = f"\n{'='*80}\n"
        error_msg += "VALIDATION FAILED - Configuration Errors Found:\n"
        error_msg += f"{'='*80}\n"
        error_msg += "\n\n".join(errors)
        error_msg += f"\n{'='*80}\n"
        error_msg += "\nPlease fix these errors in cloud_instances.json before running.\n"
        raise ValueError(error_msg)


def collect_instances_for_csp(csp_config: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Collect instances for a CSP with region expansion.

    Returns:
        (instances, enabled_regions)
    """
    instances: List[Dict[str, Any]] = []
    enabled_regions: List[str] = []

    regions = csp_config.get('regions')
    if isinstance(regions, dict):
        for region_name, region_cfg in regions.items():
            if not isinstance(region_cfg, dict):
                continue
            if not region_cfg.get('enable', False):
                continue
            enabled_regions.append(region_name)
            for inst in region_cfg.get('instances', []):
                if not isinstance(inst, dict):
                    continue
                if not inst.get('region'):
                    inst['region'] = region_name
                instances.append(inst)
    else:
        default_region = csp_config.get('region') or csp_config.get('zone')
        if default_region:
            enabled_regions.append(default_region)
        for inst in csp_config.get('instances', []):
            if not isinstance(inst, dict):
                continue
            if default_region and not inst.get('region'):
                inst['region'] = default_region
            instances.append(inst)

    return instances, enabled_regions


def order_instances_by_region(instances: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Reorder instances to enforce at least one instance per region first.

    Returns:
        (ordered_instances, regions)
    """
    region_map: Dict[str, List[Dict[str, Any]]] = {}
    for inst in instances:
        region = inst.get('region') or 'unknown-region'
        region_map.setdefault(region, []).append(inst)

    regions = list(region_map.keys())
    ordered: List[Dict[str, Any]] = []

    # First pass: one per region
    for region in regions:
        bucket = region_map[region]
        if bucket:
            ordered.append(bucket.pop(0))

    # Round-robin the rest to keep distribution
    remaining = True
    while remaining:
        remaining = False
        for region in regions:
            bucket = region_map[region]
            if bucket:
                ordered.append(bucket.pop(0))
                remaining = True

    return ordered, regions


def load_config(config_path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Load cloud_config.json and cloud_instances.json.

    Args:
        config_path: Path to cloud_config.json

    Returns:
        (config, instances) tuple
    """
    with open(config_path, 'r') as f:
        config = json.load(f)

    # Expand environment variables in paths
    for key in ['ssh_key_path', 'cloud_reports_dir', 'host_reports_dir']:
        if key in config['common']:
            config['common'][key] = os.path.expandvars(config['common'][key])

    # Load instance definitions
    inst_def_file = config.get('instance_definitions_file', 'cloud_instances.json')
    inst_def_path = Path(config_path).parent / inst_def_file

    with open(inst_def_path, 'r') as f:
        instances = json.load(f)

    return config, instances


def main():
    """Main entry point for cloud_exec_para.py with pre-flight checks."""
    global DASHBOARD

    # 1. SYNTAX CHECK FIRST (before anything else)
    if not verify_syntax():
        print("[ERROR] Syntax errors detected. Aborting execution.")
        print("[ERROR] Please fix syntax errors before launching instances.")
        sys.exit(1)

    # 1.5 JSON SYNTAX CHECK
    if not verify_json_files('cloud_config.json'):
        print("[ERROR] JSON syntax errors detected. Aborting execution.")
        sys.exit(1)

    # Optional: Check pts_runner scripts
    pts_errors = verify_pts_runner_syntax()
    if pts_errors:
        print(f"[WARN] Syntax errors found in {len(pts_errors)} pts_runner script(s):")
        for err in pts_errors[:5]:  # Show first 5 errors
            print(f"  - {err}")
        if len(pts_errors) > 5:
            print(f"  ... and {len(pts_errors) - 5} more")
        print("[WARN] Execution will continue, but workloads may fail.\n")

    # 2. Register signal handlers
    signal.signal(signal.SIGINT, cleanup_active_instances)
    signal.signal(signal.SIGTERM, cleanup_active_instances)

    # 3. Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Cloud Executor with Parallel Execution (within same CSP)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./cloud_exec_para.py --csp aws                    # Run AWS instances only
  ./cloud_exec_para.py --csp gcp --max-workers 3    # Run GCP with custom parallelism
  ./cloud_exec_para.py --csp oci --dry-run          # Show execution plan only
  ./cloud_exec_para.py --csp aws --test             # Run testloads only (quick verification)
        """
    )

    parser.add_argument('--csp', required=True, choices=['aws', 'gcp', 'oci'],
                        help='CSP to execute (required)')
    parser.add_argument('--config', default='cloud_config.json',
                        help='Path to cloud_config.json (default: cloud_config.json)')
    parser.add_argument('--max-workers', type=int,
                        help='Override recommended max_workers for parallelism')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show execution plan without launching instances')
    parser.add_argument('--test', action='store_true',
                        help='Run testloads instead of workloads (quick verification)')

    args = parser.parse_args()

    # Load configuration
    try:
        config, instances_def = load_config(args.config)
    except Exception as e:
        print(f"[ERROR] Failed to load configuration: {e}")
        sys.exit(1)

    # Validate instance definitions BEFORE execution
    try:
        validate_instance_definitions(instances_def, csp_filter=args.csp)
    except ValueError as e:
        print(str(e))
        sys.exit(1)

    # Add testloads mode flag to config
    config['_testloads_mode'] = args.test

    # Get CSP-specific config
    csp_config = instances_def.get(args.csp)
    if not csp_config:
        print(f"[ERROR] CSP '{args.csp}' not found in instance definitions")
        sys.exit(1)

    if not csp_config.get('enable', False):
        print(f"[WARN] CSP '{args.csp}' is disabled in configuration")
        sys.exit(0)

    # Expand region-based definitions
    instances, enabled_regions = collect_instances_for_csp(csp_config)
    if enabled_regions and not csp_config.get('region'):
        csp_config['region'] = enabled_regions[0]
        csp_config['zone'] = enabled_regions[0]

    # Filter enabled instances
    instances = [inst for inst in instances if inst.get('enable', False)]
    instances, regions = order_instances_by_region(instances)

    if not instances:
        print(f"[WARN] No enabled instances found for {args.csp}")
        sys.exit(0)

    # Set CSP name in config for provider access
    csp_config['name'] = args.csp

    # Create CloudProvider instance
    provider_map = {
        'aws': AWSProvider,
        'gcp': GCPProvider,
        'oci': OCIProvider
    }

    provider = provider_map[args.csp](config, csp_config)

    # Determine max_workers
    if args.max_workers:
        max_workers = min(args.max_workers, 10)  # Safety cap
    else:
        max_workers = provider.get_recommended_max_workers()

    # Dry-run mode
    if args.dry_run:
        print(f"\n{'='*80}")
        mode_str = "TESTLOADS MODE" if args.test else "PRODUCTION MODE"
        print(f"DRY RUN - {mode_str} - Execution Plan for {args.csp.upper()}")
        print(f"{'='*80}")
        print(f"Max Workers: {max_workers}")
        print(f"Launch Delay: {provider.get_launch_delay_between_instances()}s")
        if regions:
            print(f"Regions (ordered): {', '.join(regions)}")
            if max_workers < len(regions):
                print(f"[WARN] max_workers ({max_workers}) < regions ({len(regions)}): "
                      f"cannot start one per region concurrently.")
        if args.test:
            print(f"Mode: Testloads only (quick verification)")
        print(f"\nInstances to execute ({len(instances)}):")
        for i, inst in enumerate(instances, 1):
            testloads = " [testloads]" if inst.get('testloads') or args.test else ""
            region = inst.get('region') or "unknown-region"
            print(f"  {i}. {inst['name']} ({inst['type']}) @ {region}{testloads}")
        print(f"{'='*80}\n")
        sys.exit(0)

    # Setup log directory (include CSP name to avoid collision when running multiple CSPs simultaneously)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_dir = Path(config['common']['host_reports_dir']) / 'logs' / f"{timestamp}_{args.csp}"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Initialize Dashboard
    DASHBOARD = Dashboard(enabled=True)
    DASHBOARD.set_log_dir(log_dir)
    DASHBOARD.start()

    print(f"\n{'='*80}")
    print(f"CLOUD BENCHMARKING EXECUTOR - {args.csp.upper()} (Parallel Mode)")
    print(f"{'='*80}")
    print(f"Instances: {len(instances)}")
    print(f"Max Workers: {max_workers}")
    print(f"Log Directory: {log_dir}")
    print(f"{'='*80}\n")

    try:
        # Initialize shared resources
        print(f"[{args.csp.upper()}] Initializing shared resources...")
        provider.initialize_shared_resources()
        print(f"[{args.csp.upper()}] Shared resources initialized\n")

        # Get SSH key path
        key_path = config['common']['ssh_key_path']

        # Execute instances in parallel
        execute_instances_parallel(provider, instances, config, key_path, log_dir, max_workers)

    except Exception as e:
        print(f"\n[ERROR] Execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        # Stop dashboard
        if DASHBOARD:
            DASHBOARD.stop()

    print(f"\n{'='*80}")
    print(f"EXECUTION COMPLETED")
    print(f"Logs saved to: {log_dir}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
