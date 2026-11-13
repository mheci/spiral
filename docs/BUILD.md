# Build pipeline

## Supported path

GitHub Actions is the supported builder. A developer should not need to download Debian images or install live-build locally.

The build workflow:

1. checks out the repository with full history,
2. frees disk space on the hosted runner,
3. starts a `debian:trixie` container with the repository mounted,
4. installs Debian live-build and its dependencies,
5. discovers Debian Stable from signed `InRelease` metadata,
6. builds the ISO,
7. creates checksums, a package manifest and a build manifest,
8. validates ISO structure,
9. uploads the artifacts.

The `weekly-rebuild` workflow calls the same build every Sunday at 03:17 UTC. It publishes only when the Debian version, package manifest hash or git commit changed, unless force is requested. The validation build itself always runs.

## Manual contributor build

Use a disposable Debian trixie container. Do not build on the host if you can avoid it. The build needs root inside the container for debootstrap and chroot.

```sh
docker run --rm --privileged \
  -v "$PWD:/spiral" -w /spiral \
  -e SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)" \
  debian:trixie \
  bash -lc "./builder/scripts/install-build-deps.sh && ./builder/scripts/build-iso.sh --output /spiral/dist"
```

Requirements for that container:

- 20 GiB free disk
- network access to deb.debian.org and security.debian.org
- the repository mounted at `/spiral`

The ISO is written to `dist/` with `SHA256SUMS`, `package-manifest.txt`, `build-manifest.json`, `build-id.txt` and release notes.

## Release tag format

```
spiral-<spiral-version>-<debian-codename>-<utc-date>-<short-git-sha>
```

Example:

```
spiral-0.1.0-trixie-20261113-abc1234
```

The ISO filename adds the architecture:

```
Spiral-0.1.0-trixie-20261113-abc1234-amd64.iso
```

## Signing

No signing key belongs in the repository. If release signing is configured, provide these secrets to the protected `release` environment:

- `SPIRAL_GPG_PRIVATE_KEY`: ASCII-armored private key
- `SPIRAL_GPG_PASSPHRASE`: optional passphrase

The release job imports the key in an ephemeral runner, signs `SHA256SUMS`, and uploads `SHA256SUMS.asc`. Without those secrets, only SHA256 checksums are published.

## GitHub asset limit

A single GitHub release asset must stay below 2 GiB. The live package set is intentionally small, and `release.sh` refuses to upload an oversized ISO instead of silently failing. If a larger image is needed, use external artifact storage and document it in the release notes.

## Reproducibility

The build records:

- Spiral version
- Debian codename and version
- full package manifest with versions
- git commit and dirty state
- `SOURCE_DATE_EPOCH`
- ISO SHA256
- live-build version

Byte-for-byte reproducibility is not claimed because Debian stable repositories are mutable. A future option can pin `snapshot.debian.org` through `config/debian.json` `snapshot`, but the build script does not enable that path yet.

## Failure handling

A failed build never publishes. The release job exists only after a successful ISO job. If a new build fails, the previous GitHub release stays in place. Releases are created as drafts, populated, then published. An existing tag is never overwritten.
