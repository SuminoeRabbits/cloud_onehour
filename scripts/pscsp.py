#!/usr/bin/env python3
import subprocess
import json
import sys
from tabulate import tabulate

# --- 設定エリア ---
AWS_REGION = "ap-northeast-1"
GCP_REGION_KEY = "asia-northeast1"
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

def get_aws_instances(region):
    cmd = ["aws", "ec2", "describe-instances", "--region", region, "--output", "json"]
    success, data, err = run_command(cmd)
    if not success:
        return [["AWS", region, "ERROR", "N/A", err]]
    
    rows = []
    for res in data.get("Reservations", []):
        for inst in res.get("Instances", []):
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "N/A")
            rows.append(["AWS", region, name, inst["InstanceId"], inst["State"]["Name"]])
    return rows if rows else [["AWS", region, "(No Instances Found)", "-", "-"]]

def get_gcp_instances(region_keyword):
    cmd = ["gcloud", "compute", "instances", "list", f"--filter=zone ~ {region_keyword}", "--format=json"]
    success, data, err = run_command(cmd)
    if not success:
        # 認証エラー(Expired)などの場合、errにメッセージが入る
        return [["GCP", region_keyword, "ERROR", "N/A", err]]
    
    rows = []
    for inst in data:
        zone = inst.get("zone", "").split("/")[-1]
        rows.append(["GCP", zone, inst.get("name"), inst.get("id"), inst.get("status")])
    return rows if rows else [["GCP", region_keyword, "(No Instances Found)", "-", "-"]]

def get_oci_instances():
    # 1. まずテナンシーID（ルートコンパートメント）を自動取得
    cmd_id = ["oci", "iam", "compartment", "list", "--query", "data[0].id", "--raw-output"]
    # run_commandはJSON想定なので、ここは個別に実行
    try:
        cid_proc = subprocess.run(cmd_id, capture_output=True, text=True, timeout=10)
        if cid_proc.returncode != 0:
            return [["OCI", "N/A", "ERROR", "N/A", "Auth failed or Config missing"]]
        compartment_id = cid_proc.stdout.strip()
    except Exception:
        return [["OCI", "N/A", "ERROR", "N/A", "OCI CLI not installed"]]

    # 2. インスタンス取得
    cmd = ["oci", "compute", "instance", "list", "--compartment-id", compartment_id, "--output", "json"]
    success, data, err = run_command(cmd)
    if not success:
        return [["OCI", "N/A", "ERROR", "N/A", err]]
    
    rows = []
    for inst in data.get("data", []):
        rows.append(["OCI", inst.get("region", "N/A"), inst.get("display-name"), inst.get("id")[-10:], inst.get("lifecycle-state")])
    return rows if rows else [["OCI", "N/A", "(No Instances Found)", "-", "-"]]

def main():
    print("Gathering status from AWS, GCP, and OCI...")
    
    all_data = []
    all_data.extend(get_aws_instances(AWS_REGION))
    all_data.extend(get_gcp_instances(GCP_REGION_KEY))
    all_data.extend(get_oci_instances())

    headers = ["Cloud", "Region/Zone", "Name", "ID", "Status/Error"]
    print("\n" + tabulate(all_data, headers=headers, tablefmt="grid"))

if __name__ == "__main__":
    main()