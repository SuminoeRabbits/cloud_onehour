#!/bin/bash
#
# clean_escape_in_log.sh
#
# Remove ANSI escape sequences from all .log files in the results directory.
# This script safely replaces escape codes in-place.
#
# Usage:
#   ./clean_escape_in_log.sh [directory]
#
# Arguments:
#   directory - Target directory to process (default: current directory)
#
# Examples:
#   ./clean_escape_in_log.sh
#   ./clean_escape_in_log.sh /path/to/results
#

set -euo pipefail

# Default to current directory if no argument provided
TARGET_DIR="${1:-.}"

# Check if target directory exists
if [[ ! -d "$TARGET_DIR" ]]; then
    echo "Error: Directory '$TARGET_DIR' does not exist." >&2
    exit 1
fi

# Counter for processed files
processed=0
modified=0

echo "Scanning for .log files in: $TARGET_DIR"
echo "---"

# Find all .log files and process them
while IFS= read -r -d '' logfile; do
    ((processed++)) || true

    # Check if file contains ANSI escape sequences
    # ANSI escape pattern: ESC [ ... m (where ESC is \x1b or \033)
    if grep -qP '\x1b\[[0-9;]*m' "$logfile" 2>/dev/null; then
        echo "Cleaning: $logfile"

        # Create a temporary file for safe replacement
        tmpfile=$(mktemp)

        # Remove ANSI escape sequences and write to temp file
        # Pattern matches: ESC [ <params> m
        # Where <params> is zero or more digits and semicolons
        sed 's/\x1b\[[0-9;]*m//g' "$logfile" > "$tmpfile"

        # Replace original file with cleaned version
        mv "$tmpfile" "$logfile"

        ((modified++)) || true
    fi
done < <(find "$TARGET_DIR" -name "*.log" -type f -print0)

echo "---"
echo "Done!"
echo "  Files scanned: $processed"
echo "  Files cleaned: $modified"
