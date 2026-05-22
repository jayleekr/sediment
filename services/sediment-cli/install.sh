#!/usr/bin/env bash
# Sediment CLI installer.
#
# One-liner for teammates:
#   curl -fsSL -H "Authorization: token $(gh auth token)" \
#     https://raw.githubusercontent.com/jayleekr/sediment/main/services/sediment-cli/install.sh \
#     | bash
#
# Or even shorter if you've already shell-aliased the curl part:
#   sediment-install
#
# Steps:
#   1. Detect OS + arch → release-asset triple
#   2. Resolve GITHUB_TOKEN (env > `gh auth token` > error)
#   3. Hit api.github.com for the latest release
#   4. Download the matching asset via api.github.com (private-repo-safe)
#   5. Verify sha256 against asset.digest metadata
#   6. Install to $INSTALL_DIR (default: /usr/local/bin if writable, else ~/.local/bin)
#   7. Print next steps

set -euo pipefail

REPO_OWNER="jayleekr"
REPO_NAME="sediment"
ASSET_TAG_PREFIX="sediment-cli-v"
INSTALL_DIR="${SEDIMENT_INSTALL_DIR:-}"
VERSION="${SEDIMENT_VERSION:-latest}"  # e.g. SEDIMENT_VERSION=v0.1.3 to pin

c_red()    { printf '\033[31m%s\033[0m\n' "$*" >&2; }
c_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
c_blue()   { printf '\033[34m%s\033[0m\n' "$*"; }
header()   { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

die() { c_red "✗ $*"; exit 1; }

# ---------- 1. Detect platform ----------
detect_triple() {
    local os arch
    case "$(uname -s)" in
        Darwin) os="apple-darwin" ;;
        Linux)  os="unknown-linux-gnu" ;;
        *)      die "Unsupported OS: $(uname -s) — install from source: cargo install --git https://github.com/$REPO_OWNER/$REPO_NAME --path services/sediment-cli" ;;
    esac
    case "$(uname -m)" in
        arm64|aarch64) arch="aarch64" ;;
        x86_64|amd64)  arch="x86_64" ;;
        *)             die "Unsupported arch: $(uname -m)" ;;
    esac
    echo "${arch}-${os}"
}

# ---------- 2. Resolve token ----------
resolve_token() {
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        echo "$GITHUB_TOKEN"
        return
    fi
    if command -v gh >/dev/null 2>&1; then
        local t
        t=$(gh auth token 2>/dev/null || true)
        if [[ -n "$t" ]]; then
            echo "$t"
            return
        fi
    fi
    die "No GitHub token available.
   Options:
     • install gh CLI:  brew install gh && gh auth login
     • or set env var:  export GITHUB_TOKEN=<personal-access-token>
   The Sediment repo is private, so download needs auth.
   This step is one-time; sediment update later uses the same token."
}

# ---------- 3+4+5. Resolve release, download, verify ----------
api() {
    local path="$1"
    curl -fsSL \
        -H "Authorization: token $TOKEN" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "https://api.github.com$path"
}

# ---------- 6. Pick install dir ----------
pick_install_dir() {
    if [[ -n "$INSTALL_DIR" ]]; then
        mkdir -p "$INSTALL_DIR"
        echo "$INSTALL_DIR"
        return
    fi
    for d in /usr/local/bin /opt/homebrew/bin "$HOME/.local/bin"; do
        if [[ -d "$d" && -w "$d" ]]; then
            echo "$d"
            return
        fi
    done
    mkdir -p "$HOME/.local/bin"
    echo "$HOME/.local/bin"
}

# ---------- main ----------
main() {
    header "Sediment CLI installer"

    local triple
    triple=$(detect_triple)
    c_blue "  target: $triple"

    TOKEN=$(resolve_token)
    c_blue "  github token: resolved (len=${#TOKEN})"

    header "Fetching release metadata"
    local release_json release_path
    if [[ "$VERSION" == "latest" ]]; then
        release_path="/repos/$REPO_OWNER/$REPO_NAME/releases/latest"
    else
        release_path="/repos/$REPO_OWNER/$REPO_NAME/releases/tags/${ASSET_TAG_PREFIX}${VERSION#v}"
    fi
    release_json=$(api "$release_path") || die "release lookup failed at $release_path"
    local tag
    tag=$(echo "$release_json" | grep -o '"tag_name": *"[^"]*"' | head -1 | cut -d'"' -f4)
    [[ -n "$tag" ]] || die "Could not parse tag_name from release response"
    c_green "  latest: $tag"

    local asset_name="sediment-${triple}.tar.gz"
    local asset_api_url asset_digest
    asset_api_url=$(echo "$release_json" | python3 -c "
import json, sys
r = json.load(sys.stdin)
for a in r.get('assets', []):
    if a['name'] == '$asset_name':
        print(a['url'])
        break
")
    asset_digest=$(echo "$release_json" | python3 -c "
import json, sys
r = json.load(sys.stdin)
for a in r.get('assets', []):
    if a['name'] == '$asset_name':
        d = a.get('digest', '')
        if d.startswith('sha256:'): d = d[7:]
        print(d)
        break
")
    [[ -n "$asset_api_url" ]] || die "No release asset named $asset_name in $tag"
    c_blue "  asset: $asset_name"

    header "Downloading"
    local tmp
    tmp=$(mktemp -d)
    trap "rm -rf '$tmp'" EXIT
    curl -fsSL \
        -H "Authorization: token $TOKEN" \
        -H "Accept: application/octet-stream" \
        -o "$tmp/sediment.tar.gz" \
        "$asset_api_url"
    local actual_size
    actual_size=$(wc -c <"$tmp/sediment.tar.gz" | tr -d ' ')
    c_blue "  downloaded: ${actual_size} bytes"

    if [[ -n "$asset_digest" ]]; then
        header "Verifying sha256"
        local actual
        if command -v sha256sum >/dev/null 2>&1; then
            actual=$(sha256sum "$tmp/sediment.tar.gz" | awk '{print $1}')
        else
            actual=$(shasum -a 256 "$tmp/sediment.tar.gz" | awk '{print $1}')
        fi
        if [[ "$actual" != "$asset_digest" ]]; then
            die "sha256 mismatch:
   expected: $asset_digest
   actual:   $actual"
        fi
        c_green "  ok: $actual"
    else
        c_yellow "  (no digest field on asset — skipping sha256 verify)"
    fi

    header "Installing"
    tar -xzf "$tmp/sediment.tar.gz" -C "$tmp"
    [[ -f "$tmp/sediment" ]] || die "tarball didn't contain 'sediment' at root"
    chmod +x "$tmp/sediment"

    local dest_dir dest
    dest_dir=$(pick_install_dir)
    dest="$dest_dir/sediment"
    if ! mv "$tmp/sediment" "$dest" 2>/dev/null; then
        # Cross-device rename or perm issue → try sudo if interactive
        if [[ -t 0 ]] && command -v sudo >/dev/null 2>&1; then
            c_yellow "  needs sudo to write $dest_dir"
            sudo mv "$tmp/sediment" "$dest"
        else
            die "Could not install to $dest. Set SEDIMENT_INSTALL_DIR=<writable> and retry."
        fi
    fi
    c_green "  installed: $dest ($("$dest" --version))"

    if [[ ":$PATH:" != *":$dest_dir:"* ]]; then
        c_yellow "  ⚠ $dest_dir is not on your \$PATH"
        c_yellow "    add this to your shell rc:  export PATH=\"$dest_dir:\$PATH\""
    fi

    install_shell_completion "$dest"

    header "Next steps"
    cat <<EOF
  1. Make sure GITHUB_TOKEN is in your shell rc so 'sediment update' works:
       echo 'export GITHUB_TOKEN=\$(gh auth token)' >> ~/.zshrc

  2. Log in:
       sediment auth login

  3. Try it:
       sediment whoami
       sediment search "research" --limit 3

  4. (optional) Wire into Claude Code via MCP shim:
       pipx install sediment-mcp-shim
       # then in any Claude Code session:
       /sediment-onboard

  Future upgrades:
       sediment update

  Tab completion is now installed for your shell — restart your terminal
  (or 'source ~/.zshrc') and try: sediment <TAB>
EOF
}

# ---------- shell completion auto-install ----------
#
# Installs completion for EVERY shell detected on PATH (not just $SHELL).
# Rationale: a user may have zsh as default but also use pwsh for scripts —
# wiring all at once means tab-completion just works wherever they end up.
install_shell_completion() {
    local bin="$1"
    # If the freshly-installed binary doesn't have the completion subcommand
    # (older release), bail with a hint.
    if ! "$bin" completion --help >/dev/null 2>&1; then
        c_yellow "  shell completion: skipped (this version doesn't ship 'sediment completion'; run 'sediment update' first)"
        return
    fi
    header "Installing shell completion"
    install_completion_zsh "$bin"
    install_completion_bash "$bin"
    install_completion_fish "$bin"
    install_completion_pwsh "$bin"
    install_completion_elvish "$bin"
}

install_completion_zsh() {
    command -v zsh >/dev/null 2>&1 || return
    local dest
    if [[ -d "$HOME/.oh-my-zsh/custom/completions" ]]; then
        dest="$HOME/.oh-my-zsh/custom/completions/_sediment"
    elif [[ -d "$HOME/.zsh/completions" ]]; then
        dest="$HOME/.zsh/completions/_sediment"
    else
        mkdir -p "$HOME/.zsh/completions"
        dest="$HOME/.zsh/completions/_sediment"
        # Make sure fpath includes it.
        if [[ -f "$HOME/.zshrc" ]] && ! grep -qE 'fpath=\(.*\.zsh/completions' "$HOME/.zshrc" 2>/dev/null; then
            cat >> "$HOME/.zshrc" <<'EOF'

# Sediment CLI tab completion
fpath=(~/.zsh/completions $fpath)
autoload -Uz compinit && compinit
EOF
        fi
    fi
    "$1" completion zsh > "$dest" 2>/dev/null && c_blue "  zsh    → $dest"
    rm -f "$HOME/.zcompdump" 2>/dev/null  # bust cache
}

install_completion_bash() {
    command -v bash >/dev/null 2>&1 || return
    local dest
    for d in /usr/local/etc/bash_completion.d /etc/bash_completion.d "$HOME/.local/share/bash-completion/completions"; do
        if [[ -d "$d" && -w "$d" ]]; then
            dest="$d/sediment"
            break
        fi
    done
    if [[ -z "${dest:-}" ]]; then
        mkdir -p "$HOME/.local/share/bash-completion/completions"
        dest="$HOME/.local/share/bash-completion/completions/sediment"
    fi
    "$1" completion bash > "$dest" 2>/dev/null && c_blue "  bash   → $dest"
}

install_completion_fish() {
    command -v fish >/dev/null 2>&1 || return
    mkdir -p "$HOME/.config/fish/completions"
    local dest="$HOME/.config/fish/completions/sediment.fish"
    "$1" completion fish > "$dest" 2>/dev/null && c_blue "  fish   → $dest"
}

install_completion_pwsh() {
    # PowerShell Core (cross-platform) installs as `pwsh`. Windows PowerShell
    # is `powershell` (rarely on macOS/Linux). The install script runs from
    # bash so we only handle pwsh here — PowerShell users on Windows can run
    # `sediment completion powershell | Out-String | Invoke-Expression` once.
    command -v pwsh >/dev/null 2>&1 || return
    # Get $PROFILE path from pwsh itself
    local profile_dir
    profile_dir=$(pwsh -NoProfile -Command 'Split-Path -Parent $PROFILE.CurrentUserAllHosts' 2>/dev/null | tr -d '\r')
    if [[ -z "$profile_dir" ]]; then
        c_yellow "  pwsh   → couldn't resolve \$PROFILE; manual: pwsh -c 'sediment completion powershell > \$PROFILE/sediment.ps1'"
        return
    fi
    mkdir -p "$profile_dir"
    local dest="$profile_dir/sediment_completion.ps1"
    "$1" completion powershell > "$dest" 2>/dev/null && c_blue "  pwsh   → $dest"
    # Wire it in by appending to the AllHosts profile if not already there.
    local profile_file="$profile_dir/profile.ps1"
    touch "$profile_file"
    if ! grep -q "sediment_completion.ps1" "$profile_file" 2>/dev/null; then
        echo ". '$dest'" >> "$profile_file"
    fi
}

install_completion_elvish() {
    command -v elvish >/dev/null 2>&1 || return
    mkdir -p "$HOME/.config/elvish/lib"
    local dest="$HOME/.config/elvish/lib/sediment.elv"
    "$1" completion elvish > "$dest" 2>/dev/null && c_blue "  elvish → $dest"
    local rc="$HOME/.config/elvish/rc.elv"
    touch "$rc"
    if ! grep -q "use sediment" "$rc" 2>/dev/null; then
        echo "use sediment" >> "$rc"
    fi
}

main "$@"
