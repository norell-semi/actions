# Cadence vManager GitHub Action

Integrate [Cadence Verisium Manager](https://www.cadence.com) (vManager) into your GitHub Actions workflows via its REST API (vAPI).

This action is a port of the [Jenkins vmanager-plugin](https://plugins.jenkins.io/vmanager-plugin) and provides equivalent functionality as a composable GitHub Action step — no Java, no Jenkins dependencies, just Python 3 and the standard library.

---

## Features

- **Session Launcher** — launch one or more VSIF files on a remote vManager server.
- **Free-style vAPI Calls** — execute any vAPI REST endpoint (POST / GET / PUT / DELETE).
- **Batch & Collect Modes** — monitor sessions that were launched externally (by shell scripts, `vmgr`, etc.).
- **Session Waiting** — poll session status until completion, with configurable resolvers for every session state.
- **JUnit XML Reports** — generate JUnit-compatible XML from vManager run data, ready for artifact upload or third-party test reporters.
- **GitHub Outputs** — session IDs, status, pass/fail counts are all exposed as step outputs for downstream steps.

---

## Quick Start

### Prerequisites

| Requirement | Details |
|---|---|
| **Runner** | A self-hosted runner with network access to the vManager vAPI server. |
| **Python** | Python 3.10+ must be available on the runner (`python3`). |
| **Secrets** | Store `VAPI_URL`, `VAPI_USER`, and `VAPI_PASSWORD` as [repository secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions). |

### Minimal Example — Launch a VSIF

```yaml
- name: Run Regression
  uses: ./actions/vmanager
  with:
    mode: launcher
    vapi-url: ${{ secrets.VAPI_URL }}
    vapi-user: ${{ secrets.VAPI_USER }}
    vapi-password: ${{ secrets.VAPI_PASSWORD }}
    vsif-path: /nfs/project/regression.vsif
    wait-for-session-end: true
    generate-junit: true
```

---

## Modes of Operation

### `launcher` — Launch VSIF Sessions

Launches one or more VSIF files on the vManager server and optionally waits for the sessions to complete.

```yaml
- uses: ./actions/vmanager
  with:
    mode: launcher
    vapi-url: ${{ secrets.VAPI_URL }}
    vapi-user: ${{ secrets.VAPI_USER }}
    vapi-password: ${{ secrets.VAPI_PASSWORD }}
    vsif-path: /nfs/project/nightly.vsif
    wait-for-session-end: true
    session-timeout: 120
    generate-junit: true
    junit-output-path: results/session_runs.xml
    fail-job-unless-all-run-passed: true
```

**Multiple VSIF files** can be specified either with semicolons or via an input file:

```yaml
# Semicolon-separated
vsif-path: /nfs/a.vsif;/nfs/b.vsif;/nfs/c.vsif

# Or from a file (one path per line)
vsif-input-file: vsif_list.txt
```

### `api` — Free-style vAPI Call

Send any JSON request to any vAPI endpoint. The response is captured in the `api-output` step output and written to `vapi.output`.

```yaml
- uses: ./actions/vmanager
  with:
    mode: api
    vapi-url: ${{ secrets.VAPI_URL }}
    vapi-user: ${{ secrets.VAPI_USER }}
    vapi-password: ${{ secrets.VAPI_PASSWORD }}
    api-url: /sessions/list
    api-method: POST
    api-input: '{"filter":{},"projection":{"type":"SELECTION_ONLY","selection":["name","session_status"]}}'
```

Alternatively, load the JSON body from a file:

```yaml
    api-input-file: query.json
```

### `batch` — Monitor Pre-launched Sessions

If sessions are launched outside of this action (e.g. by a shell step using `vmgr` or `vmanager`), use batch mode to pick them up by name and monitor their progress.

```yaml
- name: Launch via shell
  run: vmgr -vsif /nfs/regression.vsif | tee sessions.txt

- uses: ./actions/vmanager
  with:
    mode: batch
    vapi-url: ${{ secrets.VAPI_URL }}
    vapi-user: ${{ secrets.VAPI_USER }}
    vapi-password: ${{ secrets.VAPI_PASSWORD }}
    sessions-input-file: sessions.txt
    wait-for-session-end: true
    generate-junit: true
```

### `collect` — Monitor Collected Sessions

Functionally identical to `batch`. Use this mode name if your flow uses the vManager "collect" methodology.

---

## Inputs Reference

### Connection

| Input | Type | Default | Required | Description |
|:------|:----:|:-------:|:--------:|:------------|
| `vapi-url` | string | — | **yes** | vManager vAPI base URL (e.g. `https://host:port/project/vapi`) |
| `vapi-user` | string | `vAPI` | no | Username for authentication |
| `vapi-password` | string | `""` | no | Password for authentication (**use a GitHub secret**) |
| `auth-required` | boolean | `true` | no | Enable Basic authentication |
| `conn-timeout` | integer | `1` | no | Connection timeout in **minutes** |
| `read-timeout` | integer | `30` | no | Read timeout in **minutes** |
| `ignore-ssl-errors` | boolean | `true` | no | Skip SSL certificate verification |

### Mode Selection

| Input | Type | Default | Required | Description |
|:------|:----:|:-------:|:--------:|:------------|
| `mode` | string | `launcher` | **yes** | Operation mode: `launcher`, `api`, `batch`, or `collect` |

### Launcher Mode

| Input | Type | Default | Required | Description |
|:------|:----:|:-------:|:--------:|:------------|
| `vsif-path` | string | `""` | conditional | Static VSIF file path(s), semicolon-separated for multiple |
| `vsif-input-file` | string | `""` | conditional | Path to a file listing VSIF paths (one per line) |
| `env-variables` | string | `""` | no | JSON object of environment variables for the session launch |
| `attr-values` | string | `""` | no | JSON array of `{name, value, type}` attribute overrides |
| `define-values` | string | `""` | no | JSON array of `{name, value}` define parameters |
| `use-user-on-farm` | boolean | `false` | no | Use Linux user credentials on the farm |
| `farm-user` | string | `""` | no | Farm username (defaults to `vapi-user`) |
| `farm-password` | string | `""` | no | Farm password (defaults to `vapi-password`) |
| `user-private-ssh-key` | boolean | `false` | no | Use stored SSH public key instead of password |
| `env-source-file` | string | `""` | no | Shell source file for env setup on the farm |
| `env-source-file-type` | string | `BSH` | no | Source file shell type: `BSH` or `CSH` |

> **Note:** Either `vsif-path` or `vsif-input-file` must be provided in launcher mode.

### API Mode

| Input | Type | Default | Required | Description |
|:------|:----:|:-------:|:--------:|:------------|
| `api-url` | string | `""` | **yes** | REST endpoint path (e.g. `/sessions/list`) |
| `api-method` | string | `POST` | no | HTTP method: `POST`, `GET`, `PUT`, or `DELETE` |
| `api-input` | string | `{}` | no | Static JSON request body |
| `api-input-file` | string | `""` | no | Path to a file containing the JSON request body |

### Batch / Collect Mode

| Input | Type | Default | Required | Description |
|:------|:----:|:-------:|:--------:|:------------|
| `sessions-input-file` | string | `""` | **yes** | Path to a file with session names (one per line) |

### Session Waiting

| Input | Type | Default | Required | Description |
|:------|:----:|:-------:|:--------:|:------------|
| `wait-for-session-end` | boolean | `true` | no | Block until all sessions reach a terminal state |
| `session-timeout` | integer | `30` | no | Max wait time in **minutes** (`0` = no timeout) |
| `poll-interval` | integer | `60` | no | Seconds between status polls |

### State Resolvers

These control what happens when a session enters a given state:

| Input | Type | Default | Description |
|:------|:----:|:-------:|:------------|
| `inaccessible-resolver` | string | `fail` | Action on **inaccessible** state |
| `stopped-resolver` | string | `fail` | Action on **stopped** state |
| `failed-resolver` | string | `fail` | Action on **failed** state |
| `done-resolver` | string | `continue` | Action on **done** state |
| `suspended-resolver` | string | `continue` | Action on **suspended** state |

Each resolver accepts one of three values:

| Value | Behavior |
|:-----:|:---------|
| `fail` | Mark the workflow step as **failed** immediately |
| `continue` | Accept the state and move on once **all** sessions have settled |
| `ignore` | Keep waiting for a different state (useful for transient states) |

### Failure Conditions

| Input | Type | Default | Description |
|:------|:----:|:-------:|:------------|
| `fail-job-if-all-run-failed` | boolean | `false` | Fail the step if **every** run in the session failed |
| `fail-job-unless-all-run-passed` | boolean | `false` | Fail the step unless **every** run passed |

### JUnit Report

| Input | Type | Default | Description |
|:------|:----:|:-------:|:------------|
| `generate-junit` | boolean | `false` | Generate a JUnit XML report from session runs |
| `junit-output-path` | string | `session_runs.xml` | Output file path for the XML report |
| `extra-attributes` | string | `""` | Comma-separated vManager run attributes to include in failure messages |
| `no-append-seed` | boolean | `false` | Omit the computed seed from test names |

---

## Outputs Reference

| Output | Description | Available In |
|:-------|:------------|:-------------|
| `session-ids` | Comma-separated list of session IDs | `launcher`, `batch`, `collect` |
| `session-status` | Final aggregated session status (`completed`, `failed`, `mixed`, …) | `launcher`, `batch`, `collect` |
| `total-runs` | Total number of runs across all sessions | `launcher`, `batch`, `collect` |
| `passed-runs` | Number of passed runs | `launcher`, `batch`, `collect` |
| `failed-runs` | Number of failed runs | `launcher`, `batch`, `collect` |
| `api-output` | JSON response body (truncated to 64 KB) | `api` |
| `api-output-file` | Path to the full response file (`vapi.output`) | `api` |
| `junit-report-path` | Path to the generated JUnit XML | `launcher`, `batch`, `collect` |

**Using outputs in subsequent steps:**

```yaml
- name: Launch
  id: vmanager
  uses: ./actions/vmanager
  with:
    mode: launcher
    # ...

- name: Check Results
  run: |
    echo "Status: ${{ steps.vmanager.outputs.session-status }}"
    echo "Passed: ${{ steps.vmanager.outputs.passed-runs }} / ${{ steps.vmanager.outputs.total-runs }}"
```

---

## Generated Files

The action writes the following files to the working directory:

| File | When | Description |
|:-----|:-----|:------------|
| `session_launch.output` | `launcher` mode | One session ID per line |
| `session_status.properties` | After waiting completes | Key-value session status (compatible with the Jenkins plugin format) |
| `session_runs.xml` | When `generate-junit: true` | JUnit XML report (path configurable via `junit-output-path`) |
| `vapi.output` | `api` mode | Full JSON response from the vAPI call |

---

## Full Workflow Examples

### Nightly Regression with JUnit Upload

```yaml
name: Nightly Regression

on:
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:
    inputs:
      vsif:
        description: 'VSIF path'
        default: '/nfs/project/ci_nightly.vsif'
      timeout:
        description: 'Timeout (minutes)'
        default: '120'

jobs:
  regression:
    runs-on: self-hosted
    timeout-minutes: 180
    steps:
      - uses: actions/checkout@v4

      - name: Run vManager Regression
        id: vmanager
        uses: ./actions/vmanager
        with:
          mode: launcher
          vapi-url: ${{ secrets.VAPI_URL }}
          vapi-user: ${{ secrets.VAPI_USER }}
          vapi-password: ${{ secrets.VAPI_PASSWORD }}
          vsif-path: ${{ inputs.vsif || '/nfs/project/ci_nightly.vsif' }}
          wait-for-session-end: true
          session-timeout: ${{ inputs.timeout || '120' }}
          poll-interval: 60
          failed-resolver: fail
          fail-job-unless-all-run-passed: true
          generate-junit: true
          junit-output-path: results/session_runs.xml

      - name: Summary
        if: always()
        run: |
          echo "### Regression Results" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Metric | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|--------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Status | ${{ steps.vmanager.outputs.session-status }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Total  | ${{ steps.vmanager.outputs.total-runs }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Passed | ${{ steps.vmanager.outputs.passed-runs }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Failed | ${{ steps.vmanager.outputs.failed-runs }} |" >> $GITHUB_STEP_SUMMARY

      - name: Upload JUnit Report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: junit-report
          path: results/session_runs.xml
```

### Monitor a Batch-launched Session

```yaml
jobs:
  regression:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4

      - name: Launch session via shell
        run: |
          # Your existing launch script writes session names to a file
          vmgr -vsif /nfs/regression.vsif > session_names.txt

      - name: Monitor & Report
        id: vmanager
        uses: ./actions/vmanager
        with:
          mode: batch
          vapi-url: ${{ secrets.VAPI_URL }}
          vapi-user: ${{ secrets.VAPI_USER }}
          vapi-password: ${{ secrets.VAPI_PASSWORD }}
          sessions-input-file: session_names.txt
          wait-for-session-end: true
          session-timeout: 90
          generate-junit: true
          fail-job-if-all-run-failed: true
```

### Launch with Environment Variables and Attribute Overrides

```yaml
- uses: ./actions/vmanager
  with:
    mode: launcher
    vapi-url: ${{ secrets.VAPI_URL }}
    vapi-user: ${{ secrets.VAPI_USER }}
    vapi-password: ${{ secrets.VAPI_PASSWORD }}
    vsif-path: /nfs/project/regression.vsif
    env-variables: '{"GIT_SHA":"${{ github.sha }}","BRANCH":"${{ github.ref_name }}"}'
    attr-values: '[{"name":"build_tag","value":"gha-${{ github.run_id }}","type":"string"}]'
    wait-for-session-end: true
    generate-junit: true
```

### Free-style API Call — List Recent Sessions

```yaml
- name: List Sessions
  id: list
  uses: ./actions/vmanager
  with:
    mode: api
    vapi-url: ${{ secrets.VAPI_URL }}
    vapi-user: ${{ secrets.VAPI_USER }}
    vapi-password: ${{ secrets.VAPI_PASSWORD }}
    api-url: /sessions/list
    api-method: POST
    api-input: |
      {
        "filter": {},
        "pageLength": 10,
        "projection": {
          "type": "SELECTION_ONLY",
          "selection": ["name", "session_status", "owner"]
        }
      }

- name: Show results
  run: cat vapi.output
```

### Launch with Farm User Credentials

```yaml
- uses: ./actions/vmanager
  with:
    mode: launcher
    vapi-url: ${{ secrets.VAPI_URL }}
    vapi-user: ${{ secrets.VAPI_USER }}
    vapi-password: ${{ secrets.VAPI_PASSWORD }}
    vsif-path: /nfs/project/regression.vsif
    use-user-on-farm: true
    farm-user: ${{ secrets.FARM_USER }}
    farm-password: ${{ secrets.FARM_PASSWORD }}
    env-source-file: /home/user/.bashrc
    env-source-file-type: BSH
    wait-for-session-end: true
```

---

## Migration from Jenkins Plugin

The table below maps Jenkins pipeline properties to their GitHub Action equivalents:

| Jenkins Property | GitHub Action Input | Notes |
|:-----------------|:-------------------|:------|
| `vAPIUrl` | `vapi-url` | |
| `vAPIUser` | `vapi-user` | |
| `vAPIPassword` | `vapi-password` | Use `${{ secrets.VAPI_PASSWORD }}` |
| `authRequired` | `auth-required` | |
| `connTimeout` | `conn-timeout` | |
| `readTimeout` | `read-timeout` | |
| `executionType` | `mode` | `launcher` → `launcher`, `batch` → `batch` |
| `vSIFName` | `vsif-path` | |
| `vSIFInputFile` | `vsif-input-file` | |
| `vsifType` | — | Use `vsif-path` for static, `vsif-input-file` for dynamic |
| `waitTillSessionEnds` | `wait-for-session-end` | |
| `stepSessionTimeout` | `session-timeout` | |
| `inaccessibleResolver` | `inaccessible-resolver` | |
| `stoppedResolver` | `stopped-resolver` | |
| `failedResolver` | `failed-resolver` | |
| `doneResolver` | `done-resolver` | |
| `suspendedResolver` | `suspended-resolver` | |
| `generateJUnitXML` | `generate-junit` | |
| `noAppendSeed` | `no-append-seed` | |
| `staticAttributeList` | `extra-attributes` | |
| `failJobIfAllRunFailed` | `fail-job-if-all-run-failed` | |
| `failJobUnlessAllRunPassed` | `fail-job-unless-all-run-passed` | |
| `sessionsInputFile` | `sessions-input-file` | |
| `useUserOnFarm` | `use-user-on-farm` | |
| `userPrivateSSHKey` | `user-private-ssh-key` | |
| `envSourceInputFile` | `env-source-file` | |
| `envSourceInputFileType` | `env-source-file-type` | |
| `envVarible` / `envVaribleFile` | `env-variables` | Pass JSON directly instead of a file reference |
| `attrValues` / `attrValuesFile` | `attr-values` | Pass JSON directly |
| `defineVarible` / `defineVaribleFile` | `define-values` | Pass JSON directly |
| `apiUrl` | `api-url` | |
| `requestMethod` | `api-method` | |
| `vAPIInput` | `api-input` | |
| `vJsonInputFile` | `api-input-file` | |

### Key Differences from the Jenkins Plugin

| Area | Jenkins Plugin | GitHub Action |
|:-----|:---------------|:--------------|
| **Language** | Java (Maven, `.hpi`) | Python 3 (zero dependencies) |
| **Environment** | Runs inside Jenkins JVM | Runs as a composite step on any GitHub runner |
| **Credentials** | Jenkins Credentials store or plain text | GitHub Secrets (`${{ secrets.* }}`) |
| **Env / Attr files** | Separate file paths + boolean toggles | Direct JSON strings in the workflow YAML |
| **Dashboard / UI** | Jenkins sidebar links, dashboard portlet | GitHub Step Summary + artifact uploads |
| **Build archiving** | Jenkins `RunListener` for session deletion | Not ported — use a separate cleanup workflow if needed |
| **Summary Report** | Embedded HTML via post-build action | Not ported — use the `api` mode to call `/reports/generate-summary-report` directly |
| **Token Macros** | Jenkins `TokenMacro.expandAll()` | Use native GitHub Actions expressions (`${{ }}`) |
| **Hybrid mode** | Plugin launches shell then monitors | Use separate shell + `batch` mode steps |

---

## Architecture

```
actions/vmanager/
├── action.yml        # GitHub Action metadata — inputs, outputs, composite run definition
└── vmanager.py       # Self-contained Python implementation (stdlib only, no pip install)
```

The action runs as a [composite action](https://docs.github.com/en/actions/sharing-automations/creating-actions/creating-a-composite-action). All inputs are passed to `vmanager.py` via environment variables. The script uses only the Python 3 standard library (`urllib`, `json`, `ssl`, `xml.sax.saxutils`) — no external packages are required.

---

## License

MIT — see [LICENSE](../../vmanager-plugin/LICENSE) for details.

