# Release Checklist

- [ ] flake.nix: bump the version `dateVer`
- [ ] flake.nix: remove "-rc" suffix in `semVer`
- [ ] CHANGELOG.md: update
- [ ] check if `nix build`
- [ ] git commit -a -m "Release vX.Y.Z"
- [ ] git checkout stable
- [ ] git merge main
- [ ] git tag -a vX.Y.Z
- [ ] main branch: flake.nix: bump `semVer` and add "-rc" suffix
- [ ] main branch: git commit -a -m "rc version"
- [ ] git push origin --all
- [ ] git push origin --tags

