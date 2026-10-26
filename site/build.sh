#!/usr/bin/env bash
# 组装 GitHub Pages 站点：site/ 下的页面 + examples/ 里的结构图 PNG 与离线交互看板 HTML。
# 用法（仓库根目录）：bash site/build.sh _site
set -euo pipefail
out="${1:-_site}"
rm -rf "$out"
mkdir -p "$out"
cp site/index.html site/favicon.svg site/dashboard-demo.gif "$out/"
for dir in examples/*/; do
  sym="$(basename "$dir")"
  mkdir -p "$out/examples/$sym"
  cp "$dir${sym}_preview.png" "$dir${sym}_dashboard.html" "$out/examples/$sym/"
done
touch "$out/.nojekyll"
