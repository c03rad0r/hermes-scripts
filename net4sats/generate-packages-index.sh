#!/bin/sh
# generate-packages-index.sh — Generate opkg Package index (Packages.gz) from .ipk files
#
# Scans a directory for *.ipk files, extracts control metadata from each,
# appends computed Size and SHA256sum, and writes a gzip-compressed Packages
# index (the opkg repository manifest format).
#
# Usage:
#   generate-packages-index.sh <ipk-dir> [output-dir]
#
#   ipk-dir     Directory containing .ipk files (required)
#   output-dir  Where to write Packages.gz (default: same as ipk-dir)
#
# Output:
#   <output-dir>/Packages      (uncompressed, kept for debugging)
#   <output-dir>/Packages.gz   (the actual opkg feed index)
#
# Exit codes:
#   0  success
#   1  usage error / missing dependencies
#   2  no .ipk files found
#
# Part of the TollGate OpenWrt feed pipeline.
# See ~/net4sats/PLAN-openwrt-feed.md Phase 1 (task F1.1).

set -eu

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

err() {
    echo "ERROR: $*" >&2
}

# Extract the control file text from an .ipk.
# .ipk = ar archive containing control.tar.gz (or .xz/.bz2/.zst) with ./control inside.
# Prints the control file contents to stdout.
# Single scratch dir reused for all control extractions, cleaned on exit.
WORK_TMPDIR="$(mktemp -d)"
cleanup_work_tmpdir() { rm -rf "$WORK_TMPDIR"; }
trap cleanup_work_tmpdir EXIT

extract_control() {
    ipk="$1"
    tmpdir="$(mktemp -d -p "$WORK_TMPDIR")"

    # Extract control.tar.* from the ar archive into tmpdir
    if ! ar x "$ipk" --output "$tmpdir" 2>/dev/null; then
        err "failed to ar-extract: $ipk"
        return 1
    fi

    # Find the control archive — name varies: control.tar.gz, control.tar.xz,
    # control.tar.bz2, control.tar.zst, or just control.tar
    ctrl_tar=""
    for f in "$tmpdir"/control.tar.* "$tmpdir"/control.tar; do
        [ -f "$f" ] && ctrl_tar="$f" && break
    done

    if [ -z "$ctrl_tar" ]; then
        err "no control.tar.* found in: $ipk"
        return 1
    fi

    # Extract ./control from the control tarball to stdout.
    # tar auto-detects compression for .gz/.xz/.bz2/.zst.
    # Try common paths: ./control, control
    if ! tar xf "$ctrl_tar" -C "$tmpdir" 2>/dev/null; then
        err "failed to extract control tarball: $ctrl_tar"
        return 1
    fi

    ctrl_file=""
    for candidate in "$tmpdir/control" "$tmpdir/./control"; do
        [ -f "$candidate" ] && ctrl_file="$candidate" && break
    done

    if [ -z "$ctrl_file" ]; then
        err "no 'control' file inside control.tar of: $ipk"
        return 1
    fi

    cat "$ctrl_file"
}

# Extract a single field from control text.
# Usage: get_field <control-text> <field-name>
# Handles multi-line continuation (leading whitespace) but for our fields
# (Package, Version, Depends) single-line is the norm.
get_field() {
    control_text="$1"
    field="$2"
    echo "$control_text" | sed -n "
        /^${field}:/ {
            # Print the first line
            p
            # Read continuation lines (start with space/tab)
            :loop
            n
            /^[ \t]/p
            b loop
        }
    " | sed "s/^${field}:[[:space:]]*//" | tr '\n' ' ' | sed 's/[[:space:]]*$//'
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

IPK_DIR="${1:-}"
OUTPUT_DIR="${2:-$IPK_DIR}"

if [ -z "$IPK_DIR" ]; then
    echo "Usage: $0 <ipk-dir> [output-dir]" >&2
    exit 1
fi

if [ ! -d "$IPK_DIR" ]; then
    err "not a directory: $IPK_DIR"
    exit 1
fi

# Check dependencies
for dep in ar tar gzip sha256sum find; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        err "missing required tool: $dep"
        exit 1
    fi
done

mkdir -p "$OUTPUT_DIR"

PACKAGES_FILE="$OUTPUT_DIR/Packages"
PACKAGES_GZ="$OUTPUT_DIR/Packages.gz"

# Collect .ipk files (sorted for deterministic output)
ipk_list="$(find "$IPK_DIR" -maxdepth 1 -name '*.ipk' -type f | sort)"

if [ -z "$ipk_list" ]; then
    err "no .ipk files found in: $IPK_DIR"
    exit 2
fi

count=0
# Write Packages index
: > "$PACKAGES_FILE"

echo "$ipk_list" | while IFS= read -r ipk; do
    filename="$(basename "$ipk")"
    size="$(stat -c%s "$ipk" 2>/dev/null || stat -f%z "$ipk" 2>/dev/null)"
    sha256="$(sha256sum "$ipk" | cut -d' ' -f1)"

    control_text="$(extract_control "$ipk")" || {
        err "skipping (control extraction failed): $filename"
        continue
    }

    package="$(get_field "$control_text" "Package")"
    version="$(get_field "$control_text" "Version")"
    depends="$(get_field "$control_text" "Depends")"
    architecture="$(get_field "$control_text" "Architecture")"
    maintainer="$(get_field "$control_text" "Maintainer")"
    description="$(get_field "$control_text" "Description")"

    if [ -z "$package" ]; then
        err "skipping (no Package field in control): $filename"
        continue
    fi

    {
        echo "Package: $package"
        echo "Version: $version"
        [ -n "$depends" ]       && echo "Depends: $depends"
        [ -n "$architecture" ]  && echo "Architecture: $architecture"
        [ -n "$maintainer" ]    && echo "Maintainer: $maintainer"
        [ -n "$description" ]   && echo "Description: $description"
        echo "Filename: $filename"
        echo "Size: $size"
        echo "SHA256sum: $sha256"
        echo ""
    } >> "$PACKAGES_FILE"

    count=$((count + 1))
    echo "  indexed: $package $version ($filename)"
done

# Also capture count from subshell via file line counting
total=$(grep -c '^Package: ' "$PACKAGES_FILE" 2>/dev/null || echo 0)

if [ "$total" -eq 0 ]; then
    err "no packages were indexed — check .ipk file integrity"
    rm -f "$PACKAGES_FILE"
    exit 2
fi

# Gzip the index
gzip -cn9 "$PACKAGES_FILE" > "$PACKAGES_GZ"

echo ""
echo "Done: $total package(s) indexed"
echo "  $PACKAGES_FILE  ($(wc -c < "$PACKAGES_FILE") bytes)"
echo "  $PACKAGES_GZ    ($(wc -c < "$PACKAGES_GZ") bytes)"
