"""The gate's own guards, tested: the skip guard, the clean-tree check, the mutant manifest and the workflow rules. A guard that cannot fail is not a guard (docs/CI.md)."""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / "tools" / "ci"


def load(name):
    spec = importlib.util.spec_from_file_location(name, CI / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.test_a" name="test_ok" time="0.1"/>
<testcase classname="tests.test_travel" name="tests.test_travel" time="0"><skipped type="pytest.skip" message="could not import 'fli': No module named 'fli'">x</skipped></testcase>
<testcase classname="tests.test_documents" name="test_real_engine" time="0"><skipped type="pytest.skip" message="set EVENTMATE_DOCLING_PYTHON to an interpreter that has Docling">x</skipped></testcase>
</testsuite></testsuites>"""


def test_the_skip_guard_fails_on_a_missing_extra_and_passes_only_what_is_on_the_allow_list(tmp_path):
    guard = load("skip_guard")
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT)
    allow = tmp_path / "allow.txt"
    allow.write_text("# opt-in\ntests.test_documents::test_real_engine | set EVENTMATE_DOCLING_PYTHON\n")
    bad, _ = guard.check(junit, allow)
    assert len(bad) == 1 and bad[0].startswith("tests.test_travel::tests.test_travel") and "could not import 'fli'" in bad[0], "R-018: 40 tests skipped for a missing extra"
    assert guard.main(["x", str(junit), str(allow)]) == 1
    allow.write_text(allow.read_text() + "tests.test_travel::* | could not import 'fli'\n")
    assert guard.check(junit, allow)[0] == [] and guard.main(["x", str(junit), str(allow)]) == 0


def test_a_red_build_names_its_failures_in_a_file_the_log_and_the_step_summary(tmp_path, monkeypatch):
    junit = tmp_path / "j.xml"
    junit.write_text("""<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.test_a" name="test_ok"/>
<testcase classname="tests.test_payments" name="test_a_lost_answer[reset]"><failure message="assert 'failed' == 'outcome_unknown'">tb</failure></testcase>
<testcase classname="tests.test_b" name="test_boom"><error message="collection error">tb</error></testcase></testsuite></testsuites>""")
    summary = tmp_path / "summary.md"
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary)}
    out = subprocess.run([str(CI / "failed_tests.py"), str(junit), str(tmp_path / "failed.txt")], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "2 test(s) failed or errored" in out.stdout and "tests.test_payments::test_a_lost_answer[reset]" in out.stdout
    lines = (tmp_path / "failed.txt").read_text().splitlines()
    assert len(lines) == 2 and "[failure]" in lines[0] and "[error]" in lines[1]
    assert "tests.test_b::test_boom" in summary.read_text()
    junit.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1"><testcase classname="tests.test_a" name="test_ok"/></testsuite></testsuites>')
    subprocess.run([str(CI / "failed_tests.py"), str(junit), str(tmp_path / "failed.txt")], env=env, capture_output=True, text=True)
    assert (tmp_path / "failed.txt").read_text() == ""
    assert "-rfEs" in (CI / "tests.sh").read_text() and "failed.txt" in (ROOT / ".github/workflows/ci.yml").read_text()


def test_a_half_installed_fli_fails_up_front_with_the_fix(tmp_path):
    pkg = tmp_path / "libs" / "fli"
    (pkg / "search").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "models.py").write_text("Airport = FlightSearchFilters = FlightSegment = MaxStops = PassengerInfo = SeatType = SortBy = DateSearchFilters = object\n")
    (pkg / "search" / "__init__.py").write_text("import babel_that_is_not_installed\nSearchFlights = SearchDates = object\n")  # `import fli` works; the submodule does not
    (pkg / "search" / "exceptions.py").write_text("SearchClientError = Exception\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "libs")}
    out = subprocess.run([sys.executable, str(CI / "check_fli.py")], env=env, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 1 and "fli.search" in out.stdout and "babel_that_is_not_installed" in out.stdout and "make ci-tools" in out.stdout
    assert "check_fli.py" in (CI / "require_tools.sh").read_text() and "requirements.lock" in (ROOT / "Makefile").read_text()
    ok = subprocess.run([sys.executable, str(CI / "check_fli.py")], capture_output=True, text=True, cwd=ROOT)
    assert ok.returncode == 0, ok.stdout


def run_matrix(tmp_path, **extra):
    """matrix.sh against a scratch repository, with a fake `uv` that cannot provide any Python (an offline laptop)."""
    repo = scratch_repo(tmp_path)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "uv").write_text("#!/bin/sh\necho 'no network' >&2\nexit 2\n")
    (bin_ / "uv").chmod(0o755)
    env = {**os.environ, "CI_ROOT": str(repo), "CI_OUT": str(tmp_path / "out"), "PATH": f"{bin_}:{os.environ['PATH']}", "MATRIX_VERSIONS": "3.11 3.12 3.13 3.14 3.15", "MATRIX_VENVS": str(tmp_path / "venvs"), **extra}
    return subprocess.run(["bash", str(CI / "matrix.sh")], env=env, capture_output=True, text=True)


def test_a_python_version_the_gate_cannot_set_up_is_a_failure_not_a_pass(tmp_path):
    out = run_matrix(tmp_path)
    here = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert out.returncode == 1, out.stdout
    assert all(f"py{v}" in out.stdout and f"FAIL: uv could not provide Python {v}" in out.stdout for v in ("3.11", "3.12", "3.13", "3.14", "3.15") if v != here)
    assert f"this is the main interpreter" in out.stdout


def test_the_matrix_can_be_told_to_accept_a_missing_version_and_says_not_run_out_loud(tmp_path):
    out = run_matrix(tmp_path, MATRIX_SKIP_MISSING="1")
    assert out.returncode == 0 and "NOT RUN: uv could not provide Python" in out.stdout and "NOT RUN: Python" in out.stdout and "the gate did not cover them" in out.stdout


def test_make_ci_runs_the_matrix_and_it_covers_every_python_the_workflow_does():
    makefile = (ROOT / "Makefile").read_text()
    assert re.search(r"^ci:\n\t.*ci-tests ci-matrix ci-mutants", makefile, re.M) and "ci-matrix:\n\ttools/ci/matrix.sh" in makefile
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    versions = [str(v) for v in workflow["jobs"]["tests"]["strategy"]["matrix"]["python"]]
    default = re.search(r'MATRIX_VERSIONS:-([0-9. ]+)\}', (CI / "matrix.sh").read_text()).group(1).split()
    assert versions == default, "the local matrix and the CI matrix must be the same versions"
    requires = re.search(r'requires-python = ">=(\d+\.\d+)"', (ROOT / "pyproject.toml").read_text()).group(1)
    assert versions[0] == requires, "the oldest supported Python is tested"


def test_an_allow_list_line_needs_the_reason_to_match_too(tmp_path):
    guard = load("skip_guard")
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT)
    allow = tmp_path / "allow.txt"
    allow.write_text("tests.test_documents::test_real_engine | a different reason\ntests.test_travel::* | could not import 'fli'\n")
    bad, unused = guard.check(junit, allow)
    assert len(bad) == 1 and "test_real_engine" in bad[0] and unused == ["tests.test_documents::test_real_engine | a different reason"]


def test_the_shipped_allow_list_is_exactly_the_four_opt_in_tests():
    lines = [ln for ln in (CI / "allowed_skips.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(lines) == 4, lines
    assert {ln.split("|")[0].strip().split("::")[0] for ln in lines} == {"tests.test_documents", "tests.test_travel", "tests.test_voice_real_engine"}


def scratch_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t.test"], ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)
    return repo


def clean_tree(repo, tmp_path, what):
    env = {**os.environ, "CI_ROOT": str(repo), "CI_OUT": str(tmp_path / "out")}
    return subprocess.run([str(CI / "clean_tree.sh"), what], env=env, capture_output=True, text=True)


def test_the_clean_tree_check_fails_when_a_test_writes_a_file_or_changes_a_tracked_one(tmp_path):
    repo = scratch_repo(tmp_path)
    assert clean_tree(repo, tmp_path, "snapshot").returncode == 0
    assert clean_tree(repo, tmp_path, "verify").returncode == 0
    (repo / "left-behind.json").write_text("{}")
    out = clean_tree(repo, tmp_path, "verify")
    assert out.returncode == 1 and "left-behind.json" in out.stdout
    (repo / "left-behind.json").unlink()
    (repo / "a.txt").write_text("changed by a build")
    out = clean_tree(repo, tmp_path, "verify")
    assert out.returncode == 1 and "a.txt" in out.stdout


def test_work_in_progress_before_the_gate_ran_is_not_blamed_on_the_gate(tmp_path):
    repo = scratch_repo(tmp_path)
    (repo / "wip.txt").write_text("mine")
    clean_tree(repo, tmp_path, "snapshot")
    assert clean_tree(repo, tmp_path, "verify").returncode == 0


def test_every_mutant_has_a_patch_and_real_test_files_and_a_reason():
    names, rows = set(), []
    for raw in (CI / "mutants" / "manifest.txt").read_text().splitlines():
        if raw.strip() and not raw.lstrip().startswith("#"):
            name, patch, tests, why = (x.strip() for x in raw.split("|", 3))
            rows.append((name, patch, tests.split(), why))
    assert len(rows) >= 20
    for name, patch, tests, why in rows:
        assert name not in names and why, name
        names.add(name)
        assert (CI / "mutants" / patch).is_file() and (CI / "mutants" / patch).read_text().startswith("diff --git"), name
        for t in tests:
            assert (ROOT / t.split("::")[0]).is_file(), (name, t)
    assert {p.name for p in (CI / "mutants").glob("*.patch")} == {r[1] for r in rows}, "a patch with no manifest line, or a line with no patch"


def workflows():
    for p in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        yield p, yaml.safe_load(p.read_text())


def test_every_workflow_reads_only_has_a_timeout_on_every_job_and_pins_every_action_by_sha():
    seen = 0
    for p, wf in workflows():
        assert wf.get("permissions") == {"contents": "read"}, f"{p.name}: top-level permissions must be contents: read"
        assert "pull_request_target" not in wf.get(True, wf.get("on", {})), f"{p.name}: pull_request_target runs untrusted code with secrets"
        for jid, job in wf["jobs"].items():
            if "uses" not in job:  # a job that calls a reusable workflow has no steps of its own
                assert isinstance(job.get("timeout-minutes"), int), f"{p.name}:{jid} has no timeout-minutes"
            for step in job.get("steps", []):
                ref = step.get("uses", "")
                if ref and not ref.startswith("./"):
                    seen += 1
                    assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), f"{p.name}:{jid}: {ref} is not pinned to a commit sha"
    assert seen >= 8


def test_the_deploy_job_is_off_by_default_behind_an_approval_and_the_stub_refuses_to_deploy(tmp_path):
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    dep = wf["jobs"]["deploy"]
    assert dep["if"] == "vars.DEPLOY_ENABLED == 'true'" and dep["environment"] == "production"
    attest = next(st for st in wf["jobs"]["build"]["steps"] if str(st.get("uses", "")).startswith("actions/attest-build-provenance"))
    assert attest["if"] == "github.event.repository.private == false", "provenance only on a public repository: a private one would fail every release"
    assert any("provenance is skipped" in st.get("name", "") for st in wf["jobs"]["build"]["steps"])
    assert wf["jobs"]["build"]["permissions"] == {"contents": "read", "id-token": "write", "attestations": "write"}
    env = {"DEPLOY_HOST": "h", "DEPLOY_USER": "u", "DEPLOY_SSH_KEY": "THE-KEY-VALUE", "PATH": os.environ["PATH"]}
    out = subprocess.run([str(CI / "deploy.sh")], env=env, capture_output=True, text=True)
    assert out.returncode == 1 and "no host has been chosen" in out.stderr and "THE-KEY-VALUE" not in out.stdout + out.stderr
    assert subprocess.run([str(CI / "deploy.sh")], env={"PATH": os.environ["PATH"]}, capture_output=True, text=True).returncode == 1


def test_the_tool_downloads_retry_through_a_cdn_outage_and_the_binaries_are_cached():
    text = (CI / "bootstrap_tools.sh").read_text()
    assert "--retry 6 --retry-delay 10 --retry-all-errors --retry-max-time 300" in text
    action = (ROOT / ".github/actions/prepare/action.yml").read_text()
    assert "path: .ci-tools" in action and "hashFiles('tools/ci/bootstrap_tools.sh')" in action


def test_live_evals_and_the_nightly_never_run_on_push_and_the_gate_names_every_job():
    assert set(yaml.safe_load((ROOT / ".github/workflows/live-evals.yml").read_text())[True]) == {"workflow_dispatch"}
    assert set(yaml.safe_load((ROOT / ".github/workflows/nightly.yml").read_text())[True]) == {"schedule", "workflow_dispatch"}
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert set(ci["jobs"]["ci-gate"]["needs"]) == set(ci["jobs"]) - {"ci-gate"}, "a job the gate does not wait for is a job whose failure is not a failure"


def test_the_smoke_script_fails_against_nothing_and_prints_no_token():
    out = subprocess.run([str(CI / "post_deploy_smoke.sh"), "http://127.0.0.1:9", "todoh.example"], env={"OWNER_TOKEN": "SECRET-TOKEN-VALUE", "PATH": os.environ["PATH"]}, capture_output=True, text=True)
    assert out.returncode == 1 and "FAIL" in out.stdout and "SECRET-TOKEN-VALUE" not in out.stdout + out.stderr


@pytest.mark.parametrize("script", sorted(p.name for p in CI.glob("*.sh")))
def test_every_gate_script_is_executable_and_strict(script):
    path = CI / script
    assert os.access(path, os.X_OK), script
    text = path.read_text()
    assert text.startswith("#!/usr/bin/env bash") and ("set -" in text or "lib.sh" in text), f"{script} must fail on errors"
