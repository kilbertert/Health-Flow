# 33. Stage service-readable artifacts under an administrator-owned parent

Date: 2026-11-11
Status: Accepted

## Decision

Transport may use the host temporary directory, but the root deployment process
extracts the artifact beneath the administrator-owned artifact directory next to the backup directory before the
service identity installs its wheel. The parent must not be a symlink, must be
owned by the deploying identity, and must not be group/other writable. The service
identity can traverse this tree but cannot replace the staging directory. The
existing backup directory can remain service-owned; artifacts use a separately
validated parent. The service needs no access to the transport parent.

## Alternatives considered

Making the host-wide temporary directory traversable would repair this deployment,
but change a global host boundary for one application's install. Making just the
wheel world-readable cannot repair an inaccessible ancestor. Installing as root
would avoid traversal but change the established owner of the project virtualenv.
Extraction below the protected backup directory keeps the existing install identity and transport API.

## Consequences

No host-wide modes or service units change. Archive identity and content checks
remain unchanged. Successful deployment removes the extraction directory; failed
runs retain it for diagnosis. The deploy-script test checks the generated wheel
path independently from the transport path and exercises the remote block.
