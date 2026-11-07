#!/usr/bin/env bash
set -euo pipefail
# Fail closed on a divergent main. In particular, never send if reservation push
# fails: an uncommitted reservation cannot prevent a later run from duplicating.
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
if [[ ! -d archive ]]; then
  echo 'No archive directory yet'
  exit 0
fi
git add archive/
if git diff --staged --quiet; then
  echo 'No archive changes to commit'
else
  git commit -m "chore(archive): $(TZ=Asia/Shanghai date +%Y-%m-%d) ${1:-status}"
  git push origin HEAD:main
fi
