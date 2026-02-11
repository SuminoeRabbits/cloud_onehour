#!/usr/bin/env python3
import subprocess
import os
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
    env = os.environ.copy()
    env["SUPPRESS_LABEL_WARNING"] = "True"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
        if result.returncode == 0 or (result.returncode == 2 and "oci" in cmd):
            stdout = result.stdout.strip()
            if not stdout:
                # Return empty dict/list depending on what's expected? 
                # Most loaders handle empty dict safely.
                return True, {}, None
            try:
                return True, json.loads(stdout), None
            except Exception as e:
                # JSON parse failed; include raw output for debugging
                return False, None, f"JSON parse error: {e}; stdout={stdout}"
        else:
            # Prefer stderr, fallback to stdout for CLI errors without stderr
            err_msg = result.stderr.strip() if result.stderr else ""
            if not err_msg:
                err_msg = result.stdout.strip()
            if not err_msg:
                err_msg = "Unknown error (no stderr/stdout)"
            return False, None, f"CLI Error (rc={result.returncode}): {err_msg}"
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
    # If user forces a specific region, honor it
    env_region = os.getenv("OCI_REGION")
    if env_region:
        return [env_region]

    if tenancy_id:
        cmd = [
            "oci", "iam", "region-subscription", "list",
            "--tenancy-id", tenancy_id,
            "--query", "data[].region-name",
            "--output", "json"
        ]
        success, data, _ = run_command(cmd)
        if success and isinstance(data, list) and data:
            return data
    # Fallback: do not enumerate all regions (too noisy); return empty to signal unknown
    return []

def get_oci_compartment_id():
    env_cid = os.getenv("OCI_COMPARTMENT_ID")
    if env_cid:
        return env_cid, None
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

    tenancy_id = os.getenv("OCI_TENANCY_ID")
    regions = get_oci_regions(tenancy_id)
    if not regions:
        return [["OCI", "N/A", "ERROR", "N/A", "No subscribed regions found", "-"]]

    rows = []
    for region in regions:
        cmd_prefix = ["env", f"OCI_REGION={region}"] if region else []
        cmd = cmd_prefix + [
            "oci", "compute", "instance", "list",
            "--compartment-id", compartment_id,
            "--compartment-id-in-subtree", "true",
            "--output", "json"
        ]
        success, data, err = run_command(cmd)
        if not success and err and "No such option" in err:
            # Older OCI CLI: retry without subtree option
            cmd = cmd_prefix + [
                "oci", "compute", "instance", "list",
                "--compartment-id", compartment_id,
                "--output", "json"
            ]
            success, data, err = run_command(cmd)
        if not success:
            rows.append(["OCI", region or "N/A", "ERROR", "N/A", err, "-"])
            continue

        for inst in data.get("data", []):
            lifecycle_state = inst.get("lifecycle-state", "UNKNOWN")
            if lifecycle_state != "TERMINATED":
                # Region is already scoped via OCI_REGION, so avoid mismatches
                start_jst = format_start_jst(inst.get("time-created"))
                def clean(value):
                    return value.strip() if isinstance(value, str) else value
                rows.append([
                    "OCI",
                    clean(region or inst.get("region", "N/A")),
                    clean(inst.get("display-name", "N/A")),
                    clean(inst.get("id", "")[-10:]),
                    clean(lifecycle_state),
                    clean(start_jst),
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
        if (
            (row[0], row[1]) in active_keys
            or row[4] == "ERROR"
        )
        and not (row[2] and row[2].startswith("("))
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
