"""OpenCode CLI adapter (`opencode run`, non-interactive / one-shot mode)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from app.integrations.llm_cli.base import CLIInvocation, CLIProbe
from app.integrations.llm_cli.binary_resolver import (
    candidate_binary_names as _candidate_binary_names,
)
from app.integrations.llm_cli.binary_resolver import (
    default_cli_fallback_paths as _default_cli_fallback_paths,
)
from app.integrations.llm_cli.binary_resolver import (
    resolve_cli_binary,
)

_OPENCODE_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_PROBE_TIMEOUT_SEC = 8.0


def _parse_semver(text: str) -> str | None:
    m = _OPENCODE_VERSION_RE.search(text)
    return m.group(1) if m else None


def _get_opencode_creds_path() -> Path:
    """Return path to OpenCode's auth.json on all platforms."""
    # Respect XDG_DATA_HOME if set, otherwise use default
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        base = Path(xdg_data_home)
    else:
        base = Path.home() / ".local" / "share"

    return base / "opencode" / "auth.json"


def _classify_opencode_auth() -> tuple[bool | None, str]:
    """Return (logged_in, detail) by checking OpenCode's credential store."""
    creds_path = _get_opencode_creds_path()

    if creds_path.exists():
        try:
            with open(creds_path) as f:
                data = json.load(f)

                if isinstance(data, dict):
                    # Check if any provider has a non-empty key field
                    has_credentials = any(
                        isinstance(creds, dict)
                        and creds.get("key", "").strip()
                        and len(creds.get("key", "").strip()) > 0
                        for creds in data.values()
                    )

                    if has_credentials:
                        return True, f"Authenticated via {creds_path}"
                    else:
                        return (
                            False,
                            f"No valid credentials in {creds_path}. Run: opencode auth login",
                        )
        except (OSError, json.JSONDecodeError) as e:
            return None, f"Could not read {creds_path}: {e}"

    return False, f"Not authenticated. Run: opencode auth login (creates {creds_path})"


def _fallback_opencode_paths() -> list[str]:
    return _default_cli_fallback_paths("opencode")


class OpenCodeAdapter:
    """Non-interactive OpenCode CLI (`opencode run`, one-shot execution)."""

    name = "opencode"
    binary_env_key = "OPENCODE_BIN"
    install_hint = (
        "brew install anomalyco/tap/opencode  (macOS/Linux) | choco install opencode (Windows)"
    )
    auth_hint = "Run: opencode auth login (interactive, configures your LLM provider)"
    min_version: str | None = None
    default_exec_timeout_sec = 120.0

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="OPENCODE_BIN",
            binary_names=_candidate_binary_names("opencode"),
            fallback_paths=_fallback_opencode_paths,
        )

    def _probe_binary(self, binary_path: str) -> CLIProbe:
        try:
            ver_proc = subprocess.run(
                [binary_path, "--version"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"Could not run `{binary_path} --version`: {exc}",
            )

        if ver_proc.returncode != 0:
            err = (ver_proc.stderr or ver_proc.stdout or "").strip()
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"`{binary_path} --version` failed: {err or 'unknown error'}",
            )

        version = _parse_semver(ver_proc.stdout + ver_proc.stderr)
        logged_in, auth_detail = _classify_opencode_auth()

        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary_path,
            detail=auth_detail,
        )

    def detect(self) -> CLIProbe:
        binary = self._resolve_binary()
        if not binary:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=(
                    "OpenCode CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint}  or set OPENCODE_BIN."
                ),
            )
        return self._probe_binary(binary)

    def build(self, *, prompt: str, model: str | None, workspace: str) -> CLIInvocation:
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"OpenCode CLI not found. {self.install_hint}"
                " or set OPENCODE_BIN to the full binary path."
            )

        cwd = workspace or os.getcwd()

        argv: list[str] = [
            binary,
            "run",
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["-m", resolved_model])

        # OpenCode doesn't use API keys in env; auth is from pre-configured credentials
        # Only forward proxy settings if needed
        env: dict[str, str] = {"NO_COLOR": "1"}
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            val = os.environ.get(key, "").strip()
            if val:
                env[key] = val

        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=cwd,
            env=env,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        """Extract the agent's final response from stdout."""
        del stderr, returncode
        # OpenCode writes the agent's response to stdout; stderr may contain logs
        return (stdout or "").strip()

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        err = (stderr or "").strip()
        out = (stdout or "").strip()
        bits = [f"opencode run exited with code {returncode}"]

        # Check for common auth errors
        combined = (err + " " + out).lower()
        if "not authenticated" in combined or ("auth" in combined and "failed" in combined):
            bits.append("Authentication failed. Run: opencode auth login")
        elif "model" in combined and ("not found" in combined or "invalid" in combined):
            bits.append(
                "Model not found. Check OPENCODE_MODEL format: provider/model (e.g., openai/gpt-5.4)"
            )
        elif "rate limit" in combined or "quota" in combined:
            bits.append(
                "Rate limited or quota exceeded. Try again later or check your provider plan"
            )

        if err:
            bits.append(err[:2000])
        elif out:
            bits.append(out[:2000])
        return ". ".join(bits)
