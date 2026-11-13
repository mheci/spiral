# Contributing

## What to change

- A new Debian package choice belongs in a profile JSON file.
- A new desktop, kernel or driver belongs in a profile JSON file plus the package-name check.
- Installer behaviour belongs in the Python package, with a unit test.
- Build or release behaviour belongs in a script or workflow, with a structural validation.

Do not add a special case for one desktop, one kernel or one release codename in installer code. Use the profiles.

## Rules

- Never claim a feature works because code exists. Update `docs/STATUS.md` with evidence.
- Never add a package name without running the Debian package check.
- Never add a secret, key or token to the repository.
- Never bypass APT authentication for official repositories.
- Keep shell scripts in strict mode and quote paths.
- Do not use `eval`, unquoted user input or shell string construction for commands. Use argv arrays.
- Keep passwords out of arguments, logs and configuration files.
- Add or update tests with behaviour changes.
- Update documentation when the implemented behaviour changes.

## Development checks

```sh
PYTHONPATH=installer python3 -m unittest discover -s tests -t . -v
python3 builder/scripts/check-packages.py --root .
```

Build the ISO through GitHub Actions or the documented Debian container. A local ISO build is a convenience, not the release path.

## Commit style

Short imperative subject. Explain why in the body when the change is not obvious. One logical change per commit.

## Pull requests

Include:

- the problem
- the smallest change that solves it
- what was run and where
- what was not run
- security or data-loss implications
- documentation changes

Use the pull request template.
