#!/usr/bin/env python3
"""
Cadence vManager GitHub Action

Ports the Jenkins vmanager-plugin to a GitHub Action, providing integration with
Cadence Verisium Manager over REST API (vAPI).

Supports:
  - Session launcher mode (launch VSIF files)
  - Generic API call mode (POST/GET/PUT/DELETE)
  - Batch / Collect mode (monitor pre-launched sessions)
  - Session waiting with configurable state resolvers
  - JUnit XML report generation
  - Session status tracking via GitHub Action outputs
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tobool(value: str) -> bool:
    """Convert a string input to a boolean."""
    return value.strip().lower() in ("true", "1", "yes")


def env(name: str, default: str = "") -> str:
    """Read an environment variable with a default."""
    return os.environ.get(name, default)


def log(msg: str) -> None:
    """Print a timestamped log message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[vManager] ({ts}) {msg}", flush=True)


def log_group_start(title: str) -> None:
    print(f"::group::{title}", flush=True)


def log_group_end() -> None:
    print("::endgroup::", flush=True)


def set_output(name: str, value: str) -> None:
    """Set a GitHub Actions output variable."""
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as f:
            # Handle multi-line values
            if "\n" in value:
                import uuid
                delimiter = f"ghadelimiter_{uuid.uuid4()}"
                f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{name}={value}\n")
    else:
        # Fallback for local testing
        print(f"::set-output name={name}::{value}", flush=True)


def fail(msg: str) -> None:
    """Fail the action with an error message."""
    print(f"::error::{msg}", flush=True)
    sys.exit(1)


def warn(msg: str) -> None:
    """Emit a warning."""
    print(f"::warning::{msg}", flush=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config:
    """Parsed action inputs from environment variables."""

    def __init__(self):
        self.mode = env("INPUT_MODE", "launcher")
        self.vapi_url = env("INPUT_VAPI_URL").rstrip("/")
        self.vapi_user = env("INPUT_VAPI_USER", "vAPI")
        self.vapi_password = env("INPUT_VAPI_PASSWORD", "")
        self.auth_required = tobool(env("INPUT_AUTH_REQUIRED", "true"))
        self.conn_timeout = int(env("INPUT_CONN_TIMEOUT", "1")) * 60  # seconds
        self.read_timeout = int(env("INPUT_READ_TIMEOUT", "30")) * 60  # seconds
        self.ignore_ssl = tobool(env("INPUT_IGNORE_SSL_ERRORS", "true"))

        # Launcher
        self.vsif_path = env("INPUT_VSIF_PATH", "")
        self.vsif_input_file = env("INPUT_VSIF_INPUT_FILE", "")
        self.env_variables = env("INPUT_ENV_VARIABLES", "")
        self.attr_values = env("INPUT_ATTR_VALUES", "")
        self.define_values = env("INPUT_DEFINE_VALUES", "")
        self.use_user_on_farm = tobool(env("INPUT_USE_USER_ON_FARM", "false"))
        self.farm_user = env("INPUT_FARM_USER", "")
        self.farm_password = env("INPUT_FARM_PASSWORD", "")
        self.user_private_ssh_key = tobool(env("INPUT_USER_PRIVATE_SSH_KEY", "false"))
        self.env_source_file = env("INPUT_ENV_SOURCE_FILE", "")
        self.env_source_file_type = env("INPUT_ENV_SOURCE_FILE_TYPE", "BSH")

        # API
        self.api_url = env("INPUT_API_URL", "")
        self.api_method = env("INPUT_API_METHOD", "POST")
        self.api_input = env("INPUT_API_INPUT", "{}")
        self.api_input_file = env("INPUT_API_INPUT_FILE", "")

        # Batch / Collect
        self.sessions_input_file = env("INPUT_SESSIONS_INPUT_FILE", "")

        # Wait
        self.wait = tobool(env("INPUT_WAIT_FOR_SESSION_END", "true"))
        self.session_timeout = int(env("INPUT_SESSION_TIMEOUT", "30")) * 60  # seconds
        self.poll_interval = int(env("INPUT_POLL_INTERVAL", "60"))  # seconds

        # State resolvers
        self.inaccessible_resolver = env("INPUT_INACCESSIBLE_RESOLVER", "fail")
        self.stopped_resolver = env("INPUT_STOPPED_RESOLVER", "fail")
        self.failed_resolver = env("INPUT_FAILED_RESOLVER", "fail")
        self.done_resolver = env("INPUT_DONE_RESOLVER", "continue")
        self.suspended_resolver = env("INPUT_SUSPENDED_RESOLVER", "continue")
        self.fail_if_all_failed = tobool(env("INPUT_FAIL_JOB_IF_ALL_RUN_FAILED", "false"))
        self.fail_unless_all_passed = tobool(env("INPUT_FAIL_JOB_UNLESS_ALL_RUN_PASSED", "false"))

        # JUnit
        self.generate_junit = tobool(env("INPUT_GENERATE_JUNIT", "false"))
        self.junit_output_path = env("INPUT_JUNIT_OUTPUT_PATH", "session_runs.xml")
        self.extra_attributes = env("INPUT_EXTRA_ATTRIBUTES", "")
        self.no_append_seed = tobool(env("INPUT_NO_APPEND_SEED", "false"))


# ---------------------------------------------------------------------------
# vAPI HTTP Client
# ---------------------------------------------------------------------------

class VAPIClient:
    """Low-level HTTP client for Cadence Verisium Manager vAPI."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._ssl_ctx = self._build_ssl_context()

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if self.cfg.ignore_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _auth_header(self) -> str:
        cred = f"{self.cfg.vapi_user}:{self.cfg.vapi_password}"
        encoded = b64encode(cred.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def request(self, path: str, method: str = "POST", body: str | None = None) -> dict | list | str:
        """
        Make an HTTP request to the vAPI server.

        Parameters
        ----------
        path : str
            REST path appended to the base URL  (e.g. ``/rest/sessions/launch``).
        method : str
            HTTP method.
        body : str | None
            JSON body string for POST / PUT.

        Returns
        -------
        Parsed JSON (dict or list) on success.

        Raises
        ------
        VAPIError on HTTP errors.
        """
        url = f"{self.cfg.vapi_url}{path}"
        headers = {}
        if method in ("POST", "PUT"):
            headers["Content-Type"] = "application/json"
        if self.cfg.auth_required:
            headers["Authorization"] = self._auth_header()

        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(
                req, timeout=max(self.cfg.conn_timeout, self.cfg.read_timeout), context=self._ssl_ctx
            ) as resp:
                raw = resp.read().decode("utf-8")
                if not raw.strip():
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise VAPIError(exc.code, err_body) from exc
        except urllib.error.URLError as exc:
            raise VAPIError(0, str(exc.reason)) from exc

    # ---- Convenience wrappers ----

    def check_connection(self) -> None:
        """Verify we can reach the vAPI server."""
        log("Testing connection to vManager vAPI...")
        result = self.request("/rest/sessions/count", "POST", "{}")
        if isinstance(result, dict) and "count" in result:
            log(f"Connection OK – {result['count']} sessions on the server.")
        else:
            log(f"Connection response: {result}")

    def launch_vsif(self, vsif: str, extra_json: str = "") -> str:
        """Launch a single VSIF and return the session ID."""
        payload = f'{{"vsif":"{vsif}"'
        if extra_json:
            payload += f",{extra_json}"
        payload += "}"
        log(f"Launching VSIF: {vsif}")
        result = self.request("/rest/sessions/launch", "POST", payload)
        session_id = str(result.get("value", "")) if isinstance(result, dict) else ""
        if not session_id:
            raise VAPIError(0, f"No session ID returned for VSIF {vsif}. Response: {result}")
        log(f"  → Session ID: {session_id}")
        return session_id

    def get_session_status(self, session_id: str) -> dict:
        """Query the status of a session by ID."""
        body = json.dumps({
            "filter": {
                "attName": "id",
                "operand": "EQUALS",
                "@c": ".AttValueFilter",
                "attValue": session_id,
            },
            "projection": {
                "type": "SELECTION_ONLY",
                "selection": ["session_status", "name", "running", "waiting",
                              "total_runs_in_session", "passed_runs", "failed_runs",
                              "other_runs", "owner"],
            },
        })
        result = self.request("/rest/sessions/list", "POST", body)
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        return {}

    def get_session_ids_by_names(self, names: list[str]) -> list[str]:
        """Look up session IDs given session names."""
        chain = []
        for name in names:
            chain.append({
                "attName": "name",
                "operand": "EQUALS",
                "@c": ".AttValueFilter",
                "attValue": name.strip(),
            })
        body = json.dumps({
            "filter": {
                "@c": ".ChainedFilter",
                "condition": "OR",
                "chain": chain,
            },
            "pageLength": 10000,
            "settings": {"write-hidden": True, "stream-mode": False},
            "projection": {
                "type": "SELECTION_ONLY",
                "selection": ["name", "id"],
            },
        })
        result = self.request("/rest/sessions/list", "POST", body)
        ids = []
        if isinstance(result, list):
            for item in result:
                if "id" in item:
                    ids.append(str(item["id"]))
                    log(f"  Found session ID {item['id']} for name '{item.get('name', '?')}'")
        return ids

    def get_runs(self, session_ids: list[str], extra_attrs: list[str] | None = None) -> list[dict]:
        """Fetch run details for given session IDs."""
        selection = [
            "test_name", "status", "duration", "test_group",
            "computed_seed", "id", "first_failure_name", "first_failure_description",
        ]
        if extra_attrs:
            for attr in extra_attrs:
                attr = attr.strip()
                if attr and attr not in selection:
                    selection.append(attr)

        all_runs = []
        for sid in session_ids:
            body = json.dumps({
                "filter": {
                    "condition": "AND",
                    "@c": ".ChainedFilter",
                    "chain": [{
                        "@c": ".RelationFilter",
                        "relationName": "session",
                        "filter": {
                            "condition": "AND",
                            "@c": ".ChainedFilter",
                            "chain": [{
                                "@c": ".InFilter",
                                "attName": "id",
                                "operand": "IN",
                                "values": [sid],
                            }],
                        },
                    }],
                },
                "pageLength": 100000,
                "settings": {"write-hidden": True, "stream-mode": True},
                "projection": {
                    "type": "SELECTION_ONLY",
                    "selection": selection,
                },
            })
            try:
                result = self.request("/rest/runs/list", "POST", body)
                if isinstance(result, list):
                    all_runs.extend(result)
            except VAPIError as e:
                warn(f"Failed to fetch runs for session {sid}: {e}")
        return all_runs

    def get_run_attribute_labels(self, attrs: list[str]) -> dict[str, str]:
        """Get display labels for run attributes from the schema."""
        labels = {}
        try:
            result = self.request(
                "/rest/$schema/response?action=list&component=runs&extended=true", "GET"
            )
            if isinstance(result, dict) and "items" in result:
                items = result["items"]
                if isinstance(items, str):
                    items = json.loads(items)
                props = items.get("properties", {})
                if isinstance(props, str):
                    props = json.loads(props)
                for attr in attrs:
                    attr = attr.strip()
                    if attr in props:
                        prop = props[attr]
                        if isinstance(prop, str):
                            prop = json.loads(prop)
                        labels[attr] = prop.get("title", attr)
        except Exception as e:
            warn(f"Failed to fetch attribute labels: {e}")
        return labels

    def suspend_sessions(self, session_ids: list[str]) -> None:
        """Suspend (pause) sessions."""
        body = json.dumps({
            "filter": {
                "@c": ".InFilter",
                "attName": "id",
                "operand": "IN",
                "values": session_ids,
            }
        })
        try:
            self.request("/rest/sessions/suspend", "POST", body)
            log("Sessions suspended.")
        except VAPIError as e:
            warn(f"Failed to suspend sessions: {e}")


class VAPIError(Exception):
    """Represents an HTTP error from the vAPI server."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"HTTP {code}: {message}")


# ---------------------------------------------------------------------------
# Session Waiting
# ---------------------------------------------------------------------------

class SessionWaiter:
    """Wait for sessions to reach terminal states, applying resolver logic."""

    def __init__(self, client: VAPIClient, cfg: Config, session_ids: list[str]):
        self.client = client
        self.cfg = cfg
        self.session_ids = list(session_ids)
        self.remaining = list(session_ids)
        self.session_names: dict[str, str] = {}
        # Double-check map for rerun detection (same as Jenkins plugin logic)
        self.completed_last_check: dict[str, bool] = {}
        self.final_state: dict[str, bool] = {}

    def _get_resolver(self, state: str) -> str:
        resolvers = {
            "inaccessible": self.cfg.inaccessible_resolver,
            "stopped": self.cfg.stopped_resolver,
            "failed": self.cfg.failed_resolver,
            "done": self.cfg.done_resolver,
            "suspended": self.cfg.suspended_resolver,
            "completed": "continue",
        }
        return resolvers.get(state, "ignore")

    def _check_all_done(self, session_id: str) -> bool:
        """Check if session is truly done (second consecutive check with no running/waiting runs)."""
        if self.final_state.get(session_id):
            if session_id in self.remaining:
                self.remaining.remove(session_id)
        return len(self.remaining) == 0

    def wait(self) -> tuple[bool, dict]:
        """
        Block until all sessions reach a terminal state or timeout.

        Returns
        -------
        (success, aggregated_status) – success is False if the build should fail.
        """
        log("Waiting for sessions to complete...")
        log(f"  Polling every {self.cfg.poll_interval}s, timeout {self.cfg.session_timeout // 60}min")

        start_time = time.time()
        status_print_interval = 30 * 60  # Print full status every 30 min
        last_status_print = 0.0
        aggregated = {}

        while True:
            # Timeout check
            elapsed = time.time() - start_time
            if self.cfg.session_timeout > 0 and elapsed > self.cfg.session_timeout:
                fail(f"Timeout: waited more than {self.cfg.session_timeout // 60} minutes.")

            # Sleep
            time.sleep(self.cfg.poll_interval)

            should_print = (time.time() - last_status_print) > status_print_interval
            if should_print:
                last_status_print = time.time()

            # Poll each session
            build_failed = False
            build_success = False

            for sid in list(self.session_ids):
                try:
                    info = self.client.get_session_status(sid)
                except VAPIError as e:
                    if should_print:
                        warn(f"Server error while checking session {sid}: {e}")
                    continue
                except Exception as e:
                    if should_print:
                        warn(f"Connection error: {e} – will retry.")
                    break  # break inner loop, continue outer

                if not info:
                    log(f"Session {sid} appears to have been deleted. Failing.")
                    build_failed = True
                    break

                state = info.get("session_status", "unknown")
                name = info.get("name", sid)
                running = info.get("running", 0)
                waiting = info.get("waiting", 0)

                self.session_names[sid] = name
                aggregated[sid] = info

                if should_print:
                    log(f"  Session '{name}' ({sid}) = {state}  [running={running}, waiting={waiting}]")

                # Rerun detection: require two consecutive checks with running=0 and waiting=0
                if running == 0 and waiting == 0:
                    if self.completed_last_check.get(sid):
                        self.final_state[sid] = True
                        # Special: if state is 'failed' but all runs actually finished
                        if state == "failed" and self._check_all_done(sid):
                            log("All sessions completed (final state after rerun check).")
                            build_success = True
                            break
                    else:
                        self.completed_last_check[sid] = True
                        self.final_state[sid] = False
                else:
                    self.completed_last_check[sid] = False
                    self.final_state[sid] = False

                # Apply resolver
                resolver = self._get_resolver(state)

                if state == "completed":
                    if self._check_all_done(sid):
                        log("All sessions completed successfully.")
                        build_success = True
                        break

                elif resolver == "fail":
                    log(f"Session '{name}' is in state '{state}' → failing build.")
                    build_failed = True
                    break

                elif resolver == "continue":
                    if self._check_all_done(sid):
                        log("All sessions reached a continuable state.")
                        build_success = True
                        break

                # 'ignore' → do nothing, keep polling

            if build_failed:
                return False, aggregated
            if build_success:
                return True, aggregated

    def get_aggregated_stats(self, aggregated: dict) -> dict:
        """Compute totals across all sessions."""
        totals = {
            "total_runs": 0,
            "passed": 0,
            "failed": 0,
            "running": 0,
            "waiting": 0,
            "other": 0,
        }
        for info in aggregated.values():
            totals["total_runs"] += _safe_int(info.get("total_runs_in_session", 0))
            totals["passed"] += _safe_int(info.get("passed_runs", 0))
            totals["failed"] += _safe_int(info.get("failed_runs", 0))
            totals["running"] += _safe_int(info.get("running", 0))
            totals["waiting"] += _safe_int(info.get("waiting", 0))
            totals["other"] += _safe_int(info.get("other_runs", 0))
        return totals


def _safe_int(v) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# JUnit XML Generation
# ---------------------------------------------------------------------------

def generate_junit_xml(
    runs: list[dict],
    output_path: str,
    extra_attrs: list[str],
    attr_labels: dict[str, str],
    no_append_seed: bool,
) -> None:
    """Generate a JUnit-compatible XML file from vManager run data."""
    if not runs:
        log("No runs found – skipping JUnit XML generation.")
        return

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(f'<testsuite tests="{len(runs)}" name="Verisium Manager">')

    for run in runs:
        status = run.get("status", "NA")
        test_group = _xml_safe(run.get("test_group", "NA"))
        test_name = _xml_safe(run.get("test_name", "NA"))
        seed = _xml_safe(run.get("computed_seed", "NA"))
        duration = run.get("duration", 0)
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = 0

        seed_suffix = "" if no_append_seed else f" : Seed-{seed}"
        full_name = f"{test_name}{seed_suffix}"

        if status == "failed":
            error_name = _xml_safe(run.get("first_failure_name", "RUN_STILL_IN_PROGRESS"))
            error_desc = _xml_safe(run.get("first_failure_description",
                "Run is in state running, other or waiting. "
                "Reason for run to mark as failed is because session changed status."))
            extra = _build_extra_attr_text(run, extra_attrs, attr_labels)
            lines.append(
                f'    <testcase classname="{test_group}" name="{full_name}" time="{duration}">'
            )
            lines.append(
                f'      <failure message="{error_name}" type="{error_name}">'
                f'First Error Description: \n{error_desc}\n'
                f'Computed Seed: \n{seed}\n'
                f'{extra}'
                f'</failure>'
            )
            lines.append('    </testcase>')

        elif status in ("stopped", "running", "other", "waiting"):
            lines.append(
                f'    <testcase classname="{test_group}" name="{full_name}" time="{duration}">'
            )
            lines.append('      <skipped />')
            lines.append('    </testcase>')

        else:
            # passed or other terminal
            lines.append(
                f'    <testcase classname="{test_group}" name="{full_name}" time="{duration}"/>'
            )

    lines.append('</testsuite>')

    xml_content = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    log(f"JUnit XML report written to: {output_path}")


def _xml_safe(text) -> str:
    """Escape special XML characters."""
    if text is None:
        return "NA"
    s = str(text)
    return xml_escape(s, {'"': "&quot;", "'": "&apos;"})


def _build_extra_attr_text(run: dict, attrs: list[str], labels: dict[str, str]) -> str:
    """Build extra attribute text for JUnit failure messages."""
    if not attrs:
        return ""
    built_in = {"first_failure_name", "first_failure_description", "computed_seed", "test_group", "test_name"}
    parts = []
    for attr in attrs:
        attr = attr.strip()
        if not attr or " " in attr or attr in built_in:
            continue
        val = run.get(attr, "NA")
        if isinstance(val, str):
            val = val.replace("<__SEPARATOR__>", "\n    ")
        label = labels.get(attr, attr)
        parts.append(f"{label}:\n    {_xml_safe(val)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Mode Implementations
# ---------------------------------------------------------------------------

def run_launcher_mode(client: VAPIClient, cfg: Config) -> list[str]:
    """Launch VSIF sessions and return session IDs."""
    log_group_start("vManager Session Launcher")
    log(f"vAPI URL: {cfg.vapi_url}")
    log(f"User: {cfg.vapi_user}")
    log(f"Auth required: {cfg.auth_required}")

    # Determine VSIF list
    vsif_files = []
    if cfg.vsif_path:
        # Static VSIF path(s), semicolon-separated
        vsif_files = [v.strip() for v in cfg.vsif_path.split(";") if v.strip()]
        log(f"Static VSIF file(s): {vsif_files}")
    elif cfg.vsif_input_file:
        # Dynamic: read from file
        log(f"Reading VSIF paths from file: {cfg.vsif_input_file}")
        vsif_files = _read_lines_from_file(cfg.vsif_input_file)
    else:
        fail("Launcher mode requires either 'vsif-path' or 'vsif-input-file'.")

    if not vsif_files:
        fail("No VSIF files found to launch.")

    # Build extra JSON parts
    extra_parts = []
    if cfg.env_variables:
        extra_parts.append(f'"environment":{cfg.env_variables}')
    if cfg.attr_values:
        try:
            attrs = json.loads(cfg.attr_values)
            extra_parts.append(f'"attributes":{json.dumps(attrs)}')
        except json.JSONDecodeError:
            warn(f"Could not parse attr-values as JSON: {cfg.attr_values}")
    if cfg.define_values:
        try:
            defines = json.loads(cfg.define_values)
            extra_parts.append(f'"params":{json.dumps(defines)}')
        except json.JSONDecodeError:
            warn(f"Could not parse define-values as JSON: {cfg.define_values}")
    if cfg.use_user_on_farm:
        if cfg.user_private_ssh_key:
            extra_parts.append('"credentials":{"connectType":"PUBLIC_KEY"}')
        else:
            farm_user = cfg.farm_user or cfg.vapi_user
            farm_pass = cfg.farm_password or cfg.vapi_password
            extra_parts.append(f'"credentials":{{"username":"{farm_user}","password":"{farm_pass}"}}')
        if cfg.env_source_file:
            extra_parts.append(
                f'"preliminaryStage":{{"sourceFilePath":"{cfg.env_source_file}",'
                f'"shell":"{cfg.env_source_file_type}"}}'
            )

    extra_json = ",".join(extra_parts)

    # Launch each VSIF
    session_ids = []
    for vsif in vsif_files:
        try:
            sid = client.launch_vsif(vsif, extra_json)
            session_ids.append(sid)
        except VAPIError as e:
            fail(f"Failed to launch VSIF '{vsif}': {e}")

    log(f"Launched {len(session_ids)} session(s): {session_ids}")

    # Write session IDs to output file
    with open("session_launch.output", "w") as f:
        for sid in session_ids:
            f.write(f"${sid}\n")

    log_group_end()
    return session_ids


def run_api_mode(client: VAPIClient, cfg: Config) -> None:
    """Execute a free-style vAPI call."""
    log_group_start("vManager API Call")

    if not cfg.api_url:
        fail("API mode requires 'api-url' input.")

    log(f"API endpoint: /rest{cfg.api_url}")
    log(f"Method: {cfg.api_method}")

    # Determine JSON input
    json_input = cfg.api_input
    if cfg.api_input_file:
        log(f"Reading API input from file: {cfg.api_input_file}")
        with open(cfg.api_input_file, "r") as f:
            json_input = f.read().strip()

    log(f"Input: {json_input[:500]}{'...' if len(json_input) > 500 else ''}")

    # Make the call
    path = f"/rest{cfg.api_url}"
    body = json_input if cfg.api_method in ("POST", "PUT") else None
    try:
        result = client.request(path, cfg.api_method, body)
    except VAPIError as e:
        fail(f"API call failed: {e}")

    # Output
    result_str = json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
    output_file = "vapi.output"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(result_str)
    log(f"API output saved to: {output_file}")

    set_output("api-output", result_str[:65000])  # GitHub has output size limits
    set_output("api-output-file", output_file)

    log_group_end()


def run_batch_mode(client: VAPIClient, cfg: Config) -> list[str]:
    """Resolve session names to IDs from a pre-launched batch."""
    log_group_start("vManager Batch/Collect Mode")

    if not cfg.sessions_input_file:
        fail("Batch/Collect mode requires 'sessions-input-file'.")

    log(f"Reading session names from: {cfg.sessions_input_file}")
    session_names = _read_lines_from_file(cfg.sessions_input_file)

    if not session_names:
        fail("No session names found in the input file.")

    log(f"Looking up IDs for {len(session_names)} session(s)...")
    session_ids = client.get_session_ids_by_names(session_names)

    if not session_ids:
        fail("Could not find any session IDs for the given session names. "
             "Please verify the session names match what is in Verisium Manager.")

    log(f"Resolved {len(session_ids)} session ID(s): {session_ids}")
    log_group_end()
    return session_ids


def _read_lines_from_file(filepath: str) -> list[str]:
    """Read non-empty lines from a text file."""
    try:
        with open(filepath, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        fail(f"File not found: {filepath}")
    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = Config()

    if not cfg.vapi_url:
        fail("'vapi-url' is required.")

    client = VAPIClient(cfg)

    # Test connection
    try:
        client.check_connection()
    except VAPIError as e:
        fail(f"Failed to connect to vManager: {e}")
    except Exception as e:
        fail(f"Failed to connect to vManager: {e}")

    # Dispatch by mode
    session_ids: list[str] = []

    if cfg.mode == "launcher":
        session_ids = run_launcher_mode(client, cfg)

    elif cfg.mode == "api":
        run_api_mode(client, cfg)
        return  # API mode doesn't involve sessions

    elif cfg.mode in ("batch", "collect"):
        session_ids = run_batch_mode(client, cfg)

    else:
        fail(f"Unknown mode: '{cfg.mode}'. Use 'launcher', 'api', 'batch', or 'collect'.")

    # Set session IDs output (coerce to str in case vAPI returned ints)
    session_ids = [str(sid) for sid in session_ids]
    set_output("session-ids", ",".join(session_ids))

    # Wait for sessions if requested
    if cfg.wait and session_ids:
        log_group_start("Waiting for Sessions")
        waiter = SessionWaiter(client, cfg, session_ids)
        success, aggregated = waiter.wait()
        stats = waiter.get_aggregated_stats(aggregated)

        # Determine final status
        statuses = set()
        for info in aggregated.values():
            statuses.add(info.get("session_status", "unknown"))
        if len(statuses) == 1:
            final_status = statuses.pop()
        elif statuses:
            final_status = "mixed"
        else:
            final_status = "unknown"

        set_output("session-status", final_status)
        set_output("total-runs", str(stats["total_runs"]))
        set_output("passed-runs", str(stats["passed"]))
        set_output("failed-runs", str(stats["failed"]))

        log(f"Session status: {final_status}")
        log(f"Runs: total={stats['total_runs']}, passed={stats['passed']}, "
            f"failed={stats['failed']}, running={stats['running']}, "
            f"waiting={stats['waiting']}, other={stats['other']}")

        # Write session status properties file
        _write_session_status(session_ids, aggregated, cfg.vapi_url)

        # Check fail conditions
        if cfg.fail_if_all_failed and stats["total_runs"] > 0:
            if stats["total_runs"] == stats["failed"]:
                fail("All runs failed in the regression.")

        if cfg.fail_unless_all_passed and stats["total_runs"] > 0:
            if stats["total_runs"] != stats["passed"]:
                fail("Not all runs passed the regression.")

        # Generate JUnit XML
        if cfg.generate_junit:
            log_group_start("JUnit XML Report Generation")
            extra_list = [a.strip() for a in cfg.extra_attributes.split(",") if a.strip()] if cfg.extra_attributes else []
            attr_labels = {}
            if extra_list:
                attr_labels = client.get_run_attribute_labels(extra_list)

            runs = client.get_runs(session_ids, extra_list if extra_list else None)
            log(f"Fetched {len(runs)} run(s) for JUnit report.")

            generate_junit_xml(
                runs=runs,
                output_path=cfg.junit_output_path,
                extra_attrs=extra_list,
                attr_labels=attr_labels,
                no_append_seed=cfg.no_append_seed,
            )
            set_output("junit-report-path", cfg.junit_output_path)
            log_group_end()

        log_group_end()

        if not success:
            fail("One or more sessions ended in a failure state.")

    elif session_ids:
        # Not waiting – just set outputs
        set_output("session-status", "launched")
        log("Sessions launched. Not waiting for completion (wait-for-session-end=false).")


def _write_session_status(session_ids: list[str], aggregated: dict, vapi_url: str) -> None:
    """Write a session_status.properties file for downstream use."""
    lines = []
    for sid in session_ids:
        info = aggregated.get(sid, {})
        lines.append(f"# Session {sid}")
        lines.append(f"status={info.get('session_status', 'NA')}")
        lines.append(f"name={info.get('name', 'NA')}")
        lines.append(f"total_runs_in_session={info.get('total_runs_in_session', 'NA')}")
        lines.append(f"passed_runs={info.get('passed_runs', 'NA')}")
        lines.append(f"failed_runs={info.get('failed_runs', 'NA')}")
        lines.append(f"running={info.get('running', 'NA')}")
        lines.append(f"waiting={info.get('waiting', 'NA')}")
        lines.append(f"other_runs={info.get('other_runs', 'NA')}")
        lines.append(f"owner={info.get('owner', 'NA')}")
        lines.append(f"id={sid}")
        lines.append(f"url={vapi_url}")
        lines.append("")

    with open("session_status.properties", "w") as f:
        f.write("\n".join(lines))
    log("Session status written to session_status.properties")


if __name__ == "__main__":
    main()

