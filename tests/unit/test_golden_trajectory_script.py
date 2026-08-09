"""`scripts/golden_trajectory.py` as a maintenance workflow (D1.7).

`test_golden_trajectory.py` proves the *fixture* is correct. This file proves
the tooling around it behaves:

- `check` is a real gate: it fails on a tampered fixture, and it is pinned to
  the committed seed rather than to whatever seed a file happens to name.
- Verification proves reward and privileged evaluator state agree, and rejects
  a fabricated timeline that pays out without a recorded submission.
- Blind replay really is blind -- proven by replacing the script's `generator`
  dependency with a tripwire and replaying anyway.
- Regeneration cannot overwrite the committed golden oracle in place, which is
  the one failure that would make every other test in the suite meaningless.
- Malformed input produces a concise `FAIL` and a nonzero exit, not a
  traceback.
- Artifacts derive their names and claims from the fixture's own seed.

The script is loaded by path (it lives in `scripts/`, which is not an
importable package) and driven in-process; the CLI-surface tests shell out, to
confirm the documented command line is the one that exists and that failures
stay legible.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pixelgym.backends.fake import FakeBackend

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "golden_trajectory.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("golden_trajectory_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *argv],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


# -- check is a real gate ---------------------------------------------------


def test_check_passes_against_the_committed_fixture(script):
    """The Day 1 gate command. If this fails, the committed fixture and the
    current code disagree and the printed diff says how."""
    assert script.main(["check"]) == 0


def test_committed_fixture_verifies_clean(script, golden_trajectory):
    assert script.verify_fixture(golden_trajectory) == []


def test_check_is_pinned_to_the_committed_seed(script, tmp_path, monkeypatch, capsys):
    """`check` asserts something about one specific committed artifact, so it
    must not quietly follow whatever seed the file names. A seed-11 fixture
    installed at the committed path is a failure, not a different valid run."""
    eleven = tmp_path / "eleven.json"
    assert script.main(["generate", "--seed", "11", "--output", str(eleven)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(script, "COMMITTED_FIXTURE", eleven)

    exit_code = script.main(["check"])

    assert exit_code == 1
    assert f"must be seed {script.COMMITTED_SEED}" in capsys.readouterr().err


# -- ...which means it must fail on things that are actually wrong ----------


def test_a_tampered_reward_is_rejected(script, golden_trajectory):
    """The recorded timeline is not taken on faith: pay out one step early and
    the independent shape check catches it even though the fixture is
    self-consistent about it."""
    tampered = copy.deepcopy(golden_trajectory)
    tampered["timeline"][0]["reward"] = 1.0

    problems = script.verify_fixture(tampered)

    assert any("reward vector is not" in problem for problem in problems)


def test_a_timeline_that_disagrees_with_replay_is_rejected(script, golden_trajectory):
    tampered = copy.deepcopy(golden_trajectory)
    tampered["timeline"][-1]["terminated"] = False

    problems = script.verify_fixture(tampered)

    assert any("does not terminate" in problem for problem in problems)


def test_a_dropped_action_type_is_rejected(script, golden_trajectory):
    """The leading NOOP is the only NOOP; without it the replay stops covering
    one of the three declared action types."""
    tampered = copy.deepcopy(golden_trajectory)
    tampered["actions"] = tampered["actions"][1:]
    tampered["action_count"] -= 1
    tampered["timeline"] = tampered["timeline"][1:]

    problems = script.verify_fixture(tampered)

    assert any("does not exercise every declared action type" in problem for problem in problems)


def test_an_out_of_bounds_coordinate_is_rejected(script, golden_trajectory):
    tampered = copy.deepcopy(golden_trajectory)
    tampered["actions"][1]["x"] = tampered["screen"]["width"]

    problems = script.verify_fixture(tampered)

    assert any("outside the declared space" in problem for problem in problems)


def test_a_stale_key_allowlist_version_is_rejected(script, golden_trajectory):
    """Every recorded `key` is an index. Bump the allowlist and each index
    silently means a different key, so the fixture must be refused rather than
    replayed."""
    tampered = copy.deepcopy(golden_trajectory)
    tampered["key_allowlist_version"] += 1

    problems = script.verify_fixture(tampered)

    assert any("key_allowlist_version" in problem for problem in problems)


def test_a_task_hash_from_a_different_generator_is_rejected(script, golden_trajectory):
    tampered = copy.deepcopy(golden_trajectory)
    tampered["task_spec_sha256"] = "0" * 64

    problems = script.verify_fixture(tampered)

    assert any("task_spec_sha256" in problem for problem in problems)


# -- Reward must agree with privileged evaluator state ----------------------


class _RewardWithoutSubmissionEnv:
    """An environment that fabricates the golden reward timeline exactly, but
    never drives the backend -- so no submission is ever recorded.

    Everything else is faithful: five-value step results, a stable observation,
    `terminated` only on the last action, and the documented reset-required
    error afterwards. That leaves the disagreement with the privileged
    evaluator as the *only* thing verification could object to, which is what
    makes this a test of that check rather than of anything incidental.
    """

    total_actions = 0

    def __init__(self, backend: FakeBackend) -> None:
        self._backend = backend
        self._seen = 0
        self._ended = False

    def reset(self, *, seed: int | None = None, options: Any = None):
        self._backend.reset(seed)
        self._seen = 0
        self._ended = False
        return self._observation(), {"task_id": "vf-fabricated"}

    def step(self, action):
        if self._ended:
            raise RuntimeError(
                "step() called before reset() or after the episode already ended "
                "(terminated or truncated); call reset() before stepping again."
            )
        self._seen += 1
        last = self._seen == self.total_actions
        self._ended = last
        return self._observation(), 1.0 if last else 0.0, last, False, {"task_id": "vf-fabricated"}

    def close(self) -> None:
        pass

    def _observation(self) -> np.ndarray:
        return self._backend.screenshot()


def test_a_fabricated_reward_timeline_without_a_submission_is_rejected(
    script, golden_trajectory, monkeypatch
):
    """The reward-hacking shape this project exists to rule out: a perfect
    `[0, ..., 0, 1]` timeline that terminates on cue while the privileged
    evaluator never saw a submission. The timeline alone is indistinguishable
    from the real one, so only the evaluator cross-check can reject it."""
    _RewardWithoutSubmissionEnv.total_actions = len(golden_trajectory["actions"])
    monkeypatch.setattr(script, "PixelGuiEnv", _RewardWithoutSubmissionEnv)

    problems = script.verify_fixture(golden_trajectory)

    assert any("saw no submission" in problem for problem in problems)
    assert any("recorded 0 submissions, expected exactly 1" in problem for problem in problems)


def test_the_committed_trajectory_satisfies_the_evaluator_agreement_checks(
    script, golden_trajectory
):
    evidence = script.replay(golden_trajectory, collect_evidence=True)

    assert script._evaluator_agreement_problems(evidence) == []
    assert evidence["submission_count"] == 1
    assert evidence["post_terminal_raises"] is True
    assert not any(step["evaluation"]["success"] for step in evidence["steps"][:-1])
    assert evidence["final_evaluation"].success is True


# -- Blind replay is blind in fact, not only in the docstring ---------------


class _Tripwire:
    """Raises on any attribute access, so a single lookup is a loud failure."""

    def __getattr__(self, name: str):
        raise AssertionError(f"blind replay reached for the task generator: generator.{name}")


def test_blind_replay_never_touches_the_script_level_task_generator(
    script, golden_trajectory, monkeypatch
):
    """Only the *script's* `generator` name is replaced. `FakeBackend` keeps
    its own import and stays free to build the task during `reset`; what is
    being proven here is that the replay path does not read the answer, not
    that the backend stops doing its job."""
    monkeypatch.setattr(script, "generator", _Tripwire())

    observed = script.replay(golden_trajectory)

    rewards = [step["reward"] for step in observed["steps"]]
    assert rewards == [0.0] * (len(rewards) - 1) + [1.0]
    assert observed["post_terminal_raises"] is True


def test_the_tripwire_would_actually_have_caught_a_generator_lookup(
    script, golden_trajectory, monkeypatch
):
    """Guards the test above from passing vacuously: the evidence path *does*
    build a TaskSpec from the generator, so with the tripwire installed it must
    fail. If this ever stops raising, the tripwire has stopped working."""
    monkeypatch.setattr(script, "generator", _Tripwire())

    with pytest.raises(AssertionError, match="reached for the task generator"):
        script.replay(golden_trajectory, collect_evidence=True)


# -- Regeneration cannot overwrite the oracle -------------------------------


def test_generate_refuses_to_write_the_committed_fixture(script, capsys):
    """The single most important property of this workflow. Automatic
    overwriting would let a broken layout update the golden oracle and make the
    whole suite pass by moving the goalposts."""
    before = script.COMMITTED_FIXTURE.read_bytes()

    exit_code = script.main(["generate", "--seed", "7", "--output", str(script.COMMITTED_FIXTURE)])

    assert exit_code == 2
    assert "refusing to write the committed fixture" in capsys.readouterr().err
    assert script.COMMITTED_FIXTURE.read_bytes() == before


def test_generate_writes_a_candidate_that_verifies(script, tmp_path):
    output = tmp_path / "candidate.json"

    assert script.main(["generate", "--seed", "7", "--output", str(output)]) == 0
    assert script.verify_fixture(json.loads(output.read_text())) == []


def test_regenerating_seed_7_reproduces_the_committed_fixture_byte_for_byte(script):
    """Canonical serialization with no timestamps: an unchanged trajectory must
    regenerate identically, or `check`'s diff would be pure noise."""
    candidate = script.canonical_fixture_json(script.build_fixture_payload(7))

    assert candidate == script.COMMITTED_FIXTURE.read_text()


def test_a_different_seed_produces_a_different_trajectory(script):
    seven = script.build_fixture_payload(7)
    eleven = script.build_fixture_payload(11)

    assert seven["task_id"] != eleven["task_id"]
    assert seven["actions"] != eleven["actions"]


# -- Artifacts describe the fixture they were built from --------------------


def test_artifacts_for_seed_11_cannot_be_emitted_under_seed_7_names_or_claims(script, tmp_path):
    """Artifact naming and every claim inside the bundle derive from the
    fixture's own seed. A hard-coded stem would silently overwrite the
    committed seed-7 evidence with a seed-11 run."""
    fixture_path = tmp_path / "candidate11.json"
    assert script.main(["generate", "--seed", "11", "--output", str(fixture_path)]) == 0
    output = tmp_path / "artifacts"

    assert script.main(["artifacts", "--fixture", str(fixture_path), "--output", str(output)]) == 0

    assert sorted(path.name for path in output.iterdir()) == [
        "golden-trajectory-seed-11-final.png",
        "golden-trajectory-seed-11.json",
        "golden-trajectory-seed-11.md",
    ]
    assert list(output.glob("*seed-7*")) == []

    evidence = json.loads((output / "golden-trajectory-seed-11.json").read_text())
    assert evidence["run"]["seed"] == 11
    assert evidence["run"]["fixture"] == str(fixture_path)

    markdown = (output / "golden-trajectory-seed-11.md").read_text()
    assert "# Golden trajectory — seed 11" in markdown
    assert "seed 7" not in markdown
    assert str(fixture_path) in markdown
    assert str(output) in markdown


def test_evidence_records_the_fixture_path_it_was_given(script, tmp_path):
    """Not the `COMMITTED_FIXTURE` constant: evidence must name the file it was
    actually built from."""
    fixture_path = tmp_path / "elsewhere.json"
    assert script.main(["generate", "--seed", "7", "--output", str(fixture_path)]) == 0

    evidence = script.build_evidence(json.loads(fixture_path.read_text()), "0" * 64, fixture_path)

    assert evidence["run"]["fixture"] == str(fixture_path)


# -- Malformed input fails concisely ----------------------------------------


def test_an_action_missing_action_type_is_rejected(script, golden_trajectory):
    """Fields are validated before anything indexes them; a missing key must
    be reported, not raised as a `KeyError` out of the validator."""
    tampered = copy.deepcopy(golden_trajectory)
    del tampered["actions"][1]["action_type"]

    problems = script.verify_fixture(tampered)

    assert any("action 2" in problem and "action_type" in problem for problem in problems)


def test_an_empty_timeline_record_is_rejected(script, golden_trajectory):
    tampered = copy.deepcopy(golden_trajectory)
    tampered["timeline"][0] = {}

    problems = script.verify_fixture(tampered)

    assert any("timeline record 1" in problem for problem in problems)


def test_a_non_integer_action_field_is_rejected(script, golden_trajectory):
    tampered = copy.deepcopy(golden_trajectory)
    tampered["actions"][1]["x"] = "768"

    problems = script.verify_fixture(tampered)

    assert any("is not a plain int" in problem for problem in problems)


def test_a_trajectory_that_ends_before_its_last_action_is_rejected(script, golden_trajectory):
    """An extra action after the terminating Submit cannot be replayed. The
    replay failure is reported as a problem, not raised."""
    tampered = copy.deepcopy(golden_trajectory)
    tampered["actions"].append(copy.deepcopy(tampered["actions"][0]))
    tampered["action_count"] += 1
    tampered["timeline"].append(
        {
            "step": len(tampered["actions"]),
            "action_type": "NOOP",
            "reward": 0.0,
            "terminated": False,
            "truncated": False,
        }
    )

    problems = script.verify_fixture(tampered)

    assert any("replay failed" in problem for problem in problems)


@pytest.mark.parametrize(
    ("contents", "expected"),
    [("{not json", "not valid JSON"), ("[]", "must be a JSON object")],
)
def test_unusable_fixture_files_fail_without_a_traceback(tmp_path, contents, expected):
    path = tmp_path / "broken.json"
    path.write_text(contents)

    result = _run_cli("verify", "--fixture", str(path))

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "FAIL" in result.stderr
    assert expected in result.stderr


def test_a_missing_fixture_file_fails_without_a_traceback(tmp_path):
    result = _run_cli("verify", "--fixture", str(tmp_path / "nope.json"))

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "cannot read" in result.stderr


# -- The documented command line is the one that exists ---------------------


@pytest.mark.parametrize("command", ["check", "generate", "verify", "artifacts"])
def test_documented_subcommands_exist(command):
    result = _run_cli(command, "--help")

    assert result.returncode == 0, result.stderr
