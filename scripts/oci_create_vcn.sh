#!/bin/bash
set -e

if [ -z "$OCI_TENANCY_ID" ]; then
    echo "ERROR: OCI_TENANCY_ID が設定されていません"
    exit 1
fi

echo "=== Checking for existing VCNs ==="

VCN_COUNT=$(oci network vcn list \
    --compartment-id "$OCI_TENANCY_ID" \
    --query "length(data)" \
    --raw-output)

VCN_COUNT=${VCN_COUNT:-0}

if [ "$VCN_COUNT" -gt 0 ]; then
    echo "既存の VCN が見つかりました。作成はスキップします。"
    oci network vcn list --compartment-id "$OCI_TENANCY_ID"
    exit 0
fi

echo "=== No VCN found. Creating new VCN ==="

VCN_NAME="my-vcn"
VCN_CIDR="10.0.0.0/16"

VCN_OCID=$(
  oci network vcn create \
    --compartment-id "$OCI_TENANCY_ID" \
    --cidr-block "$VCN_CIDR" \
    --display-name "$VCN_NAME" \
    --query 'data.id' \
    --raw-output
)

echo "VCN created: $VCN_OCID"

echo "=== Creating Internet Gateway ==="

IGW_NAME="my-vcn-igw"

IGW_OCID=$(
  oci network internet-gateway create \
    --compartment-id "$OCI_TENANCY_ID" \
    --vcn-id "$VCN_OCID" \
    --is-enabled true \
    --display-name "$IGW_NAME" \
    --query 'data.id' \
    --raw-output
)

echo "Internet Gateway created: $IGW_OCID"

echo "=== Updating Route Table ==="

RT_OCID=$(
  oci network vcn get \
    --vcn-id "$VCN_OCID" \
    --query 'data."default-route-table-id"' \
    --raw-output
)

oci network route-table update \
  --rt-id "$RT_OCID" \
  --route-rules '[
    {
      "cidrBlock": "0.0.0.0/0",
      "networkEntityId": "'"$IGW_OCID"'"
    }
  ]' \
  --force

echo "Route Table updated: $RT_OCID"

echo "=== Creating Public Subnet ==="

SUBNET_NAME="my-public-subnet"
SUBNET_CIDR="10.0.1.0/24"

SUBNET_OCID=$(
  oci network subnet create \
    --compartment-id "$OCI_TENANCY_ID" \
    --vcn-id "$VCN_OCID" \
    --cidr-block "$SUBNET_CIDR" \
    --display-name "$SUBNET_NAME" \
    --prohibit-public-ip-on-vnic false \
    --query 'data.id' \
    --raw-output
)

echo "Subnet created: $SUBNET_OCID"

echo "=== VERIFYING NETWORK CONFIGURATION ==="

echo "Checking VCN..."
oci network vcn get --vcn-id "$VCN_OCID" >/dev/null && echo "✔ VCN OK"

echo "Checking Internet Gateway..."
oci network internet-gateway get --ig-id "$IGW_OCID" >/dev/null && echo "✔ IGW OK"

echo "Checking Route Table..."
oci network route-table get --rt-id "$RT_OCID" >/dev/null && echo "✔ Route Table OK"

echo "Checking Subnet..."
oci network subnet get --subnet-id "$SUBNET_OCID" >/dev/null && echo "✔ Subnet OK"

echo "=== DONE ==="
echo "VCN_OCID=$VCN_OCID"
echo "IGW_OCID=$IGW_OCID"
echo "RT_OCID=$RT_OCID"
echo "SUBNET_OCID=$SUBNET_OCID"
