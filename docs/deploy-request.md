# Deploy request (proposal)

Status: **proposal for the owner's review.** The development environment can't deploy: it holds
no infrastructure credentials. When a change is ready to run, it ends in a deploy request that
the infrastructure side applies through its normal change loop (diff, approval, verify).

A request is a Markdown file `deploy/<yyyy-mm-dd>-<short-name>.md` in this repo, merged to
`main` by pull request. It is the only thing the infrastructure side needs to read.

```markdown
# Deploy: <short name>

- **Image:** ghcr.io/chacal/heatpump-control@sha256:<digest>   (CI-built, tests passed)
- **Commit:** <sha>
- **Mode:** shadow | live
- **Spec milestone:** M<n>

## What changes
One paragraph: new version, new behaviour.

## Needs from the infrastructure side
Each item with its reason. "None" if none.
- [ ] Config / env (names only, never values): ...
- [ ] Credentials to create in the vault: ... (scope, ACL)
- [ ] Network: source, destination, port
- [ ] Prometheus scrape target, Grafana panels or alerts (files to add)
- [ ] vcontrold command changes (`vito.xml`) with the exact XML

## Verify after deploy
Observable checks the infrastructure side runs: a metric present, an MQTT topic published, a log
line, a value in range.

## Rollback
What to do to return to the previous behaviour (previous digest; for a live writer: restore the
pump's own setpoint, and the controller's dead-man does it by itself when it stops).

## Risks
Anything that writes to the pump or exposes a service: what the worst case is and which guard
covers it.
```

Rules:

- The digest, not a tag. A request with a new identity, listening service or credential is
  flagged "needs security check" by the infrastructure side before apply.
- Shadow first: the first request for any writer has `Mode: shadow`; going live is a separate
  request after the shadow period's results are in the PR.
- Secrets never appear in the repo or the request; only their names and scopes.
