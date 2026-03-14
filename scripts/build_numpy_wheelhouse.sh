#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-${ROOT_DIR}/pts_runner/requirements_numpy-1.2.1.txt}"
WHEELHOUSE_DIR="${WHEELHOUSE_DIR:-${ROOT_DIR}/pts_runner/wheelhouse/numpy-1.2.1}"
TMP_VENV="${TMP_VENV:-/tmp/build-numpy-wheelhouse-$$}"

cleanup() {
    rm -rf "${TMP_VENV}"
}
trap cleanup EXIT

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "[ERROR] Python interpreter not found: ${PYTHON_BIN}" >&2
    exit 1
fi

if [ ! -f "${REQUIREMENTS_FILE}" ]; then
    echo "[ERROR] requirements file not found: ${REQUIREMENTS_FILE}" >&2
    exit 1
fi

echo "[INFO] Python interpreter : $(command -v "${PYTHON_BIN}")"
echo "[INFO] Requirements file : ${REQUIREMENTS_FILE}"
echo "[INFO] Wheelhouse dir    : ${WHEELHOUSE_DIR}"
echo "[INFO] Temp venv         : ${TMP_VENV}"

"${PYTHON_BIN}" -m venv "${TMP_VENV}"
"${TMP_VENV}/bin/pip" install --upgrade pip

mkdir -p "${WHEELHOUSE_DIR}"

echo "[INFO] Downloading wheels into wheelhouse..."
"${TMP_VENV}/bin/pip" download \
    --only-binary=:all: \
    --dest "${WHEELHOUSE_DIR}" \
    -r "${REQUIREMENTS_FILE}"

echo "[OK] Wheelhouse populated:"
find "${WHEELHOUSE_DIR}" -maxdepth 1 -type f -name '*.whl' | sort
