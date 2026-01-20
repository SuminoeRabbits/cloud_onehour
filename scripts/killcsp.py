#!/usr/bin/env python3
import subprocess
import json
import sys
import argparse
from tabulate import tabulate

# --- 設定エリア ---
AWS_REGION = "ap-northeast-1"
# GCPとOCIは全リージョン検索のため設定不要
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

def get_all_instances(csp_filter=None):
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
        success, data, _ = run_command(["aws", "ec2", "describe-instances", "--region", AWS_REGION, "--output", "json"])
        if success:
            for res in data.get("Reservations", []):
                for inst in res.get("Instances", []):
                    if inst["State"]["Name"] != "terminated":
                        name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "N/A")
                        # [cloud, location, name, display_id, full_id, state]
                        all_instances.append(["AWS", AWS_REGION, name, inst["InstanceId"], inst["InstanceId"], inst["State"]["Name"]])

    # --- GCP --- (全リージョン・全ゾーン検索)
    if 'gcp' in enabled_csps:
        success, data, _ = run_command(["gcloud", "compute", "instances", "list", "--format=json"])
        if success and data:
            for inst in data:
                zone = inst.get("zone", "").split("/")[-1]
                name = inst.get("name")
                # GCPは名前で削除するので、full_idにも名前を格納
                all_instances.append(["GCP", zone, name, inst.get("id"), name, inst.get("status")])

    # --- OCI ---
    if 'oci' in enabled_csps:
        try:
            # テナンシーID（ルートコンパートメント）を取得
            cid_proc = subprocess.run(["oci", "iam", "compartment", "list", "--query", "data[0].\"compartment-id\"", "--raw-output"], capture_output=True, text=True, timeout=10)
            if cid_proc.returncode == 0:
                compartment_id = cid_proc.stdout.strip()
                success, data, _ = run_command(["oci", "compute", "instance", "list", "--compartment-id", compartment_id, "--output", "json"])
                if success:
                    for inst in data.get("data", []):
                        if inst["lifecycle-state"] != "TERMINATED":
                            full_id = inst.get("id")
                            display_id = full_id[-10:] if full_id else "N/A"
                            all_instances.append(["OCI", inst.get("region", "N/A"), inst.get("display-name"), display_id, full_id, inst.get("lifecycle-state")])
        except:
            pass

    return all_instances

def execute_kill(instances):
    """
    収集したリストに基づいて削除を実行する

    Args:
        instances: インスタンスリスト [cloud, location, name, display_id, full_id, state]
    """
    for inst in instances:
        cloud, loc, name, display_id, full_id, _ = inst
        print(f"Terminating {cloud} instance: {name} ({display_id})...")

        if cloud == "AWS":
            subprocess.run(["aws", "ec2", "terminate-instances", "--instance-ids", full_id, "--region", loc], capture_output=True)
        elif cloud == "GCP":
            # GCPは名前で削除（full_idには名前が入っている）
            subprocess.run(["gcloud", "compute", "instances", "delete", full_id, "--zone", loc, "--quiet"], capture_output=True)
        elif cloud == "OCI":
            # OCIはフルIDを使用
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

    targets = get_all_instances(csp_filter)

    if not targets:
        if csp_filter == 'all':
            print("\nNo running instances found in any cloud. Your wallet is safe!")
        else:
            print(f"\nNo running instances found in {csp_filter.upper()}. Your wallet is safe!")
        return

    # リストを表示（display_id列を使用）
    print("\nTarget Instances to be DELETED:")
    headers = ["Cloud", "Region/Zone", "Name", "ID", "Status"]
    # テーブル用にdisplay_id列のみ表示（full_idは削除時に使用）
    display_targets = [[inst[0], inst[1], inst[2], inst[3], inst[5]] for inst in targets]
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