#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

git init -q "${work_dir}/starrocks"
git -C "${work_dir}/starrocks" remote add origin https://github.com/StarRocks/starrocks.git
git -C "${work_dir}/starrocks" sparse-checkout init --no-cone
git -C "${work_dir}/starrocks" sparse-checkout set \
    /conf/ranger/ranger-starrocks-security.xml \
    /fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerAccessController.java \
    /fe/fe-core/src/main/java/com/starrocks/authorization/ranger/starrocks/RangerStarRocksAccessController.java \
    /fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerStarRocksAccessRequest.java \
    /fe/fe-core/src/test/java/com/starrocks/authorization/ranger/RangerInterfaceTest.java \
    /fe/fe-core/src/test/resources/ranger-starrocks-security.xml
git -C "${work_dir}/starrocks" fetch -q --filter=blob:none --depth 1 \
    origin 4a9848edf03f5c936dac664b2d52527f48e72eb0
git -C "${work_dir}/starrocks" checkout -q FETCH_HEAD
git -C "${work_dir}/starrocks" apply --check "${repo_root}/patches/starrocks/4.1.4-ranger-active-role.patch"
git -C "${work_dir}/starrocks" apply "${repo_root}/patches/starrocks/4.1.4-ranger-active-role.patch"
git -C "${work_dir}/starrocks" apply --check "${repo_root}/patches/starrocks/4.1.4-ranger-rpc-context.patch"
echo "Patch applies cleanly to StarRocks 4.1.4 (4a9848edf03f5c936dac664b2d52527f48e72eb0)."
