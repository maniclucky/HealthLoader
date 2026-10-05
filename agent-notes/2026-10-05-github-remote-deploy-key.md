# GitHub remote + deploy key

- Remote: `origin` = `git@github-healthloader:maniclucky/HealthLoader.git` (public repo).
- `github-healthloader` is an SSH host alias in `~/.ssh/config` pointing at github.com with
  `IdentityFile ~/.ssh/healthloader_deploy` and `IdentitiesOnly yes`. The default key
  `~/.ssh/id_ed25519` is a deploy key for a different repo (maniclucky/FitApp), so plain
  `git@github.com:` URLs will authenticate as the wrong repo and fail.
- The repo is public: personal analysis outputs (`*.csv` outside `tests/fixtures/`, `*.ipynb`,
  `targetSettings.json`) are gitignored. `targetSettings.json` is read by the daily.csv gap-fill,
  so a fresh clone must create its own.
