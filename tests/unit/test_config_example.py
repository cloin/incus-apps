from pathlib import Path

import yaml


def test_example_network_prefix_is_loaded_as_a_string():
    repository_root = Path(__file__).resolve().parents[2]
    configuration = yaml.safe_load(
        (repository_root / "config" / "lab.example.yml").read_text()
    )

    assert configuration["incus_app_network_prefix"] == "10.210"
