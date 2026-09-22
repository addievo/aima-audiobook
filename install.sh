#!/usr/bin/env bash
# aima-audiobook one-line installer for macOS and Linux:
#   curl -fsSL https://raw.githubusercontent.com/addievo/aima-audiobook/main/install.sh | bash
# Installs uv (if missing), the aima-audiobook command, and ffmpeg (if missing and a package manager is available).
set -euo pipefail
SRC="${AIMA_AUDIOBOOK_SRC:-git+https://github.com/addievo/aima-audiobook}"

if ! command -v uv >/dev/null 2>&1; then
  echo "== installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v git >/dev/null 2>&1 && [[ "$SRC" == git+* ]]; then
  echo "git is required to install from GitHub: macOS 'xcode-select --install', Debian/Ubuntu 'sudo apt install git'"; exit 1
fi

echo "== installing aima-audiobook"
uv tool install --force --python 3.12 "$SRC"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "== installing ffmpeg"
  if command -v brew >/dev/null 2>&1; then brew install ffmpeg
  elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
  elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y ffmpeg
  elif command -v pacman >/dev/null 2>&1; then sudo pacman -S --noconfirm ffmpeg
  else echo "install ffmpeg yourself: https://ffmpeg.org/download.html"; fi
fi

uv tool update-shell >/dev/null 2>&1 || true
echo
echo "done. Open a new terminal, then:"
echo "  aima-audiobook toc your-book.pdf"
echo "  aima-audiobook build your-book.pdf --select rmit-ai26"
