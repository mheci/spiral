# Security

## Threat model

Spiral assumes:

- the GitHub repository and protected environments are trusted
- GitHub Actions runners are trusted for the duration of a build
- Debian archive keys in `debian-archive-keyring` are trusted
- the user's firmware and boot medium are not actively malicious
- the target disk is about to be erased and contains nothing worth preserving

Spiral defends against:

- a wrong or stale Debian codename
- unsigned Debian repository metadata
- downloaded Debian ISOs from untrusted mirrors, by not using them
- package installation from an unauthenticated source
- accidental disk selection
- secrets leaking into logs, process arguments or the repository
- a failed build replacing a known-good release

Spiral does not defend against:

- a compromised GitHub account or runner
- a compromised Debian archive key
- malicious firmware or hardware
- a user who confirms the wrong disk
- physical access to an unencrypted installed system

## Trust chain

1. `InRelease` for Debian Stable is fetched over HTTPS and verified with `gpgv` against `/usr/share/keyrings/debian-archive-keyring.gpg`.
2. The codename and version come from that verified file.
3. APT verifies repository Release files and package checksums inside the live build and inside the target system.
4. No Debian installation ISO is downloaded. There is no ISO checksum file to trust.
5. A local offline repository, if ever enabled, is unsigned and lives inside the ISO. Its trust comes from the ISO itself. The installer uses `[trusted=yes]` only for that local path and documents it. This path is disabled in `config/build.json`.

## Build isolation

- Builds run in an ephemeral Debian trixie container.
- Workflow permissions are read-only by default.
- Only the release job receives `contents: write`, and it runs in the `release` environment.
- Release secrets are available only to that job.
- Third-party actions are pinned to commit SHAs.
- The build does not accept secrets from pull requests.
- Release assets are drafted before publication.
- Existing release tags are never overwritten.

## Secrets

No password, passphrase, signing key or token is stored in the repository.

- The installer asks for the user password and LUKS passphrase with a no-echo prompt.
- Passwords are sent to `chpasswd` and `cryptsetup` on standard input.
- Command arguments are logged; standard input is not.
- `Secret.__repr__` and `Secret.__str__` redact the value.
- Unattended password files must be mode 0600 or stricter.
- Diagnostic logs contain no secrets.

The live account uses passwordless sudo. This is acceptable on a disposable live medium and is not carried into the installed system. The installed root account is locked and the created user is in `sudo`.

## Destructive operations

- The plan must pass validation before execution.
- All critical warnings must be acknowledged.
- The confirmation phrase includes the stable disk path.
- The engine revalidates the target disk before partitioning.
- Mounted and removable disks are refused.
- The live medium is normally detected as busy and refused as a target.
- There is no rollback claim. Partitioning and formatting are irreversible.

## Release signing

If configured, the release job signs `SHA256SUMS` with a GPG key stored only as a protected environment secret. Without that secret, releases carry SHA256 checksums but no signature. The documentation must not claim signed releases when no signature exists.

## Reporting

Open a private security advisory on the GitHub repository. Do not include exploit details, credentials or personal data in a public issue.
