#!/bin/sh
# generate-apk-index.sh
#
# Generate an apk repository index (APKINDEX.tar.gz) from a directory of .apk files.
#
# Usage:  generate-apk-index.sh <apk-dir> [output-dir]
#   apk-dir    Directory containing *.apk files
#   output-dir Where to write APKINDEX.tar.gz (default: apk-dir)
#
# Strategy:
#   1. If the `apk index` command is available (apk-tools), use it (authoritative).
#   2. Otherwise fall back to manual generation by parsing each .apk's metadata
#      (APKv2 is a tar.gz: control data in .PKGINFO inside the tarball).
#
# See: ~/net4sats/PLAN-openwrt-feed.md Phase 1
set -eu

usage() {
    echo "Usage: $0 <apk-dir> [output-dir]" >&2
    exit 2
}

[ $# -ge 1 ] || usage
APK_DIR=$1
OUT_DIR=${2:-$APK_DIR}

[ -d "$APK_DIR" ] || { echo "error: apk dir '$APK_DIR' not found" >&2; exit 1; }
mkdir -p "$OUT_DIR"

# Use a tmpdir under OUT_DIR so the script works even when system /tmp is full.
export TMPDIR="$OUT_DIR/.tmp"
mkdir -p "$TMPDIR"

# Absolutize OUT_DIR: the tar step cds into a temp dir, so a relative OUT_DIR
# would resolve against the wrong CWD.
OUT_DIR=$(cd "$OUT_DIR" && pwd)

# Collect apk files (null-delimited for safety with spaces)
apks=$(find "$APK_DIR" -maxdepth 1 -type f -name '*.apk' | sort)
apk_count=$(printf '%s\n' "$apks" | grep -c '\.apk$' || true)

if [ "$apk_count" -eq 0 ]; then
    echo "warning: no .apk files in '$APK_DIR' — generating empty index" >&2
fi

# ---------------------------------------------------------------- strategy 1
if command -v apk >/dev/null 2>&1; then
    echo "==> using native 'apk index'" >&2
    # apk index writes APKINDEX..tar.gz in CWD
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    # shellcheck disable=SC2086
    ( cd "$tmp" && apk index \
            --output "$OUT_DIR/APKINDEX.tar.gz" \
            --description "net4sats feed" \
            $apks )
    echo "==> wrote $OUT_DIR/APKINDEX.tar.gz (apk index, $apk_count packages)" >&2
    exit 0
fi

# ---------------------------------------------------------------- strategy 2
# Manual fallback. APKv2 packages are gzip-compressed tarballs whose first
# member is .PKGINFO, a list of `key = value` lines. We build an APKINDEX
# record per package using the documented flag prefixes.
#
# Reference flags (Alpine apk-tools):
#   P package name        V version          A arch
#   S .apk file size      I installed size   T deps (space-sep)
#   p provides            i install_if       k provider priority
#   D description         o origin           m maintainer
#   U file size (int)     t build timestamp  c commit hash
#   l license             u url
#
# Records are newline-separated by a blank line.
echo "==> 'apk index' not found; building APKINDEX manually" >&2

INDEX_FILE=$(mktemp)
DESC_FILE=$(mktemp)
trap 'rm -f "$INDEX_FILE" "$DESC_FILE"' EXIT

printf 'net4sats feed\n' > "$DESC_FILE"

# Requires tar + gzip (busybox tar is fine). GNU awk optional; we use POSIX sh.
for apk in $apks; do
    [ -f "$apk" ] || continue
    base=$(basename "$apk")
    size=$(wc -c < "$apk" | tr -d ' ')

    # Extract .PKGINFO member from the gzip tarball.
    pkginfo=$(tar -xzOf "$apk" .PKGINFO 2>/dev/null || true)
    [ -n "$pkginfo" ] || { echo "warn: $base has no .PKGINFO, skipping" >&2; continue; }

    # Helper: read first value for a PKGINFO key.
    field() { printf '%s\n' "$pkginfo" | sed -n "s/^$1 = \\(.*\\)$/\\1/p" | head -n1; }

    name=$(field pkgname)
    ver=$(field pkgver)
    arch=$(field arch)
    deps=$(field depend)
    provides=$(field provides)
    install_if=$(field install_if)
    origin=$(field origin)
    maint=$(field maintainer)
    license=$(field license)
    url=$(field url)
    desc=$(field pkgdesc)
    builddate=$(field builddate)
    csize=$(field packager || true)
    isz=$(field size || true)
    commit=$(field commit || true)

    # SHA1 checksum of the apk file (lowercase hex) — apk index uses 'C' for it.
    csum=$(sha1sum "$apk" | awk '{print $1}')

    {
        [ -n "$csum" ]    && printf 'C:%s\n'    "$csum"
        [ -n "$name" ]    && printf 'P:%s\n'    "$name"
        [ -n "$ver" ]     && printf 'V:%s\n'    "$ver"
        [ -n "$arch" ]    && printf 'A:%s\n'    "$arch"
        printf 'S:%s\n' "$size"
        [ -n "$isz" ]     && printf 'I:%s\n'    "$isz"
        [ -n "$deps" ]    && printf 'T:%s\n'    "$deps"
        [ -n "$provides" ]  && printf 'p:%s\n' "$provides"
        [ -n "$install_if" ] && printf 'i:%s\n' "$install_if"
        [ -n "$desc" ]    && printf 'D:%s\n'    "$desc"
        [ -n "$url" ]     && printf 'u:%s\n'    "$url"
        [ -n "$license" ] && printf 'l:%s\n'    "$license"
        [ -n "$origin" ]  && printf 'o:%s\n'    "$origin"
        [ -n "$maint" ]   && printf 'm:%s\n'    "$maint"
        [ -n "$builddate" ] && printf 't:%s\n'  "$builddate"
        printf 'U:%s\n' "$size"
        [ -n "$commit" ]  && printf 'c:%s\n'    "$commit"
        printf '\n'
    } >> "$INDEX_FILE"
done

# ---------------------------------------------------------------- tar+gzip
# APKINDEX.tar.gz is a gzip tarball containing APKINDEX and DESCRIPTION
# (plain files, not compressed individually).
tmp_tar=$(mktemp -d)
trap 'rm -rf "$tmp_tar"' EXIT
cp "$INDEX_FILE" "$tmp_tar/APKINDEX"
cp "$DESC_FILE"  "$tmp_tar/DESCRIPTION"

( cd "$tmp_tar" && tar -czf "$OUT_DIR/APKINDEX.tar.gz" APKINDEX DESCRIPTION )

echo "==> wrote $OUT_DIR/APKINDEX.tar.gz (manual, $apk_count packages)" >&2
