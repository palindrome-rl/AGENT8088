# Contributing to Agent8088

Thanks for helping improve Agent8088. This page is the short version; the
[full contribution guide](docs/wiki/14-contributing.md) covers the details.

## Ground rules

- Be respectful. Everyone taking part is expected to follow the
  [Code of Conduct](CODE_OF_CONDUCT.md).
- Report security issues privately, never in a public issue. See
  [SECURITY.md](SECURITY.md).
- Open an issue before a large change, so the approach can be agreed first.

## Get set up

```sh
git clone --branch AGENT8088-v1.2 https://github.com/palindrome-rl/AGENT8088.git
cd AGENT8088
uv sync --all-extras
```

Never run the agent against your real `~/.agent8088` while developing. Give
every run a throwaway home:

```sh
export HOME="$(mktemp -d)" AGENT8088_HOME="$HOME/.agent8088"
uv run agent8088 --version
```

## Make a change

1. Branch from `AGENT8088-v1.2` and open your pull request against it.
2. Keep each pull request focused on one change.
3. Update the docs in `docs/wiki/` when behaviour changes. The GitHub wiki is
   generated from those files automatically, so do not edit it directly.

## Run the checks

These are the same checks CI runs on every push and pull request:

```sh
uvx ruff check --select E9,F63,F7,F82 src scripts
python scripts/check_duplicate_defs.py
python -m compileall -q src scripts
bash scripts/check_installer_portability.sh
```

For behaviour changes, also run the feature checks in a throwaway home and
compare the result with the same run on `AGENT8088-v1.2`:

```sh
HOME="$(mktemp -d)" AGENT8088_HOME="$(mktemp -d)" uv run python scripts/verify_features.py
```

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>: <short summary in the imperative mood>
```

| Type | Use it for |
|---|---|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `refactor` | A code change that neither fixes a bug nor adds a feature |
| `test` | Adding or correcting checks |
| `ci` | CI and automation |
| `chore` | Maintenance, dependencies, releases |

Examples: `fix: keep the approval prompt open after EOF`,
`docs: document the /local command`.

## Pull request checklist

The pull request template lists what reviewers look for: what changed, how you
verified it, and anything you deliberately left out.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
