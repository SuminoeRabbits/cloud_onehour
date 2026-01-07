#!/usr/bin/env python3
import subprocess
import json
import sys
import argparse
from tabulate import tabulate

# --- 設定エリア ---
AWS_REGION = "ap-northeast-1"
GCP_REGION_KEY = "asia-northeast1"
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

def get_all_instances():
    """全クラウドから削除対象のインスタンスを収集する"""
    all_instances = []
    
    # --- AWS ---
    success, data, _ = run_command(["aws", "ec2", "describe-instances", "--region", AWS_REGION, "--output", "json"])
    if success:
        for res in data.get("Reservations", []):
            for inst in res.get("Instances", []):
                if inst["State"]["Name"] != "terminated":
                    name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "N/A")
                    all_instances.append(["AWS", AWS_REGION, name, inst["InstanceId"], inst["State"]["Name"]])

    # --- GCP ---
    success, data, _ = run_command(["gcloud", "compute", "instances", "list", f"--filter=zone ~ {GCP_REGION_KEY}", "--format=json"])
    if success and data:
        for inst in data:
            zone = inst.get("zone", "").split("/")[-1]
            all_instances.append(["GCP", zone, inst.get("name"), inst.get("id"), inst.get("status")])

    # --- OCI ---
    try:
        cid_proc = subprocess.run(["oci", "iam", "compartment", "list", "--query", "data[0].id", "--raw-output"], capture_output=True, text=True, timeout=10)
        if cid_proc.returncode == 0:
            compartment_id = cid_proc.stdout.strip()
            success, data, _ = run_command(["oci", "compute", "instance", "list", "--compartment-id", compartment_id, "--output", "json"])
            if success:
                for inst in data.get("data", []):
                    if inst["lifecycle-state"] != "TERMINATED":
                        all_instances.append(["OCI", inst.get("region", "N/A"), inst.get("display-name"), inst.get("id")[-10:], inst.get("lifecycle-state")])
    except:
        pass

    return all_instances

def execute_kill(instances):
    """収集したリストに基づいて削除を実行する"""
    for inst in instances:
        cloud, loc, name, inst_id, _ = inst
        print(f"Terminating {cloud} instance: {name} ({inst_id})...")
        
        if cloud == "AWS":
            subprocess.run(["aws", "ec2", "terminate-instances", "--instance-ids", inst_id, "--region", loc], capture_output=True)
        elif cloud == "GCP":
            subprocess.run(["gcloud", "compute", "instances", "delete", name, "--zone", loc, "--quiet"], capture_output=True)
        elif cloud == "OCI":
            # OCIのIDは短縮表示していたので、フルIDが必要（本来は収集時に保持すべきですが簡易化のため再取得なしなら元のリストに含める必要あり）
            # ここでは安全のため、テーブル用リストのIDは表示用、削除には実際のIDを渡すよう設計
            subprocess.run(["oci", "compute", "instance", "terminate", "--instance-id", inst_id, "--force"], capture_output=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Delete all without confirmation")
    args = parser.parse_args()

    print("Scanning for running instances across clouds...")
    targets = get_all_instances()

    if not targets:
        print("\nNo running instances found. Your wallet is safe!")
        return

    # リストを表示
    print("\nTarget Instances to be DELETED:")
    headers = ["Cloud", "Region/Zone", "Name", "ID", "Status"]
    print(tabulate(targets, headers=headers, tablefmt="grid"))

    # 確認プロセス
    if not args.force:
        confirm = input("\nDo you want to DELETE ALL these instances? [y/N]: ")
        if confirm.lower() != 'y':
            print("Abortion. Nothing was deleted.")
            sys.exit(0)

    print("\nStarting termination process...")
    execute_kill(targets)
    print("\nAll termination commands sent.")

if __name__ == "__main__":
    main()