# Security Policy

## Supported versions

Only the latest published Agent8088 release receives security fixes. Upgrade to
the current release before opening a report when possible.

## Reporting a vulnerability

Use this repository's GitHub Private Vulnerability Reporting form. Do not open a
public issue or include credentials, production logs, or exploit details in a
public discussion.

We aim to acknowledge reports within seven calendar days, share a remediation
plan after triage, and coordinate disclosure after affected users have a fix.

## If a credential is exposed

Revoke or rotate it immediately at its provider, remove it from repository
history and logs, then report the exposure privately. Agent8088 credentials
should be stored in its local `.env` key store or environment, never committed
to `config.txt` or source files.

## Maintainer setup

Enable **Private vulnerability reporting** in the repository's Security settings
before publishing this release.
