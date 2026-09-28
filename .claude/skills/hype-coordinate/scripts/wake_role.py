#!/usr/bin/env python3
"""Safely dispatch a GitHub work packet to an idle HypeProof cmux role tab."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass


WORKSPACE_TITLE = "studio-testing"
ROLE_CONFIG = {
    "a": ("claude-1", "/hype-coordinate"),
    "b": ("claude-2-impl", "/hype-studio"),
    "c": ("claude-3-impl", "/hype-chalk"),
    "x1": ("codex-testing", "$hype-intent"),
    "x2": ("codex-2", "$hype-verify"),
}
WORK_URL = re.compile(
    r"https://github\.com/jayleekr/"
    r"(?:hypeproof-harness|hypeproof-studio|hypeprooflab)/"
    r"(?:issues|pull)/[0-9]+\Z"
)
PACKET = re.compile(r"[A-Za-z0-9._-]+\Z")
WORKSPACE_LINE = re.compile(
    r"workspace (workspace:[0-9]+) ([0-9A-F-]+) \"([^\"]+)\""
)
SURFACE_LINE = re.compile(
    r"surface (surface:[0-9]+) ([0-9A-F-]+).* \"([^\"]+)\""
)
PROMPT_LINE = re.compile(r"^\s*[❯›]\s*(.*)$")


@dataclass(frozen=True)
class Surface:
    workspace_ref: str
    workspace_id: str
    surface_ref: str
    surface_id: str
    title: str


def cmux(*args: str) -> str:
    proc = subprocess.run(
        ["cmux", *args], text=True, capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "cmux failed")
    return proc.stdout


# A socket in cmuxOnly mode closes connections from processes that cmux did not
# start (for example a launchd agent); the CLI then reports a broken pipe even
# though the socket exists and CMUX_SOCKET_PATH is correct (#180).
SOCKET_DENIED = re.compile(r"broken pipe|errno 32|access denied|not allowed|unauthori[sz]ed", re.I)


def preflight_socket(retries: int = 2, delay: float = 0.5) -> None:
    """Fail before touching any surface unless cmux answers a ping."""
    error = ""
    for attempt in range(retries + 1):
        try:
            reply = cmux("ping").strip()
        except RuntimeError as exc:
            error = str(exc)
        else:
            if reply == "PONG":
                return
            error = f"unexpected ping reply {reply!r}"
        if SOCKET_DENIED.search(error):
            raise RuntimeError(
                f"socket_access_denied: cmux rejected this process ({error}). "
                "In cmuxOnly mode only processes started under cmux may connect; a "
                "launchd watcher needs socketControlMode=password with "
                "CMUX_SOCKET_PASSWORD, or must run inside a cmux surface. "
                "Retrying cannot recover."
            )
        if attempt < retries:
            time.sleep(delay)
    raise RuntimeError(
        f"socket_unavailable: cmux ping failed after {retries + 1} attempts ({error})"
    )


def find_surface(title: str) -> Surface:
    current: tuple[str, str, str] | None = None
    for line in cmux("tree", "--all", "--id-format", "both").splitlines():
        workspace = WORKSPACE_LINE.search(line)
        if workspace:
            current = workspace.groups()
            continue
        surface = SURFACE_LINE.search(line)
        if current and current[2] == WORKSPACE_TITLE and surface:
            surface_ref, surface_id, surface_title = surface.groups()
            if surface_title == title:
                return Surface(current[0], current[1], surface_ref, surface_id, title)
    raise RuntimeError(f"cmux surface {title!r} not found in {WORKSPACE_TITLE!r}")


def read_screen(surface: Surface, lines: int = 30) -> str:
    return cmux(
        "read-screen",
        "--workspace", surface.workspace_id,
        "--surface", surface.surface_id,
        "--scrollback",
        "--lines", str(lines),
    )


def prompt_state(screen: str) -> str:
    """Return composer text that may be unsent, after known-inert cases."""
    tail = screen.splitlines()[-12:]
    if any("esc to interrupt" in line.lower() for line in tail):
        raise RuntimeError("target role is running")
    prompts = [
        (index, match.group(1).replace("\u00a0", " ").strip())
        for index, line in enumerate(tail)
        if (match := PROMPT_LINE.match(line))
    ]
    if not prompts:
        raise RuntimeError("target idle prompt was not observed")
    index, prompt = prompts[-1]
    if "Ask Codex to do anything" in prompt:
        prompt = ""
    if any("done " in line for line in tail[index + 1:]):
        prompt = ""
    if prompt and any(previous.startswith(prompt) for _, previous in prompts[:-1]):
        # Claude can render a dim history suggestion in an empty prompt. cmux's
        # plain-text screen does not preserve the dim styling, so recognize the
        # suggestion by its exact prefix of a completed earlier prompt.
        prompt = ""
    return prompt


def assert_idle(screen: str) -> None:
    if prompt_state(screen):
        raise RuntimeError("target prompt contains unsent text")


# Claude Code shows dim prompt suggestions that are not history entries, and
# cmux's text read drops the dim styling (#180). Typing one probe character
# replaces a suggestion but is appended to real input, so the rendered result
# tells them apart; one backspace then restores the composer either way.
PROBE = "¶"


def classify_prompt(surface: Surface, timeout: float = 2.0) -> None:
    """Return only when the visible composer text is an inert suggestion."""
    type_message(surface, PROBE)
    deadline = time.monotonic() + max(0.1, timeout)
    observed = None
    while time.monotonic() < deadline:
        observed = prompt_state(read_screen(surface, lines=40))
        if observed.endswith(PROBE):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError(
            f"suggestion probe was not rendered; composer not restored, check it for {PROBE}"
        )
    cmux("send-key", *target_args(surface), "backspace")
    if observed != PROBE:
        raise RuntimeError("target prompt contains unsent text; typed input kept")


def target_args(surface: Surface) -> tuple[str, ...]:
    return ("--workspace", surface.workspace_id, "--surface", surface.surface_id)


def type_message(surface: Surface, message: str) -> None:
    # `cmux send` returning only means that cmux accepted the text. The TUI can
    # render it later, so Enter must be a separate, observed step.
    cmux("send", *target_args(surface), "--", message)


def submit_message(surface: Surface) -> None:
    cmux("send-key", *target_args(surface), "enter")


def cancel_message(surface: Surface) -> None:
    # Ctrl-U clears active terminal input without submitting the packet.
    cmux("send-key", *target_args(surface), "ctrl+u")


def marker_count(screen: str, marker: str) -> int:
    return screen.count(marker)


def running_observed_after(screen: str, marker: str) -> bool:
    """Return true only for a running marker rendered after this dispatch."""
    lines = screen.splitlines()
    indexes = [index for index, line in enumerate(lines) if marker in line]
    if not indexes:
        return False
    return any(
        "esc to interrupt" in line.lower() for line in lines[indexes[-1] + 1 :]
    )


def wait_for_render(
    surface: Surface, marker: str, baseline_count: int, timeout: float
) -> None:
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if marker_count(read_screen(surface, lines=40), marker) > baseline_count:
            return
        time.sleep(0.2)
    raise RuntimeError("dispatch text was not rendered; Enter was not sent")


def clear_unsubmitted(surface: Surface, marker: str, timeout: float = 2.0) -> bool:
    screen = read_screen(surface, lines=40)
    if marker not in screen or running_observed_after(screen, marker):
        return marker not in screen
    cancel_message(surface)
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        screen = read_screen(surface, lines=40)
        if marker not in screen:
            return True
        if running_observed_after(screen, marker):
            return False
        time.sleep(0.2)
    return False


def send(surface: Surface, message: str, marker: str, render_timeout: float) -> None:
    # Codex treats a newline embedded in `cmux send` as multiline prompt text.
    # Type the complete text, observe this unique attempt in the composer, then
    # target the same surface with Enter. This closes the render/Enter race.
    baseline = read_screen(surface, lines=40)
    type_message(surface, message)
    try:
        wait_for_render(
            surface, marker, marker_count(baseline, marker), render_timeout
        )
    except RuntimeError:
        clear_unsubmitted(surface, marker)
        raise
    submit_message(surface)


def running_observed(screen: str) -> bool:
    return any(
        "esc to interrupt" in line.lower()
        for line in screen.splitlines()[-12:]
    )


def wait_for_start(surface: Surface, marker: str, timeout: float) -> None:
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if running_observed_after(read_screen(surface, lines=40), marker):
            return
        time.sleep(0.2)
    cleared = clear_unsubmitted(surface, marker)
    cleanup = "composer cleared" if cleared else "composer cleanup not confirmed"
    raise RuntimeError(
        f"dispatch remained unsubmitted or no marker-specific start was observed; {cleanup}"
    )


def ack_observed(screen: str, ack: str) -> bool:
    answer = re.compile(rf"^\s*[^A-Za-z0-9_]*{re.escape(ack)}\s*$")
    return any(
        not PROMPT_LINE.match(line) and bool(answer.fullmatch(line))
        for line in screen.splitlines()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ROLE_CONFIG), required=True)
    parser.add_argument("--packet")
    parser.add_argument("--work-url")
    parser.add_argument(
        "--state-file",
        help="Shared delivery-delta state path included in coordinator packets",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--ack-timeout", type=int, default=30)
    parser.add_argument("--render-timeout", type=float, default=5.0)
    parser.add_argument("--start-timeout", type=float, default=5.0)
    args = parser.parse_args()

    try:
        title, invocation = ROLE_CONFIG[args.role]
        if args.state_file and args.role != "a":
            raise ValueError("--state-file is only valid for the coordinator role")
        preflight_socket()
        surface = find_surface(title)
        if prompt_state(read_screen(surface)):
            classify_prompt(surface)

        ack = None
        attempt = f"WAKE_ATTEMPT_{args.role.upper()}_{time.time_ns()}"
        if args.test:
            ack = f"WAKE_ACK_{args.role.upper()}_{int(time.time())}"
            message = (
                "WAKE TEST from the deterministic coordinator dispatcher. "
                "Do not read repositories or modify files. "
                f"Dispatch attempt {attempt}. Reply exactly {ack} and return to idle."
            )
        else:
            if not args.packet or not PACKET.fullmatch(args.packet):
                raise RuntimeError("--packet must use letters, numbers, dot, dash, or underscore")
            if not args.work_url or not WORK_URL.fullmatch(args.work_url):
                raise RuntimeError("--work-url must be an allowed HypeProof GitHub issue or PR")
            if args.role == "a" and args.state_file:
                state_file = Path(args.state_file).expanduser().resolve()
                message = (
                    f"{invocation} Deterministic watcher packet {args.packet}. "
                    f"Dispatch attempt {attempt}. Run delivery_delta.py with "
                    f"--state-file {shlex.quote(str(state_file))}, reconcile pending "
                    f"token {args.packet} from {args.work_url}, route and verify actual "
                    "worker acknowledgment, then acknowledge that token only after the "
                    "durable GitHub outcome. Do not register a recurring task in this "
                    "session. Use English for engineering work and report to the user "
                    "in Korean."
                )
            else:
                message = (
                    f"{invocation} Coordinator dispatch packet {args.packet}. "
                    f"Dispatch attempt {attempt}. "
                    f"Read and execute {args.work_url}. Acknowledge the real session "
                    "and branch in the GitHub record, work through tests and handoff, "
                    "use English for engineering work, and report to the user in Korean."
                )

        result = {
            "status": "ready" if not args.apply else "submitted",
            "role": args.role,
            "workspace": surface.workspace_id,
            "surface": surface.surface_id,
            "title": surface.title,
            "dispatch_attempt": attempt,
        }
        if not args.apply:
            print(json.dumps(result, separators=(",", ":")))
            return 0

        send(surface, message, attempt, args.render_timeout)
        if ack:
            deadline = time.monotonic() + max(1, args.ack_timeout)
            while time.monotonic() < deadline:
                screen = read_screen(surface, lines=20)
                if ack_observed(screen, ack):
                    try:
                        assert_idle(screen)
                    except RuntimeError:
                        pass
                    else:
                        result["status"] = "acknowledged"
                        result["ack"] = ack
                        print(json.dumps(result, separators=(",", ":")))
                        return 0
                time.sleep(1)
            raise RuntimeError(f"dispatch sent but {ack} was not observed")

        wait_for_start(surface, attempt, args.start_timeout)
        result["status"] = "submitted_pending_ack"
        result["accepted"] = False
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
