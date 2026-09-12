#!/bin/sh
# install.sh — put these tools on the PATH by linking them into ~/.local/bin.
#
#   ./install.sh            link everything, report what changed
#   ./install.sh --check    report what would change, touch nothing
#   BIN_DIR=/somewhere ./install.sh     link into a different directory
#
# Links rather than copies, so `git pull` in this checkout updates the installed
# tools with no second step. That is also why the checkout has to stay where it is:
# move it and the links break.

set -eu

REPO_DIR=$(cd "$(dirname "$0")" && pwd)
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

TOOLS="clickup-cli.py recruit roster fire tell await await-mr"

changed=0
note() { printf '%s\n' "$*"; }

link_one() {
    src="$1"
    dst="$2"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        note "ok       $(basename "$dst")"
        return 0
    fi
    changed=$((changed + 1))
    if [ "$CHECK" = "1" ]; then
        if [ -e "$dst" ] || [ -L "$dst" ]; then
            note "differs  $(basename "$dst") -> would be relinked"
        else
            note "missing  $(basename "$dst") -> would be created"
        fi
        return 0
    fi
    # An existing regular file is somebody's own copy; keep it rather than destroy it.
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
        backup="$dst.backup-$(date +%Y%m%d-%H%M%S)"
        mv "$dst" "$backup"
        note "kept     $(basename "$dst") as $(basename "$backup")"
    fi
    rm -f "$dst"
    ln -s "$src" "$dst"
    note "linked   $(basename "$dst")"
}

mkdir -p "$BIN_DIR"

for tool in $TOOLS; do
    [ -f "$REPO_DIR/bin/$tool" ] || { echo "install.sh: $REPO_DIR/bin/$tool is missing" >&2; exit 1; }
    chmod +x "$REPO_DIR/bin/$tool"
    link_one "$REPO_DIR/bin/$tool" "$BIN_DIR/$tool"
done

# The ClickUp CLI is called `clickup`, not `clickup-cli.py`, everywhere it is used.
link_one "$REPO_DIR/bin/clickup-cli.py" "$BIN_DIR/clickup"

note ""
if [ "$CHECK" = "1" ]; then
    [ "$changed" = "0" ] && note "everything is linked" || note "$changed link(s) would change — run ./install.sh"
    exit 0
fi
[ "$changed" = "0" ] && note "everything was already linked" || note "$changed link(s) updated"

# Prerequisites. None of these stop the installation: every tool degrades to a clear
# message instead of a stack trace, and some of them are wanted by only one user in ten.
note ""
note "Prerequisites:"

case ":$PATH:" in
    *":$BIN_DIR:"*) note "  ok    $BIN_DIR is on your PATH" ;;
    *) note "  MISS  $BIN_DIR is not on your PATH — add it to your shell profile" ;;
esac

if command -v python3 >/dev/null 2>&1; then
    note "  ok    python3 ($(python3 --version 2>&1)) — needed by the ClickUp CLI"
else
    note "  MISS  python3 — the ClickUp CLI will not run without it"
fi

if [ -f "$HOME/.config/clickup/token" ]; then
    note "  ok    ClickUp token at ~/.config/clickup/token"
else
    note "  MISS  ClickUp token — create it with:"
    note "          mkdir -p ~/.config/clickup"
    note "          echo 'pk_your_personal_token' > ~/.config/clickup/token"
    note "        Get the token at ClickUp -> Settings -> Apps -> API Token."
    note "        The workspace and your user id are discovered on first use."
fi

if command -v herdr >/dev/null 2>&1; then
    note "  ok    herdr — the orchestration scripts can run"
else
    note "  MISS  herdr — recruit, roster, fire, tell, await and await-mr need it."
    note "        The ClickUp CLI does not; ignore this if you only came for that."
fi

if command -v glab >/dev/null 2>&1; then
    note "  ok    glab — await-mr can watch merge requests"
else
    note "  MISS  glab — only await-mr needs it (brew install glab, then glab auth login)"
fi
