# Repository context (optional)

Install into the same Python environment as Agent8088:

```sh
python -m pip install 'agent8088[repository]'
```

For an editable checkout use `python -m pip install -e '.[repository]'`.
Restart the agent after installation. No public digest service receives your
repository; GitIngest runs locally. Selected evidence is sent to your configured
model, like ordinary file reads. This is not a guarantee of secret detection.

`repository_read` is available through the shared agent tool runtime:

- `source`: permitted local directory or HTTPS GitHub owner/repository URL.
- `action=overview`: bounded file manifest and GitIngest summary.
- `action=search`, `query`: literal, case-insensitive search; first 12 matches.
- `action=read`, `path`: exact included path, with character continuation cursors.
- `snapshot_id`: rejects changed selected content or filters; not a Git commit ID.
- `include`, `exclude`: case-sensitive relative-path globs.
- `revision`: branch/tag for a remote repository. Commit SHA selection is not supported.

Start with overview, then search and read only relevant files. A manifest is not
an analysis of every file. Search is explicitly partial. Repository instructions
are untrusted evidence, not authority for actions.

## Boundaries

Protected paths, links/junctions and hardlinked files are excluded before the
formatter sees content. GitIngest defaults and nested ignore files are applied
conservatively: a nested negation cannot restore an excluded parent. Binary,
non-UTF-8, oversized and very deep paths are omitted with counts. Limits are
256 KiB/file, 8 MiB selected text, 10,000 scanned entries and 20 directory levels.
Scans have a 20-second deadline; formatter subprocesses have a 30-second deadline.

Remote acquisition is separately permission-gated and restricted to GitHub HTTPS.
It uses a temporary shallow clone, no submodules, disabled hooks, noninteractive
authentication and a 60-second deadline. Existing `gh` credentials can be used;
never put credentials in a URL. Disk growth is checked periodically against
128 MiB, not enforced by a filesystem quota. Each remote call reacquires the
checkout; temporary checkouts are removed afterward. Narrow local checkouts are
preferable for repeated exploration of large repositories.

Overview formatting is cached in memory for up to 16 content identities. Files
are rescanned on each call to detect edits and reapply protection. This deliberately
does not introduce a persistent source-code store. Token counts in the GitIngest
summary are estimates, not exact GLM token usage.

Progress and interruption use the shared agent callbacks. No automatic GitIngest
installation or permission-default change is introduced.
