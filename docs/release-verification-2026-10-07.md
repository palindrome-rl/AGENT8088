# Public v1.2 reliability update verification

Verified on 2026-10-07 before publication.

## Scope and preservation

Backported runtime, Web UI and container improvements from internal staging
snapshot `3fc84950e2ed`, onto public release snapshot `c15c12f707bc`.
The internal development and staging branches were not modified.

The public branch retains its MIT license, package version 1.2.0, public
repository URLs, `AGENT8088-v1.2` update channel, installer checkout safeguards,
and public wiki page names. Maintainer root tests, local configuration, private
development instructions and benchmark datasets are not published.

The installer additionally fixes the OpenCodeReview version-variable scope,
captures download errors, retries transient network failures at most three
times, and verifies the executable's pinned version before recording success.
Docker is not required to install OpenCodeReview.

## Verification performed

- Runtime regression matrix: 597 passed, 44 skipped, 3 deselected.
- Linux shipped installer/runtime checks: 97 passed, 95 platform/optional skips.
- Windows shipped installer/runtime checks: 133 passed, 57 platform/optional
  skips, including missing version
  pin, missing npm, wrong executable version, DNS recovery, exhausted retries,
  permission errors, timeouts and successful reinstalls.
- A real Windows npm installation in a new directory containing spaces downloaded
  and executed OpenCodeReview v1.12.1 successfully.
- Built and installed the public v1.2.0 wheel in an isolated environment;
  verified its CLI version, runtime imports and public update channel without
  the maintainer root tests or local working configuration.
- Web frontend dependency installation and production build passed.
- Chromium acceptance covered chat, tools, configuration, Doctor, capability
  status expansion and a 390-pixel mobile viewport. Screenshots were inspected;
  the Doctor table was made responsive after finding mobile clipping.
- Wiki dry run converted 18 canonical pages without broken links.
- Syntax/undefined-name lint, duplicate definitions, shell syntax, installer
  portability, dependency lock validation and metadata preservation checked.

## Boundaries

The three deselected maintainer assertions assume an uncapped initial completion
budget, blocking background memory saves, or search being the first capability
row. Shipped replacement checks verify context-bounded recovery, explicit-memory
request durability and lookup of search alongside other limited capabilities.

Platform skips are not passes. macOS execution is delegated to the existing CI
matrix; Linux portability checks are not a substitute for macOS execution.
Browser checks used a real local Agent8088 server but did not submit live model
requests. This update does not claim every possible model/provider scenario has
been verified. Hosted CI and wiki publication outcomes must be checked after
pushing the commit.
