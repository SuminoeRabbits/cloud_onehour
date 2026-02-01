#!/usr/bin/env python3
import subprocess
import json
import sys
import argparse
from datetime import datetime, timezone, timedelta
from tabulate import tabulate
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- 設定エリア ---
AWS_REGIONS_FALLBACK = ["ap-northeast-1"]
# GCPは全リージョン・全ゾーン検索
# OCIは全リージョン検索
# ----------------

def run_command(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, (json.loads(result.stdout) if result.stdout.strip() else {}), None
        return False, None, result.stderr.strip()
    except FileNotFoundError:
        return False, None, f"Command '{cmd[0]}' not found"
    except Exception as e:
        return False, None, str(e)

def parse_timestamp(iso_ts):
    if not iso_ts:
        return None
    try:
        normalized = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def format_elapsed(iso_ts):
    if not iso_ts:
        return "-"
    try:
        dt = parse_timestamp(iso_ts)
        if not dt:
            return "N/A"
        now = datetime.now(timezone.utc)
        elapsed = now - dt
        total = int(elapsed.total_seconds())
        if total < 0:
            return "0s"
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h}h{m:02}m"
        if m > 0:
            return f"{m}m{s:02}s"
        return f"{s}s"
    except Exception:
        return "N/A"

def format_start_jst(iso_ts):
    dt = parse_timestamp(iso_ts)
    if not dt:
        return "-"
    try:
        jst = dt.astimezone(timezone(timedelta(hours=9)))
        return jst.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "N/A"

def get_aws_regions(only_region=None):
    if only_region:
        return [only_region]
    success, data, _ = run_command(["aws", "ec2", "describe-regions", "--query", "Regions[].RegionName", "--output", "json"])
    if success and isinstance(data, list) and data:
        return sorted(data)
    return AWS_REGIONS_FALLBACK

def get_oci_regions(only_region=None):
    if only_region:
        return [only_region]
    success, data, _ = run_command(["oci", "iam", "region", "list", "--query", "data[].name", "--output", "json"])
    if success and isinstance(data, list):
        return data
    return []

def get_oci_compartment_id():
    cmd_id = ["oci", "iam", "compartment", "list", "--query", "data[0].\"compartment-id\"", "--raw-output"]
    try:
        cid_proc = subprocess.run(cmd_id, capture_output=True, text=True, timeout=10)
        if cid_proc.returncode != 0:
            return None
        compartment_id = cid_proc.stdout.strip()
        return compartment_id if compartment_id else None
    except Exception:
        return None

def get_all_instances(csp_filter=None, aws_region=None, oci_region=None):
    """
    全クラウドから削除対象のインスタンスを収集する

    Args:
        csp_filter: CSPフィルター ('aws', 'gcp', 'oci', または None で全て)

    Returns:
        list: インスタンスリスト [cloud, location, name, instance_id, full_id, state]
    """
    all_instances = []

    # CSPフィルター正規化
    enabled_csps = set()
    if csp_filter is None or csp_filter == 'all':
        enabled_csps = {'aws', 'gcp', 'oci'}
    else:
        enabled_csps = {csp_filter.lower()}

    # --- AWS ---
    if 'aws' in enabled_csps:
        regions = get_aws_regions(aws_region)
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(regions)))) as executor:
            future_map = {
                executor.submit(run_command, ["aws", "ec2", "describe-instances", "--region", region, "--output", "json"]): region
                for region in regions
            }
            for future in as_completed(future_map):
                region = future_map[future]
                success, data, _ = future.result()
                if success:
                    for res in data.get("Reservations", []):
                        for inst in res.get("Instances", []):
                            if inst["State"]["Name"] != "terminated":
                                name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "N/A")
                                # [cloud, location, name, display_id, full_id, state]
                        all_instances.append(["AWS", region, name, inst["InstanceId"], inst["InstanceId"], inst["State"]["Name"], inst.get("LaunchTime")])

    # --- GCP --- (全リージョン・全ゾーン検索)
    if 'gcp' in enabled_csps:
        success, data, _ = run_command(["gcloud", "compute", "instances", "list", "--format=json"])
        if success and data:
            for inst in data:
                zone = inst.get("zone", "").split("/")[-1]
                name = inst.get("name")
                # GCPは名前で削除するので、full_idにも名前を格納
                all_instances.append(["GCP", zone, name, inst.get("id"), name, inst.get("status"), inst.get("creationTimestamp")])

    # --- OCI ---
    if 'oci' in enabled_csps:
        compartment_id = get_oci_compartment_id()
        if compartment_id:
            regions = get_oci_regions(oci_region)
            if not regions:
                regions = [None]
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(regions)))) as executor:
                future_map = {}
                for region in regions:
                    cmd_prefix = ["env", f"OCI_REGION={region}"] if region else []
                    cmd = cmd_prefix + ["oci", "compute", "instance", "list", "--compartment-id", compartment_id, "--output", "json"]
                    future_map[executor.submit(run_command, cmd)] = region
                for future in as_completed(future_map):
                    region = future_map[future]
                    success, data, _ = future.result()
                    if success:
                        for inst in data.get("data", []):
                            if inst["lifecycle-state"] != "TERMINATED":
                                full_id = inst.get("id")
                                display_id = full_id[-10:] if full_id else "N/A"
                                all_instances.append(["OCI", region or inst.get("region", "N/A"), inst.get("display-name"), display_id, full_id, inst.get("lifecycle-state"), inst.get("time-created")])

    return all_instances

def execute_kill(instances):
    """
    収集したリストに基づいて削除を実行する

    Args:
        instances: インスタンスリスト [cloud, location, name, display_id, full_id, state]
    """
    for inst in instances:
        cloud, loc, name, display_id, full_id, _, _ = inst
        print(f"Terminating {cloud} instance: {name} ({display_id})...")

        if cloud == "AWS":
            subprocess.run(["aws", "ec2", "terminate-instances", "--instance-ids", full_id, "--region", loc], capture_output=True)
        elif cloud == "GCP":
            # GCPは名前で削除（full_idには名前が入っている）
            subprocess.run(["gcloud", "compute", "instances", "delete", full_id, "--zone", loc, "--quiet"], capture_output=True)
        elif cloud == "OCI":
            # OCIはフルIDを使用
            if loc and loc != "N/A":
                subprocess.run(["env", f"OCI_REGION={loc}", "oci", "compute", "instance", "terminate", "--instance-id", full_id, "--force"], capture_output=True)
            else:
                subprocess.run(["oci", "compute", "instance", "terminate", "--instance-id", full_id, "--force"], capture_output=True)

def main():
    parser = argparse.ArgumentParser(
        description='Terminate cloud instances across AWS, GCP, and OCI',
        epilog="""
Examples:
  %(prog)s                    # Terminate all instances (all CSPs)
  %(prog)s --all              # Same as above
  %(prog)s --aws              # Terminate AWS instances only
  %(prog)s --gcp              # Terminate GCP instances only
  %(prog)s --oci              # Terminate OCI instances only
  %(prog)s --aws --force      # Terminate AWS instances without confirmation
  %(prog)s --all --force      # Terminate all instances without confirmation
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # CSP選択オプション（相互排他的）
    csp_group = parser.add_mutually_exclusive_group()
    csp_group.add_argument("--all", action="store_true", help="Terminate instances from all CSPs (default)")
    csp_group.add_argument("--aws", action="store_true", help="Terminate AWS instances only")
    csp_group.add_argument("--gcp", action="store_true", help="Terminate GCP instances only")
    csp_group.add_argument("--oci", action="store_true", help="Terminate OCI instances only")

    parser.add_argument("--force", action="store_true", help="Delete without confirmation")
    parser.add_argument("--show-full-id", action="store_true", help="Show full instance ID in list output")
    parser.add_argument("--aws-region", help="Limit AWS scan to a single region")
    parser.add_argument("--oci-region", help="Limit OCI scan to a single region")

    args = parser.parse_args()

    # CSPフィルター決定
    csp_filter = None
    if args.aws:
        csp_filter = 'aws'
    elif args.gcp:
        csp_filter = 'gcp'
    elif args.oci:
        csp_filter = 'oci'
    else:
        # デフォルトは全CSP
        csp_filter = 'all'

    # スキャン対象を表示
    if csp_filter == 'all':
        print("Scanning for running instances across all clouds (AWS, GCP, OCI)...")
    else:
        print(f"Scanning for running instances in {csp_filter.upper()} only...")

    targets = get_all_instances(csp_filter, aws_region=args.aws_region, oci_region=args.oci_region)

    if not targets:
        if csp_filter == 'all':
            print("\nNo running instances found in any cloud. Your wallet is safe!")
        else:
            print(f"\nNo running instances found in {csp_filter.upper()}. Your wallet is safe!")
        return

    # リストを表示（display_id列を使用）
    print("\nTarget Instances to be DELETED:")
    headers = ["Cloud", "Region/Zone", "Name", "ID", "Status", "Elapsed", "Start(JST)"]
    # テーブル用にdisplay_id列のみ表示（full_idは削除時に使用）
    if args.show_full_id:
        display_targets = [[inst[0], inst[1], inst[2], inst[4], inst[5], format_elapsed(inst[6]), format_start_jst(inst[6])] for inst in targets]
    else:
        display_targets = [[inst[0], inst[1], inst[2], inst[3], inst[5], format_elapsed(inst[6]), format_start_jst(inst[6])] for inst in targets]
    print(tabulate(display_targets, headers=headers, tablefmt="grid"))

    # 確認プロセス
    if not args.force:
        confirm = input("\nDo you want to DELETE ALL these instances? [y/N]: ")
        if confirm.lower() != 'y':
            print("Aborted. Nothing was deleted.")
            sys.exit(0)

    print("\nStarting termination process...")
    execute_kill(targets)
    print("\nAll termination commands sent.")

if __name__ == "__main__":
    main()
