# Release Checklist

- [ ] flake.nix: bump the version (`dateVer` and `semVer`)
- [ ] flake.nix: remove "-rc" suffix in `semVer`
- [ ] CHANGELOG.md: update
- [ ] check if `nix build`
- [ ] git commit -a -m "Release vX.Y.Z"
- [ ] git merge into stable branch
- [ ] git tag -a vX.Y.Z
- [ ] main branch: flake.nix: bump `semVer` and add "-rc" suffix
- [ ] main branch: git commit -a -m "rc version"
- [ ] git push branches and tags

