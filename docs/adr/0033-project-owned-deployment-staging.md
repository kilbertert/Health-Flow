# 33. Stage service-readable deployment artifacts below the project root

Date: 2026-11-11
Status: Accepted

## Decision

Transport may use the host temporary directory, but the root deployment process
extracts the artifact beneath the project's `var/` directory before the service
identity installs its wheel. The service identity can traverse that existing
project tree. It does not need access to the transport parent's permissions.

## Alternatives considered

Making the host-wide temporary directory traversable would repair this deployment,
but change a global host boundary for one application's install. Making just the
wheel world-readable cannot repair an inaccessible ancestor. Installing as root
would avoid traversal but change the established owner of the project virtualenv.
Project-local extraction keeps the existing install identity and transport API.

## Consequences

No host-wide modes or service units change. Archive identity and content checks
remain unchanged. Successful deployment removes the extraction directory; failed
runs retain it for diagnosis. The deploy-script test checks the generated wheel
path independently from the transport path and exercises the remote block.
