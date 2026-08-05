from __future__ import annotations

import pathlib
import sys

import pytest
from ansible.errors import AnsibleFilterError

sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "filter_plugins"))

from incus_apps import (  # noqa: E402
    incus_app_is_ipv4_address,
    incus_app_is_private_ipv4_prefix,
    incus_app_secret_specs,
)


def test_lab_address_and_network_prefix_validation():
    assert incus_app_is_ipv4_address("192.0.2.10") is True
    assert incus_app_is_ipv4_address("192.0.2.999") is False
    assert incus_app_is_private_ipv4_prefix("10.210") is True
    assert incus_app_is_private_ipv4_prefix("172.31") is True
    assert incus_app_is_private_ipv4_prefix("172.32") is False
    assert incus_app_is_private_ipv4_prefix("192.168") is True
    assert incus_app_is_private_ipv4_prefix("192.169") is False


def _metadata(name: str, path: str) -> dict:
    return {
        "name": name,
        "x-incus-apps": {
            "secrets": {
                "path": path,
                "required": [f"{name}_password"],
                "generate": {
                    f"{name}_password": {"type": "random_string", "length": 48}
                },
            }
        },
    }


def test_project_extension_secret_requirements_are_normalized():
    assert incus_app_secret_specs([_metadata("example", "incus-apps/example")]) == [
        {
            "path": "incus-apps/example",
            "required": ["example_password"],
            "optional": False,
            "generate": {
                "example_password": {"type": "random_string", "length": 48}
            },
        }
    ]


def test_project_extension_rejects_duplicate_secret_paths():
    with pytest.raises(AnsibleFilterError, match="declared by both"):
        incus_app_secret_specs(
            [
                _metadata("one", "incus-apps/shared"),
                _metadata("two", "incus-apps/shared"),
            ]
        )
