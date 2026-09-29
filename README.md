# grail-vault

Verified copies of the RAPP Brainstem grail, kept outside the grail.

The grail ([kody-w/rapp-installer](https://github.com/kody-w/rapp-installer)) is the kernel. This repo never
changes it. Every hour it reads the grail's newest commit, copies it, and checks it. A copy that passes becomes
the **last known good**. A copy that fails is recorded, an issue is opened, and the last known good stays where it was.
So a bad push to the grail, or the grail being unreachable, never takes the working kernel with it.

## Recover

If the normal installer is broken, install the last known good copy instead:

```bash
curl -fsSL https://kody-w.github.io/grail-vault/install.sh | bash
```

It downloads the copy from a vault mirror, checks its SHA-256, and runs that copy's own installer, unchanged.
Pin a specific copy with `VAULT_SNAPSHOT=0.6.16-0e43ee5`. Try a specific mirror first with `VAULT_URL=<url>`.

## For agents

`https://kody-w.github.io/grail-vault/beacon.json` (also at `/.well-known/rapp-grail.json`) is one
machine-readable answer to "where is the last known good kernel, and how do I check it?": its SHA-256 hashes,
every place to fetch it, its permanent Software Heritage ID, and a non-interactive install command.
Trust comes from the hashes, not from whichever host served the bytes.

## What "known good" means

A copy passes when:
1. the grail's `install.sh` parses;
2. the kernel's own test suite passes;
3. the kernel boots, `/health` reports the version in its `VERSION` file, and `/chat` refuses an empty message.

## What is stored

`docs/snapshots/<version>-<commit>/`, one folder per grail commit, never overwritten:

| File | What it is |
|---|---|
| `grail.bundle` | The full git history up to that commit. `git clone grail.bundle` works. |
| `grail.tar.gz` | The plain files at that commit. |
| `manifest.json` | Commit, version, time, SHA-256 of both files, and each gate's result. |

`docs/ledger.json` lists every commit ever seen. `docs/latest.json` names the last known good.
`python3 vault.py verify` re-checks every stored copy against its manifest.

## Mirrors

| Mirror | Status |
|---|---|
| https://kody-w.github.io/grail-vault | live |
| https://kodyw.com/grail | not yet |
| Software Heritage | every known good commit, e.g. `swh:1:rev:0e43ee580e78c150b1c59002456822d2e779388e` |
