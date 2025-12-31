#!/usr/bin/env python3
"""
Phoronix Test Suite Benchmark Runner

This script runs PTS benchmarks with proper thread and CPU affinity control.
It automatically detects execution mode based on thread arguments.
"""

import argparse
import os
import sys
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile
import signal
import time
import shutil


class PTSBenchmarkRunner:
    def __init__(self, benchmark: str, threads: int | None = None):
        self.benchmark = benchmark
        self.benchmark_full = f"pts/{benchmark}"
        self.requested_threads = threads

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.config_dir = self.project_root / "user_config"
        self.config_file = self.config_dir / "user-config.xml"

        # Benchmark info
        self.benchmark_name = benchmark.split('-')[0]
        self.benchmark_config_name = self.benchmark_full.replace('/', '_')

        # System info
        self.available_cores = os.cpu_count() or 1

        # Execution parameters
        self.thread_start = 1
        self.thread_end = self.available_cores
        self.mode = None

    def validate_config(self):
        """Validate required configuration files exist."""
        repo_test_config = self.config_dir / "test-options" / f"{self.benchmark_config_name}.config"

        if not repo_test_config.exists():
            print(f"[ERROR] Test-specific config file not found: {repo_test_config}")
            print("[ERROR] All benchmarks must have a corresponding XML config file in user_config/test-options/")
            sys.exit(1)

        print(f"[OK] Test-specific config file found: {repo_test_config}")

        if not self.config_file.exists():
            print(f"[ERROR] Config file not found: {self.config_file}")
            sys.exit(1)

        print(f"[OK] Config file found: {self.config_file}")

    def determine_execution_mode(self):
        """
        Determine execution mode based on thread argument value ONLY.

        Mode 1: Null argument -> Runtime thread control, test from 1 to vCPU
        Mode 2: threads >= vCPU -> Compile-time mode, run once with all vCPUs
        Mode 3: 1 <= threads < vCPU -> Runtime thread control, run with N threads on N CPUs
        """
        print(">>> Determining execution mode based on thread argument...")
        print(f"[INFO] Benchmark: {self.benchmark_full}")

        if self.requested_threads is None:
            # Mode 1: No argument -> Runtime mode, test all thread counts
            self.mode = "RUNTIME SCALING"
            print(f"[INFO] Mode: {self.mode} (no thread argument)")
            print(f"[INFO] Will test from 1 to {self.available_cores} threads")
            self.thread_start = 1
            self.thread_end = self.available_cores

        elif self.requested_threads >= self.available_cores:
            # Mode 2: Compile-time mode (threads >= vCPU)
            self.mode = "COMPILE-TIME"
            print(f"[INFO] Mode: {self.mode} (threads={self.requested_threads} >= vCPU={self.available_cores})")
            print(f"[INFO] Will run once with all {self.available_cores} CPUs")
            self.thread_start = self.available_cores
            self.thread_end = self.available_cores

        else:
            # Mode 3: Runtime mode with fixed thread count (1 <= threads < vCPU)
            self.mode = "RUNTIME FIXED"
            print(f"[INFO] Mode: {self.mode} (threads={self.requested_threads} < vCPU={self.available_cores})")
            print(f"[INFO] Will test with {self.requested_threads} thread(s) on {self.requested_threads} CPU(s)")
            self.thread_start = self.requested_threads
            self.thread_end = self.requested_threads

    def get_cpu_affinity_list(self, threads: int) -> str:
        """
        Generate CPU affinity list optimized for performance.

        Prioritizes physical cores (even IDs) first, then logical cores (odd IDs).
        Example for 4-core system:
            threads=1 -> "0"
            threads=2 -> "0,2"
            threads=3 -> "0,2,4" or "0,2,1" (depending on total cores)
            threads=4 -> "0,2,4,6" or "0,2,1,3"
        """
        nproc_total = self.available_cores
        half_cores = nproc_total // 2
        cpu_list = []

        if threads <= half_cores:
            # Physical cores only (even IDs): 0,2,4,...
            cpu_list = [str(i * 2) for i in range(threads)]
        else:
            # Physical cores + logical cores
            # Add all even IDs first
            cpu_list = [str(i * 2) for i in range(half_cores)]
            # Add odd IDs as needed
            logical_cores = threads - half_cores
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_cores)])

        return ','.join(cpu_list)

    def set_cpu_governor_performance(self):
        """Set CPU scaling governor to performance mode."""
        print(">>> Setting CPU scaling governor to performance...")

        try:
            # Try using cpupower
            result = subprocess.run(
                ['sudo', 'cpupower', 'frequency-set', '-g', 'performance'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print("[OK] CPU governor set to performance using cpupower")
                return True
        except FileNotFoundError:
            pass

        # Try direct sysfs write
        try:
            gov_files = list(Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor'))
            if gov_files:
                for gov_file in gov_files:
                    subprocess.run(['sudo', 'tee', str(gov_file)], input=b'performance',
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[OK] CPU governor set to performance for {len(gov_files)} cores")
                return True
        except Exception:
            pass

        print("[WARN] Could not set CPU governor to performance")
        print("[WARN] Install 'cpupower' (linux-tools-common) or run with sudo for better performance")
        return False

    def verify_batch_mode(self):
        """Verify PTS batch mode is configured."""
        print(">>> Verifying batch mode configuration...")

        try:
            tree = ET.parse(self.config_file)
            root = tree.getroot()
            configured = root.find('.//BatchMode/Configured')

            if configured is not None and configured.text == 'TRUE':
                print(f"[OK] Batch mode is configured in {self.config_file}")
                return True
            else:
                print("[ERROR] Batch mode is not configured in user-config.xml")
                print(f"Please ensure <Configured>TRUE</Configured> is set in {self.config_file}")
                sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Failed to parse config file: {e}")
            sys.exit(1)

    def clean_test_cache(self):
        """
        Clean all PTS cache for the current test to ensure fresh installation.

        This removes:
        1. Test profile cache (~/.phoronix-test-suite/test-profiles/pts/{test}/)
        2. Installed test binaries (~/.phoronix-test-suite/installed-tests/pts/{test}/)

        Download cache (~/.phoronix-test-suite/download-cache/) is preserved
        to avoid re-downloading source files.
        """
        print(">>> Cleaning test cache to ensure fresh installation...")

        pts_home = Path.home() / '.phoronix-test-suite'
        test_name = self.benchmark  # e.g., "nginx-3.0.1"

        # Directories to clean
        test_profile_dir = pts_home / 'test-profiles' / 'pts' / test_name
        installed_test_dir = pts_home / 'installed-tests' / 'pts' / test_name

        removed_count = 0

        # Remove test profile cache
        if test_profile_dir.exists():
            print(f"  [CLEAN] Removing test profile cache: {test_profile_dir}")
            shutil.rmtree(test_profile_dir)
            removed_count += 1

        # Remove installed test binaries
        if installed_test_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_test_dir}")
            shutil.rmtree(installed_test_dir)
            removed_count += 1

        if removed_count == 0:
            print("  [INFO] No cached files found (clean state)")
        else:
            print(f"  [OK] Cleaned {removed_count} cache location(s)")

        # Preserve download cache
        download_cache_dir = pts_home / 'download-cache'
        if download_cache_dir.exists():
            cache_files = list(download_cache_dir.glob('*'))
            if cache_files:
                print(f"  [INFO] Preserved download cache: {len(cache_files)} file(s)")

    def force_install_test(self):
        """Force rebuild test with current compiler settings."""
        print(">>> Forcing rebuild with current compiler settings...")

        # Clean all test cache before installation
        # This ensures user_config/test-profiles override is always used
        self.clean_test_cache()

        # Re-override test-profile to ensure it's used during force-install
        # (PTS may have downloaded a fresh copy)
        self.override_test_profile()

        # Load compiler environment if available
        compiler_env_script = self.script_dir / "setup_compiler_env.sh"
        if compiler_env_script.exists():
            print(">>> Loading compiler environment settings...")
            # Note: Environment variables from setup_compiler_env.sh should be sourced
            # before running this Python script

        cflags = os.environ.get('CFLAGS', 'default')
        cxxflags = os.environ.get('CXXFLAGS', 'default')
        cc = os.environ.get('CC', 'gcc')

        print(f"[INFO] Using compiler: {cc} with CFLAGS: {cflags}")
        print(f"[INFO] Using CXXFLAGS: {cxxflags}")

        env = os.environ.copy()
        env['PTS_USER_PATH_OVERRIDE'] = str(self.config_dir)

        # Set additional CFLAGS variants for benchmarks that use them
        if 'CFLAGS' in env:
            env['XCFLAGS'] = env['CFLAGS']
            env['EXTRA_CFLAGS'] = env['CFLAGS']
            env['FLAGS'] = env['CFLAGS']
            env['FLAGSFULL'] = env['CFLAGS']
            env['CFLAGS_FULL'] = env['CFLAGS']

        if 'CXXFLAGS' in env:
            env['EXTRA_CXXFLAGS'] = env['CXXFLAGS']

        # First, remove any existing installation
        # This ensures install.sh is executed fresh, not just recompiled
        print(f"[INFO] Removing existing installation of {self.benchmark_full}...")
        subprocess.run(
            ['bash', '-c', f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Now do a fresh install (not force-install, which skips install.sh execution)
        print(f"[INFO] Performing fresh installation of {self.benchmark_full}...")
        subprocess.run(
            ['phoronix-test-suite', 'install', self.benchmark_full],
            env=env
        )

    def merge_xml_configs(self, base_path: Path, override_path: Path) -> tuple[ET.Element, str]:
        """Merge base and test-specific XML configurations."""
        def merge_elements(base_elem, override_elem):
            """Recursively merge override_elem into base_elem."""
            base_children = {child.tag: child for child in base_elem}
            for override_child in override_elem:
                if override_child.tag in base_children:
                    base_child = base_children[override_child.tag]
                    if len(override_child) > 0:
                        merge_elements(base_child, override_child)
                    else:
                        base_child.text = override_child.text
                        base_child.attrib.update(override_child.attrib)
                else:
                    base_elem.append(override_child)

        # Parse both configs
        base_tree = ET.parse(base_path)
        base_root = base_tree.getroot()
        test_tree = ET.parse(override_path)
        test_root = test_tree.getroot()

        # Merge
        merge_elements(base_root, test_root)

        # Extract test option
        test_option = "1"
        for test_opts in test_root.findall('.//TestOptions/Test'):
            opt = test_opts.find('Option')
            if opt is not None:
                test_option = opt.text
                break

        return base_tree, test_option

    def run_benchmark_for_threads(self, threads: int) -> bool:
        """Run benchmark with specified thread count."""
        print(f"\n>>> Running with {threads} threads")

        # Get CPU affinity
        cpu_list = self.get_cpu_affinity_list(threads)
        print(f">>> CPU affinity: {cpu_list}")

        # Prepare results directory
        machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        results_base_dir = self.config_dir.parent / "reports"
        benchmark_results_dir = results_base_dir / machine_name / self.benchmark_name
        benchmark_results_dir.mkdir(parents=True, exist_ok=True)

        # Capture PRE-RUN CPU frequency
        self.capture_cpu_frequency(threads, 'PRE-RUN')

        # Merge configs
        repo_test_config = self.config_dir / "test-options" / f"{self.benchmark_config_name}.config"
        pts_user_config = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        pts_user_config.parent.mkdir(parents=True, exist_ok=True)

        print("[INFO] Merging base config with test-specific config...")
        merged_tree, test_option = self.merge_xml_configs(self.config_file, repo_test_config)
        merged_tree.write(pts_user_config, encoding='utf-8', xml_declaration=True)
        print(f"[OK] Merged config written to {pts_user_config}")
        print(f"[INFO] Test option: {test_option}")

        # Create named pipe for input
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.fifo') as tmp:
            input_fifo = tmp.name
        os.unlink(input_fifo)
        os.mkfifo(input_fifo)

        # Background process to feed responses
        def feed_responses():
            try:
                with open(input_fifo, 'w') as f:
                    f.write(f"{test_option}\n")
                    for _ in range(100):
                        f.write("n\n")
                        time.sleep(0.1)
            except:
                pass

        import threading
        feeder_thread = threading.Thread(target=feed_responses, daemon=True)
        feeder_thread.start()

        # Run benchmark
        env = os.environ.copy()
        env.update({
            'TEST_RESULTS_NAME': f"{self.benchmark}-{threads}threads",
            'TEST_RESULTS_IDENTIFIER': f"{self.benchmark}-{threads}threads",
            'TEST_RESULTS_DESCRIPTION': f"Benchmark with {threads} thread(s)",
            'PTS_USER_PATH_OVERRIDE': str(self.config_dir),
            'NUM_CPU_CORES': str(threads),
            'SKIP_ALL_TEST_OPTION_CHECKS': '1',
            'SKIP_TEST_OPTION_HANDLING': '1',
            'AUTO_UPLOAD_RESULTS_TO_OPENBENCHMARKING': 'FALSE',
            'DISPLAY_COMPACT_RESULTS': '1',
            'SKIP_TEST_RESULT_PARSE': '1',
            'SKIP_ALL_PROMPTS': '1',
            'NO_COLOR': '1',
            'PHP_ERROR_REPORTING': '0',
        })

        try:
            with open(input_fifo, 'r') as stdin_file:
                result = subprocess.run(
                    ['taskset', '-c', cpu_list, 'phoronix-test-suite', 'benchmark', self.benchmark_full],
                    stdin=stdin_file,
                    env=env,
                    capture_output=False,
                    text=True
                )

            # Capture POST-RUN CPU frequency before checking results
            self.capture_cpu_frequency(threads, 'POST-RUN')

            # Enhanced error detection
            success = self.verify_test_execution(threads, result.returncode)

            return success

        finally:
            # Cleanup
            try:
                os.unlink(input_fifo)
            except:
                pass

    def verify_test_execution(self, threads: int, pts_returncode: int) -> bool:
        """
        Verify that the test actually ran and produced results.

        Checks:
        1. PTS command succeeded (returncode 0)
        2. Test exit status (~/test-exit-status file)
        3. Result directory was created
        4. Result file (composite.xml) exists and is non-empty

        Returns:
            bool: True if test succeeded and produced results, False otherwise
        """
        print(f">>> Verifying test execution for {threads} thread(s)...")

        # Check 1: PTS command returncode
        if pts_returncode != 0:
            print(f"  [ERROR] PTS command failed with return code {pts_returncode}")
            return False
        print(f"  [OK] PTS command completed (returncode={pts_returncode})")

        # Check 2: Test exit status file
        test_status_file = Path.home() / "test-exit-status"
        if test_status_file.exists():
            try:
                test_status = int(test_status_file.read_text().strip())
                if test_status != 0:
                    print(f"  [ERROR] Test execution failed with exit status {test_status}")
                    print(f"  [INFO] This usually means the test binary failed to run")
                    print(f"  [INFO] Check if test was properly installed")
                    return False
                print(f"  [OK] Test exit status: {test_status}")
            except (ValueError, IOError) as e:
                print(f"  [WARN] Could not read test-exit-status file: {e}")
        else:
            print(f"  [WARN] Test exit status file not found: {test_status_file}")

        # Check 3: Result directory exists
        pts_results_dir = Path.home() / '.phoronix-test-suite' / 'test-results'
        result_pattern = f"{self.benchmark_name}-*-{threads}threads"
        matching_dirs = sorted(pts_results_dir.glob(result_pattern),
                              key=lambda p: p.stat().st_mtime,
                              reverse=True)

        if not matching_dirs:
            print(f"  [ERROR] No result directory found matching pattern: {result_pattern}")
            print(f"  [INFO] Search path: {pts_results_dir}")
            print(f"  [INFO] This means PTS did not save any benchmark results")
            print(f"  [INFO] Possible causes:")
            print(f"         - Test binary is missing or failed to execute")
            print(f"         - Test ran but produced no measurable output")
            print(f"         - PTS configuration issue preventing result save")
            return False

        result_dir = matching_dirs[0]
        print(f"  [OK] Result directory found: {result_dir.name}")

        # Check 4: Result file exists and is non-empty
        composite_xml = result_dir / 'composite.xml'
        if not composite_xml.exists():
            print(f"  [ERROR] Result file missing: {composite_xml}")
            print(f"  [INFO] Result directory exists but contains no composite.xml")
            return False

        file_size = composite_xml.stat().st_size
        if file_size == 0:
            print(f"  [ERROR] Result file is empty: {composite_xml}")
            return False

        print(f"  [OK] Result file exists: composite.xml ({file_size} bytes)")
        print(f"[OK] Test with {threads} threads completed successfully and produced results")
        return True

    def capture_cpu_frequency(self, threads: int, stage: str):
        """Capture CPU frequency snapshot (PRE-RUN or POST-RUN)."""
        machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        results_base_dir = self.config_dir.parent / "reports"
        benchmark_results_dir = results_base_dir / machine_name / self.benchmark_name
        freq_file = benchmark_results_dir / f"{self.benchmark}-{threads}threads-cpufreq.txt"

        mode = 'w' if stage == 'PRE-RUN' else 'a'

        try:
            with open(freq_file, mode) as f:
                f.write(f"=== {stage} SNAPSHOT ===\n")
                f.write(f"timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")

                # lscpu output
                try:
                    lscpu = subprocess.run(['lscpu'], capture_output=True, text=True)
                    if lscpu.returncode == 0:
                        f.write(lscpu.stdout)
                except:
                    pass

                # CPU MHz from /proc/cpuinfo
                try:
                    with open('/proc/cpuinfo', 'r') as cpuinfo:
                        for line in cpuinfo:
                            if 'cpu MHz' in line:
                                f.write(line)
                except:
                    pass

                # Current frequency from sysfs
                freq_files = list(Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_cur_freq'))
                for freq_path in sorted(freq_files):
                    try:
                        cpu_idx = freq_path.parent.parent.name.replace('cpu', '')
                        with open(freq_path, 'r') as freq:
                            val = freq.read().strip()
                            f.write(f"cpu{cpu_idx}: {val} kHz\n")
                    except:
                        pass

                f.write("\n")
        except Exception as e:
            print(f"[WARN] Could not capture CPU frequency: {e}")

    def export_results(self):
        """Export and organize benchmark results."""
        print(">>> Exporting and organizing results...")

        machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        results_base_dir = self.config_dir.parent / "reports"
        benchmark_results_dir = results_base_dir / machine_name / self.benchmark_name

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for threads in range(self.thread_start, self.thread_end + 1):
            result_identifier = f"{self.benchmark}-{threads}threads"

            # Find latest result directory matching pattern
            pattern = f"{self.benchmark_name}-*-{threads}threads"
            matching_dirs = sorted(pts_results_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

            if not matching_dirs:
                print(f"  [WARN] Result directory not found for {threads} thread(s) (pattern: {pattern})")
                continue

            result_dir = matching_dirs[0]
            print(f"  Processing results for {threads} thread(s): {result_dir.name}")

            # Copy composite.xml
            composite_xml = result_dir / "composite.xml"
            if composite_xml.exists():
                import shutil
                shutil.copy(composite_xml, benchmark_results_dir / f"{result_identifier}.xml")
                print(f"    Saved: {result_identifier}.xml")

            # Export to CSV using PTS command
            try:
                csv_output = subprocess.run(
                    ['phoronix-test-suite', 'result-file-to-csv', result_dir.name],
                    capture_output=True,
                    text=True
                )
                if csv_output.returncode == 0:
                    csv_file = benchmark_results_dir / f"{result_identifier}.csv"
                    csv_file.write_text(csv_output.stdout)
                    print(f"    Exported: {result_identifier}.csv")
                else:
                    # Fallback: Parse XML directly
                    self._export_csv_from_xml(composite_xml, benchmark_results_dir / f"{result_identifier}.csv")
            except Exception as e:
                print(f"    [WARN] CSV export failed: {e}")
                if composite_xml.exists():
                    self._export_csv_from_xml(composite_xml, benchmark_results_dir / f"{result_identifier}.csv")

            # Generate human-readable summary
            if composite_xml.exists():
                self._generate_summary(composite_xml, benchmark_results_dir / f"{result_identifier}-summary.txt")
                print(f"    Saved: {result_identifier}-summary.txt")

    def _export_csv_from_xml(self, xml_path: Path, csv_path: Path):
        """Export results from XML to CSV format."""
        import csv

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            results = []
            for result in root.findall('.//Result'):
                title = result.find('Title')
                description = result.find('Description')
                scale = result.find('Scale')

                title_text = title.text if title is not None else 'Unknown'
                desc_text = description.text if description is not None else 'Unknown'
                scale_text = scale.text if scale is not None else 'Unknown'

                for entry in result.findall('.//Data/Entry'):
                    identifier = entry.find('Identifier')
                    value = entry.find('Value')
                    raw_string = entry.find('RawString')

                    if value is not None:
                        results.append({
                            'test': title_text,
                            'description': desc_text,
                            'identifier': identifier.text if identifier is not None else '',
                            'value': value.text,
                            'unit': scale_text,
                            'raw_values': raw_string.text if raw_string is not None else ''
                        })

            if results:
                with open(csv_path, 'w', newline='') as csvfile:
                    fieldnames = ['test', 'description', 'identifier', 'value', 'unit', 'raw_values']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    for row in results:
                        writer.writerow(row)
                print(f"    Exported: {csv_path.name} (from XML)")
            else:
                print(f"    [WARN] No results found in XML")
        except Exception as e:
            print(f"    [ERROR] Failed to parse XML: {e}")

    def _generate_summary(self, xml_path: Path, summary_path: Path):
        """Generate human-readable text summary from XML results."""
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            with open(summary_path, 'w') as f:
                # Print header
                gen = root.find('Generated')
                if gen is not None:
                    title = gen.find('Title')
                    desc = gen.find('Description')
                    last_mod = gen.find('LastModified')
                    f.write("=" * 80 + "\n")
                    f.write(f"Benchmark: {title.text if title is not None else 'Unknown'}\n")
                    f.write(f"Description: {desc.text if desc is not None else 'Unknown'}\n")
                    f.write(f"Date: {last_mod.text if last_mod is not None else 'Unknown'}\n")
                    f.write("=" * 80 + "\n\n")

                # Print system info
                system = root.find('System')
                if system is not None:
                    hw = system.find('Hardware')
                    sw = system.find('Software')
                    if hw is not None:
                        f.write(f"Hardware: {hw.text}\n")
                    if sw is not None:
                        f.write(f"Software: {sw.text}\n")
                    f.write("\n")

                # Print results
                for result in root.findall('.//Result'):
                    title = result.find('Title')
                    description = result.find('Description')
                    scale = result.find('Scale')

                    f.write("-" * 80 + "\n")
                    f.write(f"Test: {title.text if title is not None else 'Unknown'}\n")
                    f.write(f"Metric: {description.text if description is not None else 'Unknown'}\n")
                    f.write(f"Unit: {scale.text if scale is not None else 'Unknown'}\n\n")

                    for entry in result.findall('.//Data/Entry'):
                        identifier = entry.find('Identifier')
                        value = entry.find('Value')
                        raw_string = entry.find('RawString')

                        if value is not None:
                            f.write(f"  Result: {value.text}\n")
                            if raw_string is not None and raw_string.text:
                                f.write(f"  Raw values: {raw_string.text}\n")
                            f.write("\n")

                f.write("=" * 80 + "\n")
        except Exception as e:
            print(f"    [ERROR] Failed to generate summary: {e}")

    def cleanup_test(self):
        """Remove test installation after completion."""
        print(">>> Removing test installation...")
        try:
            subprocess.run(
                ['bash', '-c', f'echo "y" | PTS_USER_PATH_OVERRIDE="{self.config_dir}" phoronix-test-suite remove-installed-test "{self.benchmark_full}"'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except:
            pass

    def override_test_profile(self):
        """Override PTS test-profile with local version if it exists."""
        local_test_profile = self.config_dir / "test-profiles" / "pts" / self.benchmark
        pts_test_profile = Path.home() / ".phoronix-test-suite" / "test-profiles" / "pts" / self.benchmark

        if local_test_profile.exists():
            print(f"[INFO] Overriding PTS test-profile with local version: {local_test_profile}")
            pts_test_profile.parent.mkdir(parents=True, exist_ok=True)

            # Copy all files from local test-profile to PTS directory
            if pts_test_profile.exists():
                shutil.rmtree(pts_test_profile)
            shutil.copytree(local_test_profile, pts_test_profile)
            print(f"[OK] Test-profile copied to {pts_test_profile}")
        else:
            print(f"[INFO] No local test-profile override found for {self.benchmark}")

    def run(self):
        """Main execution flow."""
        print(f"[INFO] Machine name: {os.environ.get('MACHINE_NAME', os.uname().nodename)}")
        print(f"[INFO] Detected {self.available_cores} CPU cores")

        # Validate configuration
        self.validate_config()

        # Determine execution mode
        self.determine_execution_mode()

        # Override test-profile BEFORE any PTS operations
        self.override_test_profile()

        # Set CPU governor
        self.set_cpu_governor_performance()

        # Verify batch mode
        self.verify_batch_mode()

        # Force install/rebuild test
        self.force_install_test()

        # Run benchmarks
        failed_tests = []
        for threads in range(self.thread_start, self.thread_end + 1):
            if not self.run_benchmark_for_threads(threads):
                failed_tests.append(threads)

        # Export results
        self.export_results()

        # Cleanup
        self.cleanup_test()

        # Summary
        print("\n=== Benchmark Summary ===")
        print(f"Benchmark: {self.benchmark_full}")
        if self.thread_start == self.thread_end:
            print(f"Threads tested: {self.thread_start} (fixed)")
        else:
            print(f"Threads tested: {self.thread_start} to {self.thread_end}")

        if not failed_tests:
            print("[OK] All tests completed successfully")
        else:
            print(f"[WARN] Failed tests (threads): {failed_tests}")

        return len(failed_tests) == 0


def main():
    parser = argparse.ArgumentParser(
        description='Run Phoronix Test Suite benchmarks with thread control',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s coremark-1.0.1           # Test with 1 to max vCPUs
  %(prog)s sysbench-1.1.0 4         # Test only with 4 threads
  %(prog)s openssl-3.6.0 8          # Test with 8 threads (compile-time mode if >= vCPU)
        """
    )

    parser.add_argument('benchmark', help='PTS benchmark name (e.g., coremark-1.0.1, sysbench-1.1.0)')
    parser.add_argument('threads', nargs='?', type=int, help='Number of threads (optional)')

    args = parser.parse_args()

    # Validate threads
    if args.threads is not None and args.threads <= 0:
        print(f"[ERROR] Thread count must be a positive integer (got: {args.threads})")
        sys.exit(1)

    runner = PTSBenchmarkRunner(args.benchmark, args.threads)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
