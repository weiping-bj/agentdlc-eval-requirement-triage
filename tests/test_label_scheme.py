"""Extension "label scheme switch" (labkit/label_scheme.py + ext-switch-label-scheme.sh): one command moves the
standard, golden set, evaluators, prompt and gates between 是/否 and P0/P1/P2. No AWS calls."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import config as labcfg  # noqa: E402
from labkit import label_scheme as LS  # noqa: E402


@pytest.fixture
def cfg_env(monkeypatch, tmp_path):
    local = tmp_path / "local.env"
    monkeypatch.setattr(labcfg, "LOCAL_FILE", local)
    monkeypatch.setenv("ADLC_LOCAL_ENV", str(local))
    for k in labcfg.KEYS:
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(f"_ADLC_EXP_{k}", raising=False)
    monkeypatch.setenv("LAB_SUFFIX", "zhang3")
    return local


def _states(**present):
    return lambda name: present.get(name)


def test_plan_follows_the_impact_chain_in_order():
    steps = LS.plan("level", 1, state=_states(evaluators={"x": 1}, runtime={"a": 1}, online={"runtime": {}, "local": {}}))
    argvs = [a for _w, a in steps]
    assert argvs[0] is None and "LEVEL_TOLERANCE=1" in steps[0][0]                     # standard first
    assert argvs[1][-3:] == ["--scheme", "level", "freeze"]                             # golden set (all items have levels)
    assert argvs[2:] == [["./40-create-evaluators.sh"], ["./30-deploy-runtime.sh"],     # evaluator, prompt, gate
                         ["./41-online-eval.sh", "enable"], ["./41-online-eval.sh", "enable", "--mode", "local"]]


def test_plan_without_aws_resources_only_touches_files():
    steps = LS.plan("binary", 0, state=_states())
    assert [a for _w, a in steps][1:] == [[sys.executable, "data/tools/golden_tool.py", "--scheme", "binary", "freeze"]]


def test_missing_levels_are_filled_before_the_freeze(tmp_path):
    rows = [json.loads(x) for x in (ROOT / "data" / "golden" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines() if x]
    rows[0]["expected"].pop("level", None)
    rows[1]["expected"]["level"] = None
    g = tmp_path / "g.jsonl"
    g.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    steps = LS.plan("level", 0, golden=g, state=_states())
    assert LS.missing_levels(g) == 2 and steps[1][1][-1] == "fill-levels" and steps[2][1][-1] == "freeze"
    assert "fill-levels" not in str(LS.plan("binary", 0, golden=g, state=_states()))  # binary never needs levels


def test_shipped_golden_set_already_has_every_level():
    assert LS.missing_levels() == 0


def test_local_env_keeps_other_settings_and_replaces_the_scheme(tmp_path):
    p = tmp_path / "local.env"
    p.write_text("LAB_SUFFIX=zhang3\nAGENT_VERSION=v1-ref\nLABEL_SCHEME=binary\nexport LEVEL_TOLERANCE=0\n", encoding="utf-8")
    LS.write_local_env(p, {"LABEL_SCHEME": "level", "LEVEL_TOLERANCE": "1"})
    assert p.read_text(encoding="utf-8").splitlines() == ["LAB_SUFFIX=zhang3", "AGENT_VERSION=v1-ref",
                                                          "LABEL_SCHEME=level", "LEVEL_TOLERANCE=1"]


def test_dry_run_changes_nothing(cfg_env, monkeypatch, capsys):
    monkeypatch.setattr(LS.subprocess, "call", lambda *a, **k: pytest.fail("dry run must not run commands"))
    assert LS.main(["level", "--dry-run"]) == 0
    assert not cfg_env.exists() and "只预览" in capsys.readouterr().out


def test_run_writes_the_standard_and_stops_at_the_first_failing_step(cfg_env, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(LS, "_state", _states(evaluators={"x": 1}, runtime={"a": 1}))
    monkeypatch.setattr(LS.subprocess, "call", lambda argv, cwd: calls.append(argv) or (3 if argv[0] == "./30-deploy-runtime.sh" else 0))
    assert LS.main(["level", "--tolerance", "1"]) == 3
    assert "LABEL_SCHEME=level" in cfg_env.read_text(encoding="utf-8")
    assert labcfg.load()["LABEL_SCHEME"] == "level" and labcfg.load()["LEVEL_TOLERANCE"] == "1"
    assert [c[0] for c in calls] == [sys.executable, "./40-create-evaluators.sh", "./30-deploy-runtime.sh"]
    assert "第 4 步失败" in capsys.readouterr().out


def test_evaluators_follow_the_scheme(cfg_env, monkeypatch):
    from labkit import evaluators_reg as ER
    ER.aws.cfg.cache_clear()
    assert "label-level" not in ER.lambdas() and all(s != "label_level" for s, _k, _d in ER.code_evaluators())
    cfg_env.write_text("LABEL_SCHEME=level\nLEVEL_TOLERANCE=1\n", encoding="utf-8")
    ER.aws.cfg.cache_clear()                              # aws.cfg() is cached per process
    assert ER.lambdas()["label-level"][2] == {"ADLC_SCORE_FIELD": "level"}
    assert ("label_level", "label-level") == ER.code_evaluators()[-1][:2]
    src = (ROOT / "labkit" / "evaluators_reg.py").read_text(encoding="utf-8")
    assert '"ADLC_LABEL_SCHEME": label_scheme()' in src and '"ADLC_LEVEL_TOLERANCE"' in src
    from labkit import online
    assert "label_level" in online.ONLINE_EVALUATORS     # attached only when registered (enable filters by state)
    ER.aws.cfg.cache_clear()


def test_runtime_and_prompt_follow_the_scheme(monkeypatch):
    src = (ROOT / "labkit" / "runtime.py").read_text(encoding="utf-8")
    assert '"ADLC_LABEL_SCHEME": (c.get("LABEL_SCHEME") or "binary")' in src
    pytest.importorskip("strands")
    sys.path.insert(0, str(ROOT / "agent"))
    from adlc_agent import core
    monkeypatch.delenv("ADLC_LABEL_SCHEME", raising=False)
    monkeypatch.delenv("LABEL_SCHEME", raising=False)
    base = core.system_prompt("v0")
    assert "分级口径（已启用）" not in base
    monkeypatch.setenv("ADLC_LABEL_SCHEME", "level")
    graded = core.system_prompt("v0")
    assert graded.startswith(base.rstrip()) and "分级口径（已启用）" in graded and "P0" in graded
    monkeypatch.setenv("ADLC_LABEL_SCHEME", "binary")
    monkeypatch.setenv("LABEL_SCHEME", "level")              # the EC2 run path: 00-config.sh exports LABEL_SCHEME
    monkeypatch.delenv("ADLC_LABEL_SCHEME")
    assert "分级口径（已启用）" in core.system_prompt("v1-ref")


def test_wrapper_script_sources_the_config_and_parses():
    sh = ROOT / "ext-switch-label-scheme.sh"
    assert sh.stat().st_mode & 0o111 and "source ./00-config.sh" in sh.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0
