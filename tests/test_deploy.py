import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
APP_SECRETS = {"APP_PASSWORD_HASH", "SESSION_SECRET", "LINK_SIGNING_SECRET", "API_TOKEN", "RESEND_API_KEY"}
UNITS = ["budgie-web.service", "budgie-worker.service", "budgie-predeploy.service"]


def unit_lines(name: str, key: str) -> list[str]:
    return [line.split("=", 1)[1] for line in (DEPLOY / name).read_text().splitlines() if line.startswith(f"{key}=")]


def workflow(name: str) -> dict:
    # yaml reads the bare `on:` key as True
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_units_read_secrets_only_from_the_env_file():
    for name in UNITS:
        text = (DEPLOY / name).read_text()
        assert not {s for s in APP_SECRETS if s in text}, name
        assert unit_lines(name, "EnvironmentFile") == ["/etc/budgie/paths.env", "/etc/budgie/budgie.env"], name
        assert unit_lines(name, "User") == ["budgie"], name


def test_units_exec_absolute_paths():
    for name in UNITS:
        for cmd in unit_lines(name, "ExecStart"):
            assert cmd.startswith("/"), (name, cmd)


def test_web_listens_on_localhost_only():
    (cmd,) = unit_lines("budgie-web.service", "ExecStart")
    assert "--host 127.0.0.1" in cmd


def test_paths_env_has_no_secrets():
    env = dict(line.split("=", 1) for line in (DEPLOY / "paths.env").read_text().splitlines()
               if line and not line.startswith("#"))
    assert not APP_SECRETS & set(env)
    assert env["DATABASE_URL"] == "sqlite:////var/lib/budgie/newsletter.sqlite"


def test_env_example_lists_every_secret():
    keys = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines()
            if "=" in line and not line.startswith("#")}
    assert APP_SECRETS <= keys
    assert "TS_AUTHKEY" not in keys


def test_runner_may_only_start_predeploy_and_restart_services():
    rule = re.search(r'NOPASSWD: (.*)"\)', (DEPLOY / "setup.sh").read_text())[1]
    allowed = {c.strip().split(" ", 1)[1] for c in rule.split(",")}
    assert allowed == {"start budgie-predeploy", "restart budgie-web", "restart budgie-worker"}


def test_deploy_uses_only_allowed_sudo_commands():
    used = set(re.findall(r"sudo systemctl (\w+ [\w-]+)", (DEPLOY / "deploy.sh").read_text()))
    assert used == {"start budgie-predeploy", "restart budgie-web", "restart budgie-worker"}


def test_scripts_parse():
    for script in DEPLOY.glob("*.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_release_deploys_on_the_budgie_runner():
    jobs = workflow("release.yml")["jobs"]
    assert jobs["release"]["needs"] == "test"
    assert jobs["deploy"]["needs"] == "release"
    assert jobs["deploy"]["runs-on"] == "budgie"
    assert jobs["deploy"]["if"] == "needs.release.outputs.released == 'true'"
    assert any(s.get("run") == "deploy/deploy.sh" for s in jobs["deploy"]["steps"])


def test_workflows_trigger_as_sparrow_does():
    assert list(workflow("release.yml")[True]) == ["push"]
    assert workflow("release.yml")[True]["push"]["branches"] == ["main"]
    assert list(workflow("pr.yml")[True]) == ["pull_request"]
    for name in ("pr.yml", "release.yml"):
        steps = workflow(name)["jobs"]["test"]["steps"]
        assert {"run": "uv run pytest"} in steps, name
