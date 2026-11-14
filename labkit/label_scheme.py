"""Extension "label scheme switch": move the whole lab between 是/否 (binary) and P0/P1/P2 (level) in one command.

  ./ext-switch-label-scheme.sh level [--tolerance 0|1]   # 是/否 -> P0/P1/P2
  ./ext-switch-label-scheme.sh binary                    # back to 是/否
  ./ext-switch-label-scheme.sh level --dry-run           # only print the plan, change nothing

What follows the label scheme (the impact chain standard -> golden set -> evaluator -> prompt -> gate):
  1. standard   config/local.env: LABEL_SCHEME, LEVEL_TOLERANCE (read by every script through 00-config.sh)
  2. golden set every item has expected.level (golden_tool.py fill-levels, only when some item lacks one),
                then golden_tool.py --scheme <s> freeze (validate + export + copy into the evaluator packages)
  3. evaluator  ./40-create-evaluators.sh: the label Lambdas get ADLC_LABEL_SCHEME / ADLC_LEVEL_TOLERANCE and,
                in the level scheme, one more evaluator label_level (优先级 gate)
  4. prompt     ./30-deploy-runtime.sh: the Runtime gets ADLC_LABEL_SCHEME, so agent/adlc_agent/core.py appends
                prompts/level-scheme.md (the `level` field must be P0/P1/P2) to every version's prompt
  5. gate       online evaluation config re-enabled with the new evaluator list (only if one exists); the next
                ./42-run-golden.sh + ./43-run-eval.sh judge 优先级 as a gate as well
Steps 3-5 need AWS and run only when the lab already has evaluators / a Runtime / an online config; otherwise the
first run of those scripts picks the scheme up from config/local.env. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_ENV = ROOT / "config" / "local.env"
GOLDEN = ROOT / "data" / "golden" / "golden_set.jsonl"
STATE = ROOT / ".lab-state"
LEVELS = ("P0", "P1", "P2")


def local_env_path() -> Path:
    return Path(os.environ["ADLC_LOCAL_ENV"]) if os.environ.get("ADLC_LOCAL_ENV") else LOCAL_ENV


def write_local_env(path: Path, values: dict) -> None:
    """Set KEY=VALUE lines in config/local.env, keeping every other line (same rule as 50-switch-version.sh)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keep = [ln for ln in lines if ln.split("=", 1)[0].strip().removeprefix("export ").strip() not in values]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(keep + [f"{k}={v}" for k, v in values.items()]) + "\n", encoding="utf-8")


def missing_levels(golden: Path = GOLDEN) -> int:
    n = 0
    for line in golden.read_text(encoding="utf-8").splitlines():
        if line.strip() and (json.loads(line).get("expected") or {}).get("level") not in LEVELS:
            n += 1
    return n


def _state(name: str):
    p = STATE / f"{name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def configured_version() -> str:
    """AGENT_VERSION that ./30-deploy-runtime.sh will deploy: resolved like labkit/config.py (files, then a
    deliberate environment override), the same value runtime.py's --version default takes."""
    from . import config as labcfg
    return str(labcfg.load(require_suffix=False).get("AGENT_VERSION") or "v0")


def plan(scheme: str, tolerance: int, golden: Path = GOLDEN, state=None) -> list:
    """[(what, argv or None)]: argv None = a step done in-process (config/local.env)."""
    state = state or _state
    py = sys.executable
    steps = [(f"标准：config/local.env 写入 LABEL_SCHEME={scheme}、LEVEL_TOLERANCE={tolerance}", None)]
    if scheme == "level" and missing_levels(golden):
        steps.append((f"黄金集：为 {missing_levels(golden)} 条缺少优先级的样本按“重要 / 紧急”补 P0/P1/P2",
                      [py, "data/tools/golden_tool.py", "fill-levels"]))
    steps.append((f"黄金集：按 {scheme} 口径校验并冻结（同步进评估器目录）",
                  [py, "data/tools/golden_tool.py", "--scheme", scheme, "freeze"]))
    if state("evaluators"):
        extra = "分级口径多一个 label_level" if scheme == "level" else "是/否口径不再挂 label_level"
        steps.append((f"评估器：重新部署 Lambda 并注册（{extra}）", ["./40-create-evaluators.sh"]))
    rt = state("runtime")
    if rt:
        want, live = configured_version(), (rt or {}).get("agentVersion") or "?"
        note = "" if want == live else f"；注意：线上现在是 {live}，这一步会部署配置里的 {want}"
        steps.append((f"提示词：重新部署 Runtime，Agent 版本 {want}（环境变量 ADLC_LABEL_SCHEME 决定是否要求输出 level{note}）",
                      ["./30-deploy-runtime.sh"]))
    online = state("online") or {}
    for mode in ("runtime", "local"):
        if mode in online:
            steps.append((f"门禁：用新的评估器列表重新启用在线评估（{mode}）",
                          ["./41-online-eval.sh", "enable"] + (["--mode", "local"] if mode == "local" else [])))
    return steps


def run(scheme: str, tolerance: int, dry_run: bool = False) -> int:
    steps = plan(scheme, tolerance)
    label = "P0/P1/P2 分级" if scheme == "level" else "是/否"
    print(f"▶ 切换标签方案 → {label}（{len(steps)} 步{'，只预览' if dry_run else ''}）")
    for i, (what, argv) in enumerate(steps, 1):
        print(f"  [{i}/{len(steps)}] {what}" + (f"\n        $ {' '.join(a if a != sys.executable else 'python3' for a in argv)}"
                                              if argv else ""))
        if dry_run:
            continue
        if argv is None:
            # child scripts re-read config/local.env (an inherited value equal to its _ADLC_EXP_ snapshot loses to
            # the file, labkit/config.py), so the new scheme reaches every later step
            write_local_env(local_env_path(), {"LABEL_SCHEME": scheme, "LEVEL_TOLERANCE": str(tolerance)})
            continue
        sys.stdout.flush()   # piped stdout is block-buffered: show the step before the child's own output
        rc = subprocess.call(argv, cwd=ROOT)
        if rc:
            print(f"❌ 第 {i} 步失败（退出码 {rc}）。改好后重新运行同一条命令即可（每一步都可以重复运行）。")
            return rc
    if dry_run:
        print("（预览，什么都没改）")
        return 0
    print(f"✅ 标签方案已切换为 {label}。")
    if scheme == "level":
        print(f"   优先级怎么算对：{'完全一致' if tolerance == 0 else '相差不超过一级'}；门禁多一项“优先级准确率”。")
    if _state("runtime"):
        print(f"   Runtime 上现在是 Agent {configured_version()}；要换版本用 ./50-switch-version.sh <版本> 再 ./30-deploy-runtime.sh")
    print("   下一步：./42-run-golden.sh，等 2–3 分钟后 ./43-run-eval.sh（切换前的评估结果和切换后不可直接比较）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ext-switch-label-scheme.sh", description="一条命令切换标签方案（是/否 ↔ P0/P1/P2）")
    ap.add_argument("scheme", choices=["level", "binary"], help="level = P0/P1/P2 分级；binary = 是/否（默认口径）")
    ap.add_argument("--tolerance", type=int, choices=[0, 1], default=0, help="分级口径下优先级怎么算对：0 完全一致（默认），1 相差不超过一级")
    ap.add_argument("--dry-run", action="store_true", help="只打印步骤，不做任何修改")
    a = ap.parse_args(argv)
    return run(a.scheme, a.tolerance if a.scheme == "level" else 0, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
