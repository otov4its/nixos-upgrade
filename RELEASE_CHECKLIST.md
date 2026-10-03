# Release Checklist

This procedure releases `main` to `stable` as an annotated `vX.Y.Z` tag, then
leaves `main` prepared for the next release candidate. The package version is
set in `package.nix`.

Use these version forms:

- Release: `X.Y.Z+YYYYMMDD`; tag: `vX.Y.Z`.
- Release candidate: `X.Y.Z-rc.N+YYYYMMDD`. Start at `rc.1` for a new target
  version and increment `N` for another candidate of that same target.
- Use the date on which that version is prepared. Do not infer the next target
  version; agree it with the maintainer first.

Stop if a precondition fails, validation fails, a merge conflicts, or the
expected branch, commit, or tag is unclear. Do not resolve unexpected release
state by discarding changes, force-pushing, or moving an existing tag.

## 1. Preflight

- [ ] Confirm the release version `X.Y.Z`, release date `YYYYMMDD`, and next
      target version with the maintainer.
- [ ] From the repository root, confirm `git status --short` is empty. Do not
      stash, discard, or include someone else's changes to make it clean.
- [ ] Fetch and fast-forward the release branches; stop if either branch has
      local-only commits or cannot be fast-forwarded:

  ```sh
  git fetch origin
  git switch main
  git pull --ff-only origin main
  git switch stable
  git pull --ff-only origin stable
  git switch main
  git rev-list --left-right --count main...origin/main
  git rev-list --left-right --count stable...origin/stable
  ```

  Both `rev-list` commands must report zero commits on either side, confirming
  each local branch matches its remote branch.

- [ ] Confirm the tag is absent locally and remotely:

  ```sh
  git tag --list vX.Y.Z
  git ls-remote --tags origin refs/tags/vX.Y.Z
  ```

  Both commands must produce no output. If the tag exists, stop; never overwrite
  or move a release tag.

## 2. Prepare and validate the release on `main`

- [ ] In `package.nix`, set `version` to `X.Y.Z+YYYYMMDD`.
- [ ] In `CHANGELOG.md`, move the entries included in this release from
      `Unreleased` under `## X.Y.Z+YYYYMMDD`. Keep `## Unreleased` at the top; if
      it has no entries left, use `- Nothing yet.` Do not drop unreleased entries.
- [ ] Format and validate the exact release-version source:

  ```sh
  nix fmt -- package.nix CHANGELOG.md
  nix flake check --no-write-lock-file
  nix build --no-link --no-write-lock-file .#default
  ```

- [ ] Review `git diff --check` and `git diff -- package.nix CHANGELOG.md`.
      Stage only those release files and inspect the staged diff:

  ```sh
  git add -- package.nix CHANGELOG.md
  git diff --cached --check
  git diff --cached -- package.nix CHANGELOG.md
  ```

- [ ] Commit the reviewed release changes on `main`:

  ```sh
  git commit -m "Release vX.Y.Z"
  ```

## 3. Promote the release to `stable` and tag it

- [ ] Switch to `stable` and merge the release commit. Stop on any conflict;
      do not guess at a resolution.

  ```sh
  git switch stable
  git merge --no-edit main
  ```

- [ ] Run `nix flake check --no-write-lock-file` and
      `nix build --no-link --no-write-lock-file .#default` on the merged `stable`
      tree. Confirm `package.nix` contains `X.Y.Z+YYYYMMDD`.
- [ ] Show the exact commit to be tagged and ask the maintainer for explicit
      approval to create `vX.Y.Z` on that commit. Before tagging, recheck that the
      tag is absent locally and on `origin`.
- [ ] After approval, create and verify the annotated tag:

  ```sh
  git tag -a vX.Y.Z -m "Release vX.Y.Z"
  git show --no-patch --decorate vX.Y.Z
  ```

## 4. Prepare the next release candidate on `main`

- [ ] Switch back to `main`. Using the next target version agreed in preflight,
      set `package.nix` to `<NEXT_X.Y.Z>-rc.<N>+<YYYYMMDD>` (replace every
      placeholder). Use `N=1` for a new target; increment `N` if preparing
      another candidate for the same target.
- [ ] Build the package and inspect the version-only change:

  ```sh
  nix fmt -- package.nix
  nix build --no-link --no-write-lock-file .#default
  git diff --check
  git diff -- package.nix
  ```

- [ ] Stage only `package.nix`, inspect the staged diff, and commit the RC
      version:

  ```sh
  git add -- package.nix
  git diff --cached --check
  git diff --cached -- package.nix
  git commit -m "chore: prepare next release candidate"
  ```

## 5. Publish

- [ ] Ask the maintainer for explicit approval to push exactly `main`, `stable`,
      and `vX.Y.Z` to `origin`. Do not push all local branches or tags.
- [ ] After approval, push only those refs. Do not force-push; if the push is
      rejected or only partly succeeds, stop and report the exact remote state.

  ```sh
  git push origin main stable
  git push origin vX.Y.Z
  ```

- [ ] Verify the remote refs and compare their commit IDs with the local RC
      commit, stable release commit, and tag:

  ```sh
  git ls-remote --heads origin main stable
  git ls-remote --tags origin refs/tags/vX.Y.Z
  ```

  Confirm the peeled annotated-tag commit (`vX.Y.Z^{}`) is the stable release
  commit and `git status --short` is empty.
