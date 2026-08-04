import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_missing_recovery_file_fails_before_requesting_password(tmp_path) -> None:
    environment = os.environ.copy()
    environment.pop("INCUS_APPS_VAULT_PASSWORD", None)
    missing_recovery = tmp_path / "openbao.vault.yml"

    result = subprocess.run(
        [REPO_ROOT / "scripts" / "view-recovery", missing_recovery],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "private Gitea checkout" in result.stderr
    assert "Ansible Vault password" not in result.stderr
