# Release Checklist

- [ ] flake.nix: remove "-rc" suffix in `semVer`
- [ ] flake.nix: bump the version (`dateVer` and `semVer`)
- [ ] CHANGELOG.md: update
- [ ] check if `nix build`
- [ ] git commit -a -m "Release vX.Y.Z"
- [ ] git merge into stable branch
- [ ] git tag -a vX.Y.Z
- [ ] git push branches and tags
