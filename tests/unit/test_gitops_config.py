from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGURE_GITOPS = REPO_ROOT / "playbooks" / "configure_gitops.yml"


def test_semaphore_inventory_uses_root_inventory_and_is_reconciled() -> None:
    playbook = CONFIGURE_GITOPS.read_text()

    assert "inventory: inventories/lab.yml" not in playbook
    assert "inventory: inventory.yml" in playbook
    assert "name: Reconcile the Semaphore Incus inventory" in playbook
    assert "method: PUT" in playbook
    assert "'inventory': 'inventory.yml'" in playbook
