from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP_SECRETS = {"APP_PASSWORD_HASH", "SESSION_SECRET", "LINK_SIGNING_SECRET", "API_TOKEN", "RESEND_API_KEY"}


def test_tailscale_sidecar_gets_only_its_auth_key():
    ts = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["tailscale"]
    assert "env_file" not in ts
    env = ts["environment"]
    assert "TS_AUTHKEY" in env
    assert not APP_SECRETS & set(env)
