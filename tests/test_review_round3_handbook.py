"""Regression tests for the handbook branch's adversarial review (code-review.md, round 1). No AWS calls."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_freeze_follows_the_active_label_scheme(tmp_path):
    """Finding 2: after ./ext-switch-label-scheme.sh level, a bare `golden_tool.py freeze` (handbook 2.4, E6,
    criteria.py) must validate in the graded scheme, so an item without a level is refused instead of frozen.
    Runs in a copy of data/ + evaluators/ + config/: freeze syncs into <repo>/evaluators, never the real tree."""
    import shutil
    ign = shutil.ignore_patterns("__pycache__", "tests", "history")
    for d in ("data", "evaluators", "config"):
        shutil.copytree(ROOT / d, tmp_path / d, ignore=ign)
    g = tmp_path / "data" / "golden" / "golden_set.jsonl"
    rows = [json.loads(x) for x in g.read_text(encoding="utf-8").splitlines() if x]
    rows[0]["expected"].pop("level", None)
    g.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    before = {p: p.read_bytes() for p in (ROOT / "evaluators").glob("*/golden_set.json")}
    for env_scheme, conf in (("level", None), ("", "LABEL_SCHEME=level\n")):     # from the environment, or config/local.env
        if conf:
            (tmp_path / "config" / "local.env").write_text(conf, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "LABEL_SCHEME"}
        if env_scheme:
            env["LABEL_SCHEME"] = env_scheme
        r = subprocess.run([sys.executable, "-I", str(tmp_path / "data" / "tools" / "golden_tool.py"), "freeze"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 1 and "expected.level" in r.stdout, r.stdout[-400:]
    assert {p: p.read_bytes() for p in before} == before        # the real evaluator copies are untouched


def test_deploy_refuses_a_version_without_a_prompt_file(monkeypatch, tmp_path, capsys):
    """Finding 7: `./30-deploy-runtime.sh --version v1` before 5.2 must stop before creating a Runtime that can only
    fail (prompts/v1.md is written by the participant; it is not shipped)."""
    from labkit import config as labcfg
    monkeypatch.setattr(labcfg, "LOCAL_FILE", tmp_path / "local.env")
    monkeypatch.delenv("ADLC_LOCAL_ENV", raising=False)
    monkeypatch.setenv("LAB_SUFFIX", "ws")
    from labkit import aws, runtime
    aws.cfg.cache_clear()
    monkeypatch.setattr(runtime, "deploy", lambda v: pytest.fail("deploy must not start for a missing prompt"))
    if (runtime.ROOT / "agent" / "adlc_agent" / "prompts" / "v9.md").exists():
        pytest.skip("unexpected v9 prompt")
    with pytest.raises(SystemExit) as e:
        runtime.main(["deploy", "--version", "v9"])
    assert "prompts/v9.md" in str(e.value) and "50-switch-version.sh" in str(e.value)
    aws.cfg.cache_clear()


def test_label_switch_says_which_agent_version_it_redeploys(monkeypatch, tmp_path):
    """Finding 1: the Runtime step deploys config AGENT_VERSION; the plan must say so and flag a different live one."""
    from labkit import label_scheme as LS
    local = tmp_path / "local.env"
    local.write_text("AGENT_VERSION=v0\n", encoding="utf-8")
    monkeypatch.setenv("ADLC_LOCAL_ENV", str(local))
    steps = LS.plan("level", 0, state=lambda n: {"agentVersion": "v1-ref"} if n == "runtime" else None)
    what = next(w for w, a in steps if a == ["./30-deploy-runtime.sh"])
    assert "Agent 版本 v0" in what and "线上现在是 v1-ref" in what
    local.write_text("AGENT_VERSION=v1-ref\n", encoding="utf-8")
    steps = LS.plan("level", 0, state=lambda n: {"agentVersion": "v1-ref"} if n == "runtime" else None)
    assert "线上现在是" not in next(w for w, a in steps if a == ["./30-deploy-runtime.sh"])


def test_locked_evaluator_update_also_removes_legacy_online_configs(monkeypatch, tmp_path):
    """Finding 9: a lab created before the rename has <prefix>_online_b; updating locked evaluators must remove it
    too (and restore the config under the new name)."""
    from labkit import config as labcfg
    monkeypatch.setattr(labcfg, "LOCAL_FILE", tmp_path / "local.env")
    monkeypatch.delenv("ADLC_LOCAL_ENV", raising=False)
    monkeypatch.setenv("LAB_SUFFIX", "ws")
    from labkit import aws, evaluators_reg as ER, online
    aws.cfg.cache_clear()
    live = {"adlc_ws_online_b": {"onlineEvaluationConfigId": "old-b", "onlineEvaluationConfigName": "adlc_ws_online_b"}}
    deleted = []

    class Ctl:
        def delete_online_evaluation_config(self, onlineEvaluationConfigId):
            deleted.append(onlineEvaluationConfigId)
            live.pop(next(k for k, v in live.items() if v["onlineEvaluationConfigId"] == onlineEvaluationConfigId))
    monkeypatch.setattr(aws, "client", lambda name: Ctl())
    monkeypatch.setattr(online, "find", lambda name: live.get(name))
    assert ER.remove_online() == ["runtime"] and deleted == ["old-b"]
    aws.cfg.cache_clear()


def test_label_switch_prints_each_step_before_running_it(monkeypatch, tmp_path, capsys):
    """Found live: piped stdout is block-buffered, so the step headers appeared after all child output. Each step
    line must be flushed before its command runs."""
    from labkit import label_scheme as LS
    monkeypatch.setenv("ADLC_LOCAL_ENV", str(tmp_path / "local.env"))
    monkeypatch.setattr(LS, "_state", lambda name: {"agentVersion": "v0"} if name == "runtime" else None)
    seen = []

    class Out:
        def __init__(self):
            self.buf, self.flushed = "", ""
        def write(self, s):
            self.buf += s
        def flush(self):
            self.flushed = self.buf
    out = Out()
    monkeypatch.setattr(LS.sys, "stdout", out)
    monkeypatch.setattr(LS.subprocess, "call", lambda argv, cwd: seen.append((argv[-1], out.flushed)) or 0)
    assert LS.main(["level"]) == 0
    assert seen and all(f"$ " in flushed and cmd in flushed.splitlines()[-1] for cmd, flushed in seen), seen
