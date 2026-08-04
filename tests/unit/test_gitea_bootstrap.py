from pathlib import Path

import yaml


def test_controller_token_requests_explicit_full_scope():
    repository_root = Path(__file__).resolve().parents[2]
    play = yaml.safe_load(
        (repository_root / "playbooks" / "bootstrap_gitea.yml").read_text()
    )[0]
    task = next(
        item
        for item in play["tasks"]
        if item["name"] == "Create the Gitea controller access token"
    )

    assert task["ansible.builtin.uri"]["body"]["scopes"] == ["all"]
