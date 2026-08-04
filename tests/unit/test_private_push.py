from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_private_push_uses_an_ephemeral_header_and_expected_origin() -> None:
    play = yaml.safe_load(
        (REPO_ROOT / "playbooks" / "push_repository.yml").read_text()
    )[0]
    tasks = play["tasks"]
    push = next(
        task
        for task in tasks
        if task["name"] == "Push the current commit without persisting credentials"
    )

    command = push["ansible.builtin.command"]
    assert command["argv"][-1] == "HEAD:{{ gitops_branch }}"
    assert push["environment"]["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert push["no_log"] is True
    assert any("unexpected origin" in task["name"] for task in tasks)


def test_readme_routes_private_pushes_through_the_safe_helper() -> None:
    readme = (REPO_ROOT / "README.md").read_text()

    assert "git push origin main" not in readme
    assert readme.count("scripts/push") >= 2
