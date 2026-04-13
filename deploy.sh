#!/bin/bash
# Copy latest data to docs/ for GitHub Pages deployment
# Usage: ./deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Updating docs/ for GitHub Pages ==="

# Copy public data files
cp screening_data.json docs/
cp all_stocks.json docs/
echo "  screening_data.json, all_stocks.json copied"

# Copy app files
cp index.html docs/
echo "  index.html copied"

# Copy PWA assets (manifest and sw.js are maintained separately for docs/)
cp icon-192.svg docs/
cp icon-512.svg docs/
cp icon-maskable.svg docs/
echo "  Icons copied"

# NOTE: stocks.json is NOT copied (personal portfolio data)
# NOTE: manifest.json and sw.js in docs/ use relative paths, maintained separately

echo ""
echo "=== Files ready in docs/ ==="
ls -la docs/

echo ""
echo "=== Git commit and push ==="
git add docs/
git commit -m "Update screening data $(date +%Y-%m-%d)"
git push origin main

echo ""
echo "=== Deploy complete ==="
