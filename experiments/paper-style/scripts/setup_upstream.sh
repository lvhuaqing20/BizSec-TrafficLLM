#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
upstream="$(dirname "$CODE")"
commit=95b88f7357dbdd24873be9744e223c9dbf193007
patch="$PAPER_SCRIPTS/../patches/0001-runtime-portability.patch"
if [[ -e "$upstream" ]]; then
  echo "refusing to modify an existing upstream checkout: $upstream" >&2
  exit 1
fi
mkdir -p "$(dirname "$upstream")"
git init "$upstream"
git -C "$upstream" remote add origin https://github.com/ZGC-LLM-Safety/TrafficLLM.git
GIT_LFS_SKIP_SMUDGE=1 git -C "$upstream" fetch --depth=1 origin "$commit"
GIT_LFS_SKIP_SMUDGE=1 git -C "$upstream" checkout --detach FETCH_HEAD
test "$(git -C "$upstream" rev-parse HEAD)" = "$commit"
git -C "$upstream" apply --check "$patch"
git -C "$upstream" apply "$patch"
echo "Ready: $CODE (pinned upstream + documented runtime patch)"
