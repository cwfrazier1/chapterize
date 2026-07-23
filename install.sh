#!/usr/bin/env bash
# chapterize installer for macOS and Linux.
# Installs ffmpeg (if missing) and the chapterize app into a venv, then
# symlinks the `chapterize` / `chapterize-gui` commands into ~/.local/bin.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
VENV="$PREFIX/share/chapterize-venv"
BIN="$PREFIX/bin"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# --- ensure ffmpeg -----------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
  say "ffmpeg not found — installing"
  if [[ "$(uname)" == "Darwin" ]]; then
    if command -v brew >/dev/null 2>&1; then
      brew install ffmpeg
    else
      warn "Homebrew not found. Install it from https://brew.sh then rerun,"
      warn "or install ffmpeg manually. Continuing without ffmpeg."
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y ffmpeg python3-tk python3-venv
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y ffmpeg python3-tkinter
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm ffmpeg tk
  else
    warn "Unknown package manager — please install ffmpeg yourself."
  fi
else
  say "ffmpeg present: $(command -v ffmpeg)"
fi

# --- python venv + install --------------------------------------------
say "Creating venv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$here"

mkdir -p "$BIN"
ln -sf "$VENV/bin/chapterize" "$BIN/chapterize"
ln -sf "$VENV/bin/chapterize-gui" "$BIN/chapterize-gui"

say "Installed."
echo "  CLI: chapterize --help"
echo "  GUI: chapterize-gui"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) warn "Add $BIN to your PATH:  export PATH=\"$BIN:\$PATH\"" ;;
esac
