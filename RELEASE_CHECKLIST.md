# Release Checklist

- [ ] package.nix: set `version` to the final date + SemVer release version (without `-rc`)
- [ ] CHANGELOG.md: add a release entry with the same version
- [ ] check `nix build .#default`
- [ ] git commit -a -m "Release vX.Y.Z"
- [ ] git checkout stable
- [ ] git merge main
- [ ] git tag -a vX.Y.Z
- [ ] main branch: package.nix: bump `version` for the next release and add the `-rc` suffix
- [ ] main branch: git commit -a -m "rc version"
- [ ] git push origin --all
- [ ] git push origin --tags

