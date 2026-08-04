from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FINALIZE_OPENBAO = REPO_ROOT / "playbooks" / "finalize_openbao.yml"


def test_secret_id_accessors_are_listed_from_the_secret_id_endpoint() -> None:
    playbook = FINALIZE_OPENBAO.read_text()

    assert "/secret-id\n        method: LIST" in playbook
    assert "/secret-id-accessor\n        method: LIST" not in playbook
    assert "/secret-id-accessor/destroy" in playbook
