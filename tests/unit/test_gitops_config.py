from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGURE_GITOPS = REPO_ROOT / "playbooks" / "configure_gitops.yml"


def test_semaphore_inventory_uses_root_inventory_and_is_reconciled() -> None:
    playbook = CONFIGURE_GITOPS.read_text()

    assert (REPO_ROOT / "inventory.yml").is_file()
    assert not (REPO_ROOT / "inventories" / "lab.yml").exists()
    assert "inventory = inventory.yml" in (REPO_ROOT / "ansible.cfg").read_text()
    assert "inventory: inventories/lab.yml" not in playbook
    assert "inventory: inventory.yml" in playbook
    assert "name: Reconcile the Semaphore Incus inventory" in playbook
    assert "method: PUT" in playbook
    assert "'inventory': 'inventory.yml'" in playbook


def test_recovery_path_is_independent_of_inventory_location() -> None:
    variables = (REPO_ROOT / "group_vars" / "all" / "main.yml").read_text()

    assert "{{ playbook_dir }}/../recovery/openbao.vault.yml" in variables
    assert "{{ inventory_dir }}/../recovery/openbao.vault.yml" not in variables
