# Pi Skills

The `~/.pi` repository: pi agent configuration and the `dev-*` skill suite under `agent/skills/`. Its domain is the vocabulary skills use to coordinate work between the main agent ("herder") and background leaf agents.

The glossary also distinguishes the Grafana environments accessed through the agent.

## Language

**Findings file**:
The single cited Markdown deliverable of a `dev-research` run. It always lives inside the project repo, at whatever path that repo's convention keeps such notes — if the repo has no convention, somewhere sensible in the repo, with the location stated. Never in pi-internal storage such as `~/.pi/herd/`.
_Avoid_: research notes file, result file, output file

**Close policy**:
What happens to a herd's herdr workspace — and the leaf agents inside it — when the run finishes. `never` keeps the leaves alive so the human can inspect or attach to them; `on_success` closes automatically when every leaf succeeded, and stays open for debugging when any leaf failed; `always` closes regardless of outcome. Each skill that fans out declares its own policy, and the human can always tell the herder to keep the leaves open.
_Avoid_: auto-close, teardown, kill the leaves

### Shared browser access

**Shared automation browser**:
The dedicated, persistent browser used by Pi sessions and herd leaves, with separate working tabs but shared website login state. It is separate from the human's everyday browser.
_Avoid_: personal browser, browser session (when referring to the shared automation browser)

**Agent-owned tab**:
An automation tab assigned to one Pi session or herd leaf; only that owner may inspect or operate on it unless ownership is explicitly transferred. A herder does not implicitly own its leaves' tabs.
_Avoid_: shared tab, selected tab (when referring to ownership)

### Grafana access

**Company Grafana connection**:
The existing connection used to access the company's Grafana environment. It remains separate from the additional Grafana Cloud connection.
_Avoid_: Grafana connection (without a qualifier)

**Grafana Cloud connection**:
The additional connection to a Grafana Cloud stack for interactive use, authorized as the signed-in Grafana user. It does not replace the Company Grafana connection.
_Avoid_: Company Grafana connection

**Grafana Cloud stack**:
A distinct hosted Grafana environment containing its own dashboards and data sources. It is the target of a Grafana Cloud connection, not the connection itself.
_Avoid_: MCP server, Grafana account

**Grafana Read and Query access**:
Permission to inspect Grafana resources and query data sources, without a grant to change Grafana resources. Query access does not itself guarantee that a data source rejects mutations.
_Avoid_: Strict read-only access

**Grafana Write access**:
Permission for a connection to use operations that modify Grafana resources, such as dashboards or alert rules. This capability is distinct from a request to make a particular change.
_Avoid_: Automatic approval for changes
