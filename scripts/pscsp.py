#!/usr/bin/env python3
import subprocess
import json
import sys
from tabulate import tabulate

# --- 設定エリア ---
AWS_REGION = "ap-northeast-1"
# GCPとOCIは全リージョン検索のため設定不要
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

def get_gcp_instances():
    # 全リージョン・全ゾーンを検索（フィルターなし）
    cmd = ["gcloud", "compute", "instances", "list", "--format=json"]
    success, data, err = run_command(cmd)
    if not success:
        # 認証エラー(Expired)などの場合、errにメッセージが入る
        return [["GCP", "ALL", "ERROR", "N/A", err]]

    rows = []
    for inst in data:
        zone = inst.get("zone", "").split("/")[-1]
        rows.append(["GCP", zone, inst.get("name"), inst.get("id"), inst.get("status")])
    return rows if rows else [["GCP", "ALL", "(No Instances Found)", "-", "-"]]

def get_oci_instances():
    # 1. テナンシーID（ルートコンパートメント）を取得
    # compartment listの最初の項目のcompartment-idがテナンシーID
    cmd_id = ["oci", "iam", "compartment", "list", "--query", "data[0].\"compartment-id\"", "--raw-output"]
    try:
        cid_proc = subprocess.run(cmd_id, capture_output=True, text=True, timeout=10)
        if cid_proc.returncode != 0:
            # 詳細なエラーメッセージを取得
            err_msg = cid_proc.stderr.strip().split('\n')[0] if cid_proc.stderr else "Unknown error"
            return [["OCI", "N/A", "ERROR", "N/A", f"Auth/Config error: {err_msg[:40]}..."]]
        compartment_id = cid_proc.stdout.strip()
        if not compartment_id:
            return [["OCI", "N/A", "ERROR", "N/A", "No compartment found"]]
    except FileNotFoundError:
        return [["OCI", "N/A", "ERROR", "N/A", "OCI CLI not installed"]]
    except subprocess.TimeoutExpired:
        return [["OCI", "N/A", "ERROR", "N/A", "Timeout (Network unreachable)"]]
    except Exception as e:
        return [["OCI", "N/A", "ERROR", "N/A", f"Exception: {str(e)[:40]}"]]

    # 2. インスタンス取得
    cmd = ["oci", "compute", "instance", "list", "--compartment-id", compartment_id, "--output", "json"]
    success, data, err = run_command(cmd)
    if not success:
        return [["OCI", "N/A", "ERROR", "N/A", err]]

    rows = []
    for inst in data.get("data", []):
        # TERMINATEDインスタンスは除外
        lifecycle_state = inst.get("lifecycle-state", "UNKNOWN")
        if lifecycle_state != "TERMINATED":
            rows.append([
                "OCI",
                inst.get("region", "N/A"),
                inst.get("display-name", "N/A"),
                inst.get("id", "")[-10:],
                lifecycle_state
            ])
    return rows if rows else [["OCI", "N/A", "(No Instances Found)", "-", "-"]]

def main():
    print("Gathering status from AWS, GCP, and OCI...")
    
    all_data = []
    all_data.extend(get_aws_instances(AWS_REGION))
    all_data.extend(get_gcp_instances())
    all_data.extend(get_oci_instances())

    headers = ["Cloud", "Region/Zone", "Name", "ID", "Status/Error"]
    print("\n" + tabulate(all_data, headers=headers, tablefmt="grid"))

if __name__ == "__main__":
    main()