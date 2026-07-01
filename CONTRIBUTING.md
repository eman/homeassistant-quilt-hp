# Contributing

## Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html):

- **PATCH** (`0.2.x`): backwards-compatible bug fixes
- **MINOR** (`0.x.0`): backwards-compatible new features
- **MAJOR** (`x.0.0`): incompatible API or breaking changes

## Changelog

All notable changes are documented in [`CHANGELOG.md`](CHANGELOG.md) following the
[Keep a Changelog 1.0.0](https://keepachangelog.com/en/1.0.0/) format.

- Add new entries under `## [Unreleased]` as you work
- Use the standard change categories: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`

## Release Process

1. Update the `version` field in `custom_components/quilt_hp/manifest.json`
2. Update `CHANGELOG.md`:
   - Rename `## [Unreleased]` to `## [x.y.z] - YYYY-MM-DD`
   - Add a new empty `## [Unreleased]` section at the top
   - Update the comparison links at the bottom of the file
3. Commit: `git commit -m "chore: release vX.Y.Z"`
4. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`

The `release.yml` GitHub Action will automatically create the GitHub release from the tag and
the corresponding `CHANGELOG.md` entry.
