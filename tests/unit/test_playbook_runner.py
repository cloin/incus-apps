import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runner_rejects_a_short_vault_password() -> None:
    environment = os.environ.copy()
    environment["INCUS_APPS_VAULT_PASSWORD"] = "too-short"

    result = subprocess.run(
        [REPO_ROOT / "scripts" / "run-playbook", "playbooks/preflight.yml"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "at least 12 characters" in result.stderr
