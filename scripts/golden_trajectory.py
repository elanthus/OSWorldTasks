#!/usr/bin/env python3
"""Generate, verify, and report the frozen golden trajectory.

A golden trajectory is a frozen list of public `NOOP`/`CLICK`/`KEY` actions
that solves one seeded vendor-onboarding task, plus the reward timeline it is
required to produce. The committed one is seed 7. It is the project's evidence
that reward fires exactly once, on a valid submission, and only then.

Three code paths, deliberately kept apart:

  generation   privileged. `tests.support.golden_solver` reads the task's
               expected field values to work out what to type and where to
               click. Only ever used to produce a *candidate*.

  replay       blind. Loads nothing but the literal integers in the fixture,
               resets a fresh environment to the recorded seed, and replays.
               With `collect_evidence=False` it does not construct a
               `TaskSpec`, does not call the evaluator, and does not touch
               `generator` -- so the reward timeline it observes cannot be an
               artifact of the replay knowing the answer. (`FakeBackend`
               reaches for the generator on its own during `reset`; that is the
               backend building the task, not the replay reading the answer.)

  evidence     privileged, and only reached via `collect_evidence=True`. Builds
               the host-side `TaskSpec` and records what the evaluator saw at
               each step. Evaluator output is only ever *recorded* and
               *asserted against*, never consulted to choose an action, and the
               actions were fixed before any of this ran.

Verification proves reward and privileged evaluator state agree: the evaluator
must report failure at every step before the last, the last step must pay out
`1.0` and terminate, the evaluator must independently agree it succeeded on an
exactly-matching submission, exactly one submission may exist, and stepping
afterwards must raise. A fabricated timeline that pays out without a recorded
submission is rejected.

Commands:

    # Verify the committed seed-7 fixture and detect drift; writes nothing.
    python scripts/golden_trajectory.py check

    # Produce a candidate, without overwriting the committed fixture.
    python scripts/golden_trajectory.py generate --seed 7 \\
        --output /tmp/golden_trajectory_seed7.json

    # Blindly replay any candidate.
    python scripts/golden_trajectory.py verify \\
        --fixture /tmp/golden_trajectory_seed7.json

    # Generate the evidence bundle from an accepted fixture. Artifact names and
    # every claim inside them are derived from the fixture's own seed.
    python scripts/golden_trajectory.py artifacts \\
        --fixture tests/unit/fixtures/golden_trajectory_seed7.json \\
        --output artifacts/day-1

`generate` refuses to write to the committed fixture path. Regeneration must
go candidate -> blind verify -> human diff review -> manual replace: a layout
or generator bug that silently rewrote the golden oracle in place would make
the tests pass by moving the goalposts.

This is a development tool, not part of the `pixelgym` distribution
(`[tool.setuptools.packages.find]` includes only `pixelgym*`), which is what
keeps the privileged solver unreachable from the shipped package.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Run as `python scripts/golden_trajectory.py`, sys.path[0] is `scripts/`,
    # so the repo root (and with it `tests.support`) is not importable yet.
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from pixelgym.actions import KEY_ALLOWLIST, KEY_ALLOWLIST_VERSION, ActionType, InvalidActionError
from pixelgym.backends.fake import FakeBackend
from pixelgym.env import DEFAULT_INSTRUCTION, DEFAULT_MAX_EPISODE_STEPS, PixelGuiEnv
from pixelgym.evaluator import evaluate
from pixelgym.task_spec import TaskSpec
from pixelgym.tasks.vendor_form import generator

SCHEMA_VERSION = 1
COMMITTED_SEED = 7
COMMITTED_FIXTURE = REPO_ROOT / "tests" / "unit" / "fixtures" / "golden_trajectory_seed7.json"
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "day-1"

_ACTION_FIELDS = ("action_type", "x", "y", "key")
_TIMELINE_FIELDS = ("step", "action_type", "reward", "terminated", "truncated")

FIXTURE_NOTE = (
    "Frozen golden trajectory: a list of public actions that solves the "
    "seed-7 vendor-onboarding task, and the reward timeline it must produce. "
    "Replayed verbatim by tests/unit/test_golden_trajectory.py, which never "
    "consults the task generator or any privileged backend state. Coordinates "
    "are absolute pixels for the screen size below, and 'key' indexes "
    "KEY_ALLOWLIST at the version below -- both must match or the replay is "
    "meaningless. Verify with 'python scripts/golden_trajectory.py check'; that "
    "command also prints the regeneration recipe when this file has drifted."
)


def artifact_stem(seed: int) -> str:
    """Artifact basename for `seed`. Derived, never hard-coded: a seed-11
    bundle must not be able to land under seed-7 names."""
    return f"golden-trajectory-seed-{seed}"


# -- Canonical serialization ------------------------------------------------


def canonical_fixture_json(payload: dict[str, Any]) -> str:
    """Serialize a fixture payload deterministically.

    Key order is fixed by construction (`build_fixture_payload` inserts keys in
    one order) rather than alphabetically, so the file reads top-down as
    metadata then actions then timeline. Two-space indent, trailing newline, no
    timestamps anywhere -- regenerating an unchanged trajectory must produce a
    byte-identical file, or `check`'s diff would be noise.
    """
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def _task_spec_sha256(record: dict[str, Any]) -> str:
    """SHA-256 of the canonical task spec body -- the same bytes `task_id` is
    derived from, kept at full width so drift in the task generator is
    visible even if the truncated `task_id` did not move."""
    body = {key: value for key, value in record.items() if key != "task_id"}
    return hashlib.sha256(generator.canonical_json(body).encode("utf-8")).hexdigest()


def _observation_sha256(observation: np.ndarray) -> str:
    return hashlib.sha256(observation.tobytes()).hexdigest()


def git_provenance() -> str | None:
    """`<short-sha>` or `<short-sha>-dirty`, or None outside a git checkout.

    The dirty marker matters: an artifact generated from an uncommitted tree
    does not correspond to any commit, and saying so is more useful than
    printing a commit that does not contain the code that produced it.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{sha}-dirty" if dirty else sha


# -- Replay -----------------------------------------------------------------


class ReplayError(RuntimeError):
    """A fixture could not be replayed at all (as opposed to replaying and
    producing the wrong timeline, which is reported as a check failure)."""


def _evaluator_task(seed: int, app_url: str) -> TaskSpec:
    """The host-side `TaskSpec` the evaluator scores against.

    Only ever called from the `collect_evidence=True` path. The environment's
    own `TaskSpec` is private, so this rebuilds an identical one from the public
    generator and the env module's documented defaults -- which is what
    `PixelGuiEnv(backend)` is constructed with in `replay`.
    """
    return TaskSpec.from_generated(
        generator.generate_task(seed),
        instruction=DEFAULT_INSTRUCTION,
        app_url=app_url,
        max_episode_steps=DEFAULT_MAX_EPISODE_STEPS,
    )


def replay(fixture: dict[str, Any], *, collect_evidence: bool = False) -> dict[str, Any]:
    """Blindly replay `fixture`'s literal actions through a fresh environment.

    Reads `fixture["seed"]` and `fixture["actions"]` and nothing else about the
    task. Returns the observed timeline, per-step observation hashes, the final
    observation, whether a post-terminal step raised the documented
    reset-required error, and -- only when `collect_evidence` is set -- the
    host-side evaluator's diagnostics per step.

    With `collect_evidence=False` this function constructs no `TaskSpec`, calls
    no evaluator, and touches `generator` nowhere: the blind path must be blind
    in fact, not only in the docstring.
    """
    seed = fixture["seed"]
    actions = fixture["actions"]

    backend = FakeBackend()
    env = PixelGuiEnv(backend)
    observation, info = env.reset(seed=seed)

    task = _evaluator_task(seed, backend.app_url) if collect_evidence else None

    steps: list[dict[str, Any]] = []
    ended = False
    for number, action in enumerate(actions, start=1):
        if ended:
            raise ReplayError(
                f"the episode ended at step {number - 1} of {len(actions)}; the "
                "remaining recorded actions cannot be replayed"
            )
        observation, reward, terminated, truncated, _info = env.step(action)
        ended = terminated or truncated

        step: dict[str, Any] = {
            "step": number,
            "action_type": ActionType(int(action["action_type"])).name,
            "reward": float(reward),
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "observation_sha256": _observation_sha256(observation),
        }
        if task is not None:
            result = evaluate(task, backend.read_submissions())
            step["evaluation"] = {
                "submitted": result.submitted,
                "success": result.success,
                "task_id_matches": result.task_id_matches,
                # Field *names* only. The expected values themselves never
                # enter an artifact through this path.
                "mismatched_field_names": list(result.mismatched_fields),
            }
        steps.append(step)

    # The episode is over; stepping again must raise, not silently continue.
    # Checked here, inside the replay that produced the terminal state, because
    # that is the only place the guard can be observed against the real episode.
    post_terminal_raises: bool | None = None
    if ended and actions:
        try:
            env.step(actions[0])
        except RuntimeError as error:
            post_terminal_raises = "reset" in str(error)
        else:
            post_terminal_raises = False

    final_result = evaluate(task, backend.read_submissions()) if task is not None else None
    submissions = backend.read_submissions()
    env.close()

    return {
        "task_id": info["task_id"],
        "steps": steps,
        "final_observation": observation,
        "final_evaluation": final_result,
        "submission_count": len(submissions),
        "ended": ended,
        "post_terminal_raises": post_terminal_raises,
    }


def timeline_of(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the public reward-timing record without replay-only diagnostics."""
    return [{field: step[field] for field in _TIMELINE_FIELDS} for step in steps]


# -- Generate ---------------------------------------------------------------


def build_fixture_payload(seed: int) -> dict[str, Any]:
    """A complete candidate fixture for `seed`.

    Actions come from the privileged solver; the timeline is then observed by
    replaying those actions blind, so the recorded timeline is always something
    the environment actually produced rather than something asserted here.
    """
    from tests.support.golden_solver import build_golden_actions

    backend = FakeBackend()
    record = backend.reset(seed)
    actions = build_golden_actions(backend)

    observed = replay({"seed": seed, "actions": actions})

    return {
        "note": FIXTURE_NOTE,
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "task_id": record["task_id"],
        "task_spec_sha256": _task_spec_sha256(dict(record)),
        "key_allowlist_version": KEY_ALLOWLIST_VERSION,
        "screen": {"width": backend.width, "height": backend.height},
        "action_count": len(actions),
        "actions": actions,
        "timeline": timeline_of(observed["steps"]),
    }


# -- Verify -----------------------------------------------------------------


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _action_problems(actions: list[Any], width: int, height: int) -> tuple[list[str], list[dict]]:
    """Validate every action fully before anything indexes into it.

    Returns the problems and the subset of actions that came through clean --
    only those are safe for the action-type coverage check below, which is what
    stops an action missing `action_type` from raising `KeyError` out of a
    validator whose whole job is to report that kind of thing calmly.
    """
    bounds = {"action_type": len(ActionType), "x": width, "y": height, "key": len(KEY_ALLOWLIST)}
    problems: list[str] = []
    valid: list[dict] = []

    for number, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            problems.append(f"action {number} is not an object: {action!r}")
            continue
        if set(action) != set(_ACTION_FIELDS):
            missing = sorted(set(_ACTION_FIELDS) - set(action))
            extra = sorted(set(action) - set(_ACTION_FIELDS))
            detail = ", ".join(
                part
                for part in (
                    f"missing {missing}" if missing else "",
                    f"unexpected {extra}" if extra else "",
                )
                if part
            )
            problems.append(f"action {number} has the wrong fields ({detail}): {action!r}")
            continue

        clean = True
        for name in _ACTION_FIELDS:
            value = action[name]
            if not _is_plain_int(value):
                problems.append(f"action {number}[{name!r}] is not a plain int: {value!r}")
                clean = False
            elif not 0 <= value < bounds[name]:
                problems.append(
                    f"action {number}[{name!r}]={value} is outside the declared space "
                    f"[0, {bounds[name]})"
                )
                clean = False
        if clean:
            valid.append(action)

    return problems, valid


def _timeline_record_problems(timeline: list[Any], action_count: int) -> list[str]:
    """Validate every timeline record fully before the shape checks index it."""
    problems: list[str] = []
    names = {member.name for member in ActionType}

    for number, record in enumerate(timeline, start=1):
        if not isinstance(record, dict):
            problems.append(f"timeline record {number} is not an object: {record!r}")
            continue
        if set(record) != set(_TIMELINE_FIELDS):
            missing = sorted(set(_TIMELINE_FIELDS) - set(record))
            extra = sorted(set(record) - set(_TIMELINE_FIELDS))
            detail = ", ".join(
                part
                for part in (
                    f"missing {missing}" if missing else "",
                    f"unexpected {extra}" if extra else "",
                )
                if part
            )
            problems.append(f"timeline record {number} has the wrong fields ({detail}): {record!r}")
            continue

        if not _is_plain_int(record["step"]) or record["step"] != number:
            problems.append(
                f"timeline record {number} has step {record['step']!r}; records must be "
                f"numbered 1..{action_count} in order"
            )
        if record["action_type"] not in names:
            problems.append(
                f"timeline record {number} has action_type {record['action_type']!r}, "
                f"expected one of {sorted(names)}"
            )
        if isinstance(record["reward"], bool) or not isinstance(record["reward"], (int, float)):
            problems.append(
                f"timeline record {number} reward is not a number: {record['reward']!r}"
            )
        for flag in ("terminated", "truncated"):
            if not isinstance(record[flag], bool):
                problems.append(f"timeline record {number} {flag} is not a bool: {record[flag]!r}")

    return problems


def _structural_problems(fixture: dict[str, Any]) -> list[str]:
    """Everything checkable without replaying: metadata agreement, action
    well-formedness, action-space containment, action-type coverage, and
    timeline record well-formedness.

    Every value is validated before it is indexed, so a malformed fixture
    produces a list of complaints rather than a traceback.
    """
    problems: list[str] = []

    if not isinstance(fixture, dict):
        return [f"fixture must be a JSON object, got {type(fixture).__name__}"]

    if fixture.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {fixture.get('schema_version')!r}, expected {SCHEMA_VERSION}"
        )
    if fixture.get("key_allowlist_version") != KEY_ALLOWLIST_VERSION:
        problems.append(
            f"key_allowlist_version is {fixture.get('key_allowlist_version')!r}, but the "
            f"current allowlist is version {KEY_ALLOWLIST_VERSION}; every recorded 'key' "
            "index now means a different key"
        )

    seed = fixture.get("seed")
    if _is_plain_int(seed):
        record = generator.generate_task(seed)
        if fixture.get("task_id") != record["task_id"]:
            problems.append(
                f"task_id is {fixture.get('task_id')!r}, but seed {seed} now generates "
                f"{record['task_id']!r}"
            )
        expected_hash = _task_spec_sha256(record)
        if fixture.get("task_spec_sha256") != expected_hash:
            problems.append(
                f"task_spec_sha256 is {fixture.get('task_spec_sha256')!r}, but seed {seed} "
                f"now hashes to {expected_hash!r}"
            )
    else:
        problems.append(f"seed must be an int, got {seed!r}")

    backend = FakeBackend()
    screen = {"width": backend.width, "height": backend.height}
    if fixture.get("screen") != screen:
        problems.append(
            f"screen is {fixture.get('screen')!r}, but the backend is {screen!r}; the "
            "recorded pixel coordinates no longer mean what they meant"
        )

    actions = fixture.get("actions")
    if not isinstance(actions, list) or not actions:
        problems.append(
            f"actions must be a non-empty list, got {type(actions).__name__}"
            if not isinstance(actions, list)
            else "actions must be a non-empty list, got an empty one"
        )
        return problems

    if fixture.get("action_count") != len(actions):
        problems.append(
            f"action_count is {fixture.get('action_count')!r} but there are {len(actions)} actions"
        )

    action_problems, valid_actions = _action_problems(actions, backend.width, backend.height)
    problems += action_problems

    used = {action["action_type"] for action in valid_actions}
    missing = {int(member) for member in ActionType} - used
    if missing:
        problems.append(
            "the trajectory does not exercise every declared action type; missing "
            + ", ".join(sorted(ActionType(value).name for value in missing))
        )

    timeline = fixture.get("timeline")
    if not isinstance(timeline, list) or len(timeline) != len(actions):
        problems.append(
            f"timeline must hold one record per action ({len(actions)}), got "
            f"{len(timeline) if isinstance(timeline, list) else type(timeline).__name__}"
        )
        return problems

    problems += _timeline_record_problems(timeline, len(actions))
    return problems


def _timeline_shape_problems(timeline: list[dict[str, Any]]) -> list[str]:
    """Check the required reward shape independently of replay.

    This is what stops the fixture from becoming a snapshot of whatever the
    code happens to do: `verify_fixture` compares the replay to the recorded
    timeline *and* requires the recorded timeline to be zeros-then-exactly-one
    with a single terminating final step. Regenerating after a reward-timing bug
    fails here rather than quietly blessing the bug.
    """
    problems: list[str] = []
    rewards = [step["reward"] for step in timeline]
    expected = [0.0] * (len(timeline) - 1) + [1.0]
    if rewards != expected:
        paid = [step["step"] for step in timeline if step["reward"] != 0.0]
        problems.append(
            f"recorded reward vector is not [0, ..., 0, 1]; nonzero rewards at steps {paid}"
        )
    for step in timeline[:-1]:
        if step["terminated"] or step["truncated"]:
            problems.append(
                f"step {step['step']} of {len(timeline)} ends the episode early "
                f"(terminated={step['terminated']}, truncated={step['truncated']})"
            )
    last = timeline[-1]
    if not last["terminated"]:
        problems.append(f"final step {last['step']} does not terminate")
    if last["truncated"]:
        problems.append(
            f"final step {last['step']} truncates; success must terminate, not truncate"
        )
    return problems


def _evaluator_agreement_problems(evidence: dict[str, Any]) -> list[str]:
    """Reward and privileged evaluator state must tell the same story.

    Reward is only trustworthy if it tracks the host-side evaluator rather than
    anything the episode could have faked. A run that pays out `1.0` and
    terminates while the evaluator still reports no submission is exactly the
    reward-hacking shape this project exists to rule out, so it is rejected here
    even though its reward timeline is perfect.
    """
    problems: list[str] = []
    steps = evidence["steps"]
    final_step = steps[-1]

    for step in steps[:-1]:
        if step["evaluation"]["success"]:
            problems.append(
                f"the evaluator already reported success at step {step['step']} of "
                f"{len(steps)}, before the final action"
            )
            break

    if final_step["reward"] != 1.0:
        problems.append(f"final step pays {final_step['reward']}, expected 1.0")
    if not final_step["terminated"]:
        problems.append("final step does not set terminated=True")
    if final_step["truncated"]:
        problems.append("final step sets truncated=True; success must terminate, not truncate")

    final = evidence["final_evaluation"]
    if not final.submitted:
        problems.append(
            "reward fired but the privileged evaluator saw no submission "
            "(submitted=False) -- the payout is not backed by recorded state"
        )
    if not final.success:
        problems.append(
            "reward fired but the privileged evaluator reports success=False -- "
            "the payout is not backed by an exactly-matching submission"
        )
    if not final.task_id_matches:
        problems.append("the evaluator reports task_id_matches=False for the final submission")
    if final.mismatched_fields:
        problems.append(f"the evaluator reports mismatched fields {list(final.mismatched_fields)}")

    if evidence["submission_count"] != 1:
        problems.append(
            f"the trajectory recorded {evidence['submission_count']} submissions, expected "
            "exactly 1"
        )

    if evidence["post_terminal_raises"] is not True:
        problems.append(
            "stepping after the episode ended did not raise the documented reset-required error"
        )

    return problems


def verify_fixture(fixture: dict[str, Any]) -> list[str]:
    """Every problem with `fixture`, as a list of human-readable strings.

    Empty means: the fixture is structurally sound; its recorded timeline has
    the required shape; a blind replay reproduces that timeline exactly; a
    second replay is deterministic down to the observation hashes; and the
    privileged evaluator independently agrees about when success happened, on
    the basis of exactly one recorded submission.
    """
    problems = _structural_problems(fixture)
    if problems:
        return problems

    timeline = fixture["timeline"]
    problems += _timeline_shape_problems(timeline)

    try:
        blind = replay(fixture)
        # The second pass is the determinism check *and* the evidence source.
        # The timeline authority stays the blind pass above.
        evidence = replay(fixture, collect_evidence=True)
    except (ReplayError, InvalidActionError, ValueError, TypeError, KeyError) as error:
        return [*problems, f"replay failed: {type(error).__name__}: {error}"]

    observed = timeline_of(blind["steps"])
    if observed != timeline:
        for recorded, actual in zip(timeline, observed, strict=True):
            if recorded != actual:
                problems.append(
                    f"step {actual['step']}: replay produced {actual!r}, "
                    f"fixture records {recorded!r}"
                )
                break

    if [step["observation_sha256"] for step in blind["steps"]] != [
        step["observation_sha256"] for step in evidence["steps"]
    ]:
        problems.append("two replays of the same seed produced different observation hashes")
    if timeline_of(evidence["steps"]) != observed:
        problems.append("two replays of the same seed produced different reward timelines")

    problems += _evaluator_agreement_problems(evidence)
    return problems


# -- Human-readable trace ---------------------------------------------------


def _describe_key(key: str) -> str:
    return key if len(key) == 1 else f"<{key}>"


def _label_actions(fixture: dict[str, Any]) -> list[str]:
    """A short label per action: the widget a click lands on, the key a
    keystroke sends. Privileged (it reads the layout and live focus state), and
    used only for the human-readable trace -- never for replay."""
    backend = FakeBackend()
    backend.reset(fixture["seed"])
    labels: list[str] = []

    for action in fixture["actions"]:
        action_type = ActionType(action["action_type"])
        if action_type is ActionType.NOOP:
            labels.append("NOOP")
            continue
        if action_type is ActionType.KEY:
            key = KEY_ALLOWLIST[action["key"]]
            labels.append(f"KEY {_describe_key(key)!r}")
            backend.key(key)
            continue

        x, y = action["x"], action["y"]
        hit = backend.layout.hit_test(x, y)
        if hit is None:
            target = "(background)"
        else:
            widget, index = hit
            target = widget.value
            if index is not None:
                target = f"{widget.value}={backend.form.payment_options[index]}"
        labels.append(f"CLICK({x},{y}) {target}")
        backend.click(x, y)

    return labels


def _collapse(steps: list[dict[str, Any]], labels: list[str]) -> list[dict[str, Any]]:
    """Fold consecutive keystrokes into one row each.

    A run is only collapsed when every step in it is uneventful -- reward
    `0.0`, not terminated, not truncated. A collapsed row therefore can never
    hide a payout or an episode ending.
    """

    def uneventful_key(step: dict[str, Any]) -> bool:
        return (
            step["action"]["action_type"] == "KEY"
            and step["reward"] == 0.0
            and not step["terminated"]
            and not step["truncated"]
        )

    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(steps):
        step, label = steps[index], labels[index]

        if uneventful_key(step):
            run = index
            typed: list[str] = []
            while run < len(steps) and uneventful_key(steps[run]):
                typed.append(labels[run].removeprefix("KEY ").strip("'"))
                run += 1
            if run - index > 1:
                rows.append(
                    {
                        "steps": f"{steps[index]['step']}-{steps[run - 1]['step']}",
                        "action": f"KEY x{run - index}  {''.join(typed)!r}",
                        "reward": 0.0,
                        "terminated": False,
                        "truncated": False,
                    }
                )
                index = run
                continue

        rows.append(
            {
                "steps": str(step["step"]),
                "action": label,
                "reward": step["reward"],
                "terminated": step["terminated"],
                "truncated": step["truncated"],
            }
        )
        index += 1
    return rows


def _table(rows: list[dict[str, Any]]) -> str:
    header = f"{'step':>9}  {'action':<52} {'reward':>6}  {'term':<5}  {'trunc':<5}"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['steps']:>9}  {row['action']:<52} {row['reward']:>6.1f}  "
            f"{row['terminated']!s:<5}  {row['truncated']!s:<5}"
        )
    return "\n".join(lines)


def render_markdown(fixture: dict[str, Any], evidence: dict[str, Any], output_dir: Path) -> str:
    """The human-readable trace, generated entirely from `evidence` -- the same
    structured record written to the JSON artifact. Nothing here is maintained
    by hand, so the report and the evidence cannot disagree.

    Every label, filename, and reproduction command is derived from the run
    block, so a seed-11 report never claims to be about seed 7.
    """
    run = evidence["run"]
    summary = evidence["summary"]
    steps = evidence["steps"]
    labels = _label_actions(fixture)
    final = evidence["final_evaluation"]

    seed = run["seed"]
    stem = artifact_stem(seed)
    fixture_path = run["fixture"]
    output = _rel(output_dir)

    lines = [
        f"# Golden trajectory — seed {seed}",
        "",
        "Generated by `scripts/golden_trajectory.py artifacts`. Do not edit by hand;",
        f"every value below is read from `{stem}.json`, which is produced by replaying",
        f"`{fixture_path}` blind. Regenerate rather than correct.",
        "",
        "## Run",
        "",
        "| | |",
        "|---|---|",
        f"| backend | `{run['backend']}` |",
        f"| seed | `{seed}` |",
        f"| fixture | `{fixture_path}` |",
        f"| task ID | `{run['task_id']}` |",
        f"| task spec SHA-256 | `{run['task_spec_sha256']}` |",
        f"| key allowlist version | `{run['key_allowlist_version']}` |",
        f"| screen | `{run['screen']['width']}x{run['screen']['height']}` |",
        f"| actions | `{run['action_count']}` |",
        f"| git commit | `{run['git_commit']}` |",
        f"| final frame | `{stem}-final.png` (SHA-256 `{run['final_frame_sha256']}`) |",
        "",
        "## Trace (keystroke runs collapsed)",
        "",
        "A run of keystrokes is collapsed into one row only when every step in it",
        "scored `0.0` and ended nothing, so no collapsed row can hide a payout.",
        "",
        "```text",
        _table(_collapse(steps, labels)),
        "```",
        "",
        "## Reward",
        "",
        f"- observed reward vector: `{_compact_vector(summary['reward_vector'])}`",
        f"- first positive reward at step: `{summary['first_positive_reward_step']}`",
        f"- terminal step: `{summary['terminal_step']}` of `{run['action_count']}`",
        f"- total reward: `{summary['total_reward']}`",
        f"- submissions recorded: `{summary['submission_count']}`",
        f"- stepping after the episode ended raised: `{summary['post_terminal_raises']}`",
        "",
        "## Final evaluator result",
        "",
        "Host-side `EvaluationResult` after the last action. Field *names* only —",
        "the expected values never enter this artifact.",
        "",
        "| | |",
        "|---|---|",
        f"| success | `{final['success']}` |",
        f"| submitted | `{final['submitted']}` |",
        f"| task_id_matches | `{final['task_id_matches']}` |",
        f"| mismatched_field_names | `{final['mismatched_field_names']}` |",
        "",
        "## Reproduce",
        "",
        "```bash",
        f"python scripts/golden_trajectory.py verify --fixture {fixture_path}",
        "```",
        "",
        "```bash",
        (
            f"python scripts/golden_trajectory.py artifacts "
            f"--fixture {fixture_path} --output {output}"
        ),
        "```",
        "",
        "## Full per-step table",
        "",
        "```text",
        _table(
            [
                {
                    "steps": str(step["step"]),
                    "action": label,
                    "reward": step["reward"],
                    "terminated": step["terminated"],
                    "truncated": step["truncated"],
                }
                for step, label in zip(steps, labels, strict=True)
            ]
        ),
        "```",
        "",
    ]
    return "\n".join(lines)


def _compact_vector(rewards: list[float]) -> str:
    """`[0.0 x108, 1.0]` rather than 109 literal numbers."""
    parts: list[str] = []
    index = 0
    while index < len(rewards):
        run = index
        while run < len(rewards) and rewards[run] == rewards[index]:
            run += 1
        count = run - index
        parts.append(f"{rewards[index]}" if count == 1 else f"{rewards[index]} x{count}")
        index = run
    return "[" + ", ".join(parts) + "]"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# -- Evidence bundle --------------------------------------------------------


def build_evidence(
    fixture: dict[str, Any], final_frame_sha256: str, fixture_path: Path
) -> dict[str, Any]:
    """The structured evidence record, derived entirely from `fixture`.

    `fixture_path` is recorded as given, so the artifact names the file it was
    actually built from rather than assuming the committed one.
    """
    observed = replay(fixture, collect_evidence=True)
    steps = observed["steps"]
    rewards = [step["reward"] for step in steps]
    positive = [step["step"] for step in steps if step["reward"] > 0.0]
    terminal = [step["step"] for step in steps if step["terminated"] or step["truncated"]]
    final = observed["final_evaluation"]

    return {
        "schema_version": SCHEMA_VERSION,
        "note": (
            "D1.7 evidence. Generated by replaying the golden-trajectory fixture named "
            "in run.fixture blind; not maintained by hand. Evaluator diagnostics carry "
            "field names only, never expected values."
        ),
        "run": {
            "backend": "fake",
            "seed": fixture["seed"],
            "task_id": observed["task_id"],
            "task_spec_sha256": fixture["task_spec_sha256"],
            "key_allowlist_version": fixture["key_allowlist_version"],
            "screen": fixture["screen"],
            "action_count": fixture["action_count"],
            "fixture": _rel(fixture_path),
            "git_commit": git_provenance(),
            "final_frame_sha256": final_frame_sha256,
        },
        "steps": [
            {
                "step": step["step"],
                "action": _action_evidence(fixture["actions"][step["step"] - 1]),
                "observation_sha256": step["observation_sha256"],
                "evaluation": step["evaluation"],
                "reward": step["reward"],
                "terminated": step["terminated"],
                "truncated": step["truncated"],
            }
            for step in steps
        ],
        "summary": {
            "reward_vector": rewards,
            "first_positive_reward_step": positive[0] if positive else None,
            "terminal_step": terminal[0] if terminal else None,
            "total_reward": sum(rewards),
            "submission_count": observed["submission_count"],
            "post_terminal_raises": observed["post_terminal_raises"],
        },
        "final_evaluation": {
            "success": final.success,
            "submitted": final.submitted,
            "task_id_matches": final.task_id_matches,
            "mismatched_field_names": list(final.mismatched_fields),
        },
    }


def _action_evidence(action: dict[str, int]) -> dict[str, Any]:
    """One action, rendered with its type name and only the fields that type
    actually reads (`pixelgym.actions.ACTIVE_FIELDS`)."""
    action_type = ActionType(action["action_type"])
    if action_type is ActionType.CLICK:
        return {"action_type": action_type.name, "x": action["x"], "y": action["y"]}
    if action_type is ActionType.KEY:
        return {
            "action_type": action_type.name,
            "key": action["key"],
            "key_name": KEY_ALLOWLIST[action["key"]],
        }
    return {"action_type": action_type.name}


# -- Commands ---------------------------------------------------------------


class FixtureLoadError(RuntimeError):
    """A fixture file could not be read or parsed."""


def _load(path: Path) -> dict[str, Any]:
    """Read and parse a fixture, raising `FixtureLoadError` for anything a user
    could plausibly hand this script -- a missing file, a directory, malformed
    JSON. Callers turn that into a concise FAIL rather than a traceback."""
    try:
        text = path.read_text()
    except OSError as error:
        raise FixtureLoadError(f"cannot read {path}: {error.strerror or error}") from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise FixtureLoadError(
            f"{path} is not valid JSON: {error.msg} (line {error.lineno}, column {error.colno})"
        ) from error


def _report(problems: list[str]) -> None:
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


def _fail(message: str) -> int:
    print(f"FAIL {message}", file=sys.stderr)
    return 1


def command_check(_args: argparse.Namespace) -> int:
    """Verify the committed fixture. Fixed to seed 7 on purpose: this is the
    canonical fixture check, and it asserts something about one specific
    committed artifact rather than whatever seed that file happens to name."""
    try:
        fixture = _load(COMMITTED_FIXTURE)
    except FixtureLoadError as error:
        return _fail(str(error))

    if not isinstance(fixture, dict) or fixture.get("seed") != COMMITTED_SEED:
        seed = fixture.get("seed") if isinstance(fixture, dict) else None
        return _fail(
            f"{_rel(COMMITTED_FIXTURE)} records seed {seed!r}; the committed golden "
            f"trajectory must be seed {COMMITTED_SEED}"
        )

    problems = verify_fixture(fixture)

    candidate = build_fixture_payload(COMMITTED_SEED)
    committed_text = canonical_fixture_json(fixture)
    candidate_text = canonical_fixture_json(candidate)
    drifted = committed_text != candidate_text

    if problems:
        print(f"FAIL {_rel(COMMITTED_FIXTURE)}", file=sys.stderr)
        _report(problems)
    if drifted:
        print(
            f"\nFAIL {_rel(COMMITTED_FIXTURE)} differs from what the solver now produces:",
            file=sys.stderr,
        )
        diff = difflib.unified_diff(
            committed_text.splitlines(keepends=True),
            candidate_text.splitlines(keepends=True),
            fromfile=f"committed/{COMMITTED_FIXTURE.name}",
            tofile=f"candidate/{COMMITTED_FIXTURE.name}",
        )
        sys.stderr.writelines(diff)
        print(
            "\nA diff is only acceptable if you can name the cause: a task-spec "
            "change, a layout change, a screen-size change, or an action-contract "
            "change. An unexplained coordinate or keystroke change should block "
            "acceptance.\n\nTo regenerate:\n\n"
            f"  python scripts/golden_trajectory.py generate --seed {COMMITTED_SEED} "
            f"--output /tmp/{COMMITTED_FIXTURE.name}\n"
            "  python scripts/golden_trajectory.py verify --fixture /tmp/"
            f"{COMMITTED_FIXTURE.name}\n"
            f"  diff -u {_rel(COMMITTED_FIXTURE)} /tmp/{COMMITTED_FIXTURE.name}\n"
            "  # then, only after reviewing that diff:\n"
            f"  cp /tmp/{COMMITTED_FIXTURE.name} {_rel(COMMITTED_FIXTURE)}\n"
            f"  python scripts/golden_trajectory.py artifacts --fixture "
            f"{_rel(COMMITTED_FIXTURE)} --output {_rel(DEFAULT_ARTIFACT_DIR)}",
            file=sys.stderr,
        )
    if problems or drifted:
        return 1

    steps = fixture["timeline"]
    print(f"OK   {_rel(COMMITTED_FIXTURE)}")
    print(f"     seed {fixture['seed']}, task {fixture['task_id']}, {len(steps)} actions")
    print(f"     reward vector {_compact_vector([step['reward'] for step in steps])}")
    print("     replayed twice blind; identical timeline and observation hashes")
    print("     evaluator agrees: no success before the final step, exactly 1 submission")
    print("     stepping after the episode ended raises the documented error")
    print("     matches the solver action-for-action")
    return 0


def command_generate(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    if output == COMMITTED_FIXTURE.resolve():
        print(
            f"FAIL refusing to write the committed fixture ({_rel(COMMITTED_FIXTURE)}) "
            "in place.\nWrite a candidate elsewhere, verify it, review the diff, then copy "
            "it over deliberately -- automatic overwriting would let a broken layout or "
            "generator silently update the golden oracle and make the tests pass.",
            file=sys.stderr,
        )
        return 2

    payload = build_fixture_payload(args.seed)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_fixture_json(payload))
    print(f"wrote {output} ({payload['action_count']} actions, seed {payload['seed']})")
    print(f"next: python scripts/golden_trajectory.py verify --fixture {output}")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    path = Path(args.fixture)
    try:
        fixture = _load(path)
    except FixtureLoadError as error:
        return _fail(str(error))

    problems = verify_fixture(fixture)
    if problems:
        print(f"FAIL {path}", file=sys.stderr)
        _report(problems)
        return 1
    print(f"OK   {path} replays blind to its recorded timeline")
    print("     evaluator agrees about when success happened; exactly 1 submission")
    return 0


def command_artifacts(args: argparse.Namespace) -> int:
    from PIL import Image

    path = Path(args.fixture)
    try:
        fixture = _load(path)
    except FixtureLoadError as error:
        return _fail(str(error))

    problems = verify_fixture(fixture)
    if problems:
        print(
            f"FAIL {path} is not a valid golden trajectory; not writing artifacts", file=sys.stderr
        )
        _report(problems)
        return 1

    # Names derive from the fixture's own seed, so a seed-11 bundle cannot be
    # emitted under seed-7 filenames or claims.
    stem = artifact_stem(fixture["seed"])
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    frame = replay(fixture)["final_observation"]
    png_path = output / f"{stem}-final.png"
    Image.fromarray(frame).save(png_path)
    frame_sha256 = hashlib.sha256(png_path.read_bytes()).hexdigest()

    evidence = build_evidence(fixture, frame_sha256, path)
    json_path = output / f"{stem}.json"
    json_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=True) + "\n")

    md_path = output / f"{stem}.md"
    md_path.write_text(render_markdown(fixture, evidence, output))

    for written in (json_path, md_path, png_path):
        print(f"wrote {_rel(written)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "check",
        help=f"verify the committed seed-{COMMITTED_SEED} fixture and detect drift; writes nothing",
    ).set_defaults(handler=command_check)

    generate = subparsers.add_parser(
        "generate", help="write a candidate fixture (never the committed one)"
    )
    generate.add_argument("--seed", type=int, default=COMMITTED_SEED)
    generate.add_argument("--output", required=True, help="path to write the candidate to")
    generate.set_defaults(handler=command_generate)

    verify = subparsers.add_parser("verify", help="blindly replay any candidate fixture")
    verify.add_argument("--fixture", default=str(COMMITTED_FIXTURE))
    verify.set_defaults(handler=command_verify)

    artifacts = subparsers.add_parser(
        "artifacts", help="write the evidence bundle from an accepted fixture"
    )
    artifacts.add_argument("--fixture", default=str(COMMITTED_FIXTURE))
    artifacts.add_argument("--output", default=str(DEFAULT_ARTIFACT_DIR))
    artifacts.set_defaults(handler=command_artifacts)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
