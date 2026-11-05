"""Verify that bootstrap state reaches the Semaphore execution host once."""

import grp
import json
import os
import pathlib
import pwd
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = pathlib.Path(__file__).parents[2]
APPS = ("ingress", "openbao", "gitea", "semaphore")


@pytest.mark.skipif(shutil.which("ansible-playbook") is None,
                    reason="ansible-playbook is required")
def test_bootstrap_ownership_handoff_is_idempotent_and_rejects_divergence(tmp_path):
    bootstrap = (ROOT / "playbooks" / "bootstrap_lab.yml").read_text()
    assert bootstrap.index("import_playbook: bootstrap_semaphore.yml") < bootstrap.index(
        "import_playbook: handoff_ownership.yml"
    ) < bootstrap.index("import_playbook: verify_gitops.yml")

    source = tmp_path / "bootstrap.json"
    destination = tmp_path / "semaphore" / "ownership.json"
    records = {
        f"fake/{name}": {"status": "active", "token": f"{index:032x}"}
        for index, name in enumerate(APPS, start=1)
    }
    source.write_text(json.dumps({"version": 1, "records": records}))
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("semaphore ansible_connection=local\n")
    ansible_config = tmp_path / "ansible.cfg"
    ansible_config.write_text("[defaults]\n")
    variables = tmp_path / "vars.yml"
    variables.write_text(yaml.safe_dump({
        "ansible_python_interpreter": sys.executable,
        "incus_remote": "fake",
        "incus_platform_applications": list(APPS),
        "incus_compose_ownership_path": str(source),
        "incus_apps_semaphore_ownership_path": str(destination),
        "incus_apps_semaphore_owner": pwd.getpwuid(os.getuid()).pw_name,
        "incus_apps_semaphore_group": grp.getgrgid(os.getgid()).gr_name,
    }))

    def handoff():
        return subprocess.run(
            ["ansible-playbook", "-i", str(inventory), "-e", f"@{variables}",
             str(ROOT / "playbooks" / "handoff_ownership.yml")],
            cwd=ROOT, env={**os.environ, "ANSIBLE_CONFIG": str(ansible_config)},
            capture_output=True, text=True, check=False,
        )

    first = handoff()
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(destination.read_text())["records"] == records
    assert destination.stat().st_mode & 0o777 == 0o600

    second = handoff()
    assert second.returncode == 0, second.stdout + second.stderr
    assert "changed=0" in second.stdout

    # A later bootstrap must preserve Semaphore's newer application records.
    current = json.loads(destination.read_text())
    current["records"]["fake/new-app"] = {
        "status": "active", "token": "a" * 32,
    }
    destination.write_text(json.dumps(current))
    third = handoff()
    assert third.returncode == 0, third.stdout + third.stderr
    assert json.loads(destination.read_text()) == current

    # A platform token disagreement must be reported before Apply All starts.
    current["records"]["fake/ingress"]["token"] = "b" * 32
    destination.write_text(json.dumps(current))
    conflict = handoff()
    assert conflict.returncode != 0
    assert json.loads(destination.read_text()) == current
