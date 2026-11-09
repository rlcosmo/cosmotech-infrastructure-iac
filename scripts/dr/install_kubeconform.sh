#!/usr/bin/env bash
set -euo pipefail
output_dir="${1:?destination directory required}"
mkdir -p "$output_dir"
archive="$output_dir/kubeconform-linux-amd64.tar.gz"
curl -fsSL --retry 2 https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz -o "$archive"
printf '%s  %s\n' 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883 "$archive" | sha256sum -c -
tar -xzf "$archive" -C "$output_dir" kubeconform
