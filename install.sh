#!/bin/bash
# Recovery install of the RAPP Brainstem from the grail vault.
#
#   curl -fsSL https://kody-w.github.io/grail-vault/install.sh | bash
#
# Use this when the normal installer is broken or GitHub is unreachable. It downloads the last
# known good copy of the grail from a vault mirror, checks its SHA-256 against the vault's manifest,
# and runs that copy's own installer, unchanged. Git is pointed at the verified copy instead of
# github.com for this run only (url.insteadOf, set in the environment), so the install ends up
# exactly as the grail made it, with its origin still at GitHub for later updates.
#
# Options (environment):
#   VAULT_URL=<base>          try this mirror first
#   VAULT_SNAPSHOT=<name>     install a specific snapshot, e.g. 0.6.16-0e43ee5, instead of the latest
# Every argument is passed to the grail's installer (e.g. --no-launch).
set -euo pipefail

GRAIL_URL="https://github.com/kody-w/rapp-installer.git"
MIRRORS="${VAULT_URL:-} https://kodyw.com/grail https://kody-w.github.io/grail-vault"

say() { printf '  %s\n' "$*" >&2; }
die() { printf '  ✗ %s\n' "$*" >&2; exit 1; }

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# Read one string field from our own small JSON files: "key": "value"
field() { sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" "$2" | head -1; }

main() {
    command -v git >/dev/null 2>&1 || die "git is required."
    command -v curl >/dev/null 2>&1 || die "curl is required."
    work=$(mktemp -d "${TMPDIR:-/tmp}/grail-vault-XXXXXX")
    trap 'rm -rf "$work"' EXIT

    base=""
    for mirror in $MIRRORS; do
        if curl -fsSL --max-time 20 "$mirror/latest.json" -o "$work/latest.json" 2>/dev/null \
                && grep -q '"commit"' "$work/latest.json"; then
            base="$mirror"; break
        fi
    done
    [ -n "$base" ] || die "No vault mirror answered. Tried: $MIRRORS"

    name="${VAULT_SNAPSHOT:-$(field name "$work/latest.json")}"
    [ -n "$name" ] || die "The vault at $base has no known good snapshot."
    say "Vault: $base"
    say "Snapshot: $name"

    curl -fsSL --max-time 60 "$base/snapshots/$name/manifest.json" -o "$work/manifest.json" \
        || die "Snapshot $name is not in the vault at $base."
    grep -q '"known_good": true' "$work/manifest.json" \
        || die "Snapshot $name did not pass the vault's gates; refusing to install it."
    expected=$(tr -d '\n ' < "$work/manifest.json" | sed -n 's/.*"grail.bundle":{"sha256":"\([0-9a-f]*\)".*/\1/p')
    commit=$(field commit "$work/manifest.json")
    [ -n "$expected" ] && [ -n "$commit" ] || die "The manifest for $name is unreadable."

    curl -fsSL --max-time 300 "$base/snapshots/$name/grail.bundle" -o "$work/grail.bundle" \
        || die "Could not download $name."
    actual=$(sha256_of "$work/grail.bundle")
    [ "$actual" = "$expected" ] || die "SHA-256 mismatch for $name (expected $expected, got $actual). Not installing."
    say "Verified SHA-256 $expected"

    git clone --quiet "$work/grail.bundle" "$work/grail" || die "The snapshot is not a readable git bundle."
    git -C "$work/grail" checkout --quiet "$commit" || die "Commit $commit is missing from the snapshot."
    say "Installing the grail at ${commit:0:7} with its own installer"

    # For this run only, every git operation on the grail's URL reads the verified copy instead.
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0="url.$work/grail.bundle.insteadOf"
    export GIT_CONFIG_VALUE_0="$GRAIL_URL"
    bash "$work/grail/install.sh" "$@"
}

main "$@"
