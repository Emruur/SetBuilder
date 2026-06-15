#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "Killing running instances..."
pkill -x SetBuilder 2>/dev/null || true
pkill -x SetBuilderDenoiser 2>/dev/null || true

echo "Clearing Application Support..."
rm -rf ~/Library/Application\ Support/SetBuilder/

echo "Removing apps from Applications..."
rm -rf /Applications/SetBuilder.app 2>/dev/null || true
rm -rf /Applications/SetBuilderDenoiser.app 2>/dev/null || true
rm -rf ~/Applications/SetBuilder.app 2>/dev/null || true
rm -rf ~/Applications/SetBuilderDenoiser.app 2>/dev/null || true

echo "Done."
