#!/usr/bin/env python3
import subprocess
import json
import sys
import argparse
from datetime import datetime, timedelta, timezone
from tabulate import tabulate

# --- 設定エリア ---
AWS_REGIONS_FALLBACK = ["ap-northeast-1"]
# GCPは全リージョン・全ゾーン検索
# OCIは全リージョン検索
# ----------------

def run_command(cmd):
    """コマンドを実行し、(成功フラグ, 結果データ, エラーメッセージ) を返す"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return True, json.loads(result.stdout), None
        else:
            # 標準エラー出力から主要なメッセージを抽出
            err_msg = result.stderr.strip().split('\n')[0]
            return False, None, f"CLI Error: {err_msg[:50]}..."
    except subprocess.TimeoutExpired:
        return False, None, "Timeout (Cloud or Network unreachable)"
    except FileNotFoundError:
        return False, None, f"Command '{cmd[0]}' not found"
    except Exception as e:
        return False, None, str(e)

def get_aws_regions():
    cmd = ["aws", "ec2", "describe-regions", "--query", "Regions[].RegionName", "--output", "json"]
    success, data, err = run_command(cmd)
    if success and isinstance(data, list) and data:
        return sorted(data)
    return AWS_REGIONS_FALLBACK

def get_oci_regions(tenancy_id=None):
    if tenancy_id:
        cmd = ["oci", "iam", "region-subscription", "list", "--tenancy-id", tenancy_id, "--query", "data[].region-name", "--output", "json"]
        success, data, _ = run_command(cmd)
        if success and isinstance(data, list) and data:
            return data
    cmd = ["oci", "iam", "region", "list", "--query", "data[].name", "--output", "json"]
    success, data, _ = run_command(cmd)
    if success and isinstance(data, list):
        return data
    return []

def get_oci_compartment_id():
    cmd_id = ["oci", "iam", "compartment", "list", "--query", "data[0].\"compartment-id\"", "--raw-output"]
    try:
        cid_proc = subprocess.run(cmd_id, capture_output=True, text=True, timeout=10)
        if cid_proc.returncode != 0:
            err_msg = cid_proc.stderr.strip().split('\n')[0] if cid_proc.stderr else "Unknown error"
            return None, f"Auth/Config error: {err_msg[:40]}..."
        compartment_id = cid_proc.stdout.strip()
        if not compartment_id:
            return None, "No compartment found"
        return compartment_id, None
    except FileNotFoundError:
        return None, "OCI CLI not installed"
    except subprocess.TimeoutExpired:
        return None, "Timeout (Network unreachable)"
    except Exception as e:
        return None, f"Exception: {str(e)[:40]}"

def format_start_jst(iso_timestamp):
    if not iso_timestamp:
        return "-"
    try:
        normalized = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        jst = dt.astimezone(timezone(timedelta(hours=9)))
        return jst.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "N/A"

def get_aws_instances(region):
    cmd = ["aws", "ec2", "describe-instances", "--region", region, "--output", "json"]
    success, data, err = run_command(cmd)
    if not success:
        return [["AWS", region, "ERROR", "N/A", err, "-"]]
    
    rows = []
    for res in data.get("Reservations", []):
        for inst in res.get("Instances", []):
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "N/A")
            start_jst = format_start_jst(inst.get("LaunchTime"))
            rows.append(["AWS", region, name, inst["InstanceId"], inst["State"]["Name"], start_jst])
    return rows if rows else [["AWS", region, "(No Instances Found)", "-", "-", "-"]]

def get_gcp_instances():
    # 全リージョン・全ゾーンを検索（フィルターなし）
    cmd = ["gcloud", "compute", "instances", "list", "--format=json"]
    success, data, err = run_command(cmd)
    if not success:
        # 認証エラー(Expired)などの場合、errにメッセージが入る
        return [["GCP", "ALL", "ERROR", "N/A", err, "-"]]

    rows = []
    for inst in data:
        zone = inst.get("zone", "").split("/")[-1]
        start_jst = format_start_jst(inst.get("creationTimestamp"))
        rows.append(["GCP", zone, inst.get("name"), inst.get("id"), inst.get("status"), start_jst])
    return rows if rows else [["GCP", "ALL", "(No Instances Found)", "-", "-", "-"]]

def get_oci_instances():
    compartment_id, err = get_oci_compartment_id()
    if err:
        return [["OCI", "N/A", "ERROR", "N/A", err, "-"]]

    regions = get_oci_regions(compartment_id)
    if not regions:
        regions = [None]

    rows = []
    for region in regions:
        cmd_prefix = ["env", f"OCI_REGION={region}"] if region else []
        cmd = cmd_prefix + ["oci", "compute", "instance", "list", "--compartment-id", compartment_id, "--output", "json"]
        success, data, err = run_command(cmd)
        if not success:
            rows.append(["OCI", region or "N/A", "ERROR", "N/A", err, "-"])
            continue

        for inst in data.get("data", []):
            lifecycle_state = inst.get("lifecycle-state", "UNKNOWN")
            if lifecycle_state != "TERMINATED":
                inst_region = inst.get("region")
                if region and inst_region and inst_region != region:
                    continue
                start_jst = format_start_jst(inst.get("time-created"))
                rows.append([
                    "OCI",
                    region or inst.get("region", "N/A"),
                    inst.get("display-name", "N/A"),
                    inst.get("id", "")[-10:],
                    lifecycle_state,
                    start_jst
                ])

    return rows if rows else [["OCI", "N/A", "(No Instances Found)", "-", "-", "-"]]

def main():
    parser = argparse.ArgumentParser(
        description="List cloud instances across AWS, GCP, and OCI",
        epilog="""
Examples:
  %(prog)s                 # Scan all clouds and show active regions/zones
  %(prog)s --show-all      # Show all regions/zones (including empty)
  %(prog)s --aws           # Scan AWS only
  %(prog)s --gcp           # Scan GCP only
  %(prog)s --oci           # Scan OCI only
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    csp_group = parser.add_mutually_exclusive_group()
    csp_group.add_argument("--all", action="store_true", help="Scan all CSPs (default)")
    csp_group.add_argument("--aws", action="store_true", help="Scan AWS only")
    csp_group.add_argument("--gcp", action="store_true", help="Scan GCP only")
    csp_group.add_argument("--oci", action="store_true", help="Scan OCI only")
    parser.add_argument("--show-all", action="store_true", help="Show all regions/zones (including empty)")
    args = parser.parse_args()

    print("Gathering status from AWS, GCP, and OCI...")
    
    all_data = []
    if args.aws:
        enabled_csps = {"aws"}
    elif args.gcp:
        enabled_csps = {"gcp"}
    elif args.oci:
        enabled_csps = {"oci"}
    else:
        enabled_csps = {"aws", "gcp", "oci"}

    if "aws" in enabled_csps:
        for region in get_aws_regions():
            all_data.extend(get_aws_instances(region))
    if "gcp" in enabled_csps:
        all_data.extend(get_gcp_instances())
    if "oci" in enabled_csps:
        all_data.extend(get_oci_instances())

    # Show only regions/zones that have active instances (search still covers all regions)
    active_keys = set()
    for row in all_data:
        cloud, region, name, _, status, _ = row
        if name and name.startswith("("):
            continue
        if status in {"N/A", "ERROR"}:
            continue
        active_keys.add((cloud, region))

    filtered = [
        row for row in all_data
        if (row[0], row[1]) in active_keys and not (row[2] and row[2].startswith("("))
    ]

    if not args.show_all:
        if filtered:
            all_data = filtered
        else:
            all_data = [["ALL", "-", "(No Active Instances Found)", "-", "-", "-"]]

    headers = ["Cloud", "Region/Zone", "Name", "ID", "Status/Error", "Start(JST)"]
    print("\n" + tabulate(all_data, headers=headers, tablefmt="grid"))

if __name__ == "__main__":
    main()
