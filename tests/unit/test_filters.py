from __future__ import annotations

import pathlib
import sys

import pytest
from ansible.errors import AnsibleFilterError

sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "filter_plugins"))

from incus_apps import (
    incus_app_devices_config,
    incus_app_exposure_device,
    incus_app_ingress_bindings,
    incus_app_ingress_count,
    incus_app_is_ipv4_address,
    incus_app_is_private_ipv4_prefix,
    incus_app_network_attachment,
    incus_app_network_name,
    incus_app_oci_defaults,
    incus_app_oci_raw_config,
    incus_app_service_order,
    incus_app_secret_specs,
    incus_app_validate_catalog,
)


def test_lab_address_and_network_prefix_validation():
    assert incus_app_is_ipv4_address("192.168.4.121") is True
    assert incus_app_is_ipv4_address("192.168.4.999") is False
    assert incus_app_is_private_ipv4_prefix("10.210") is True
    assert incus_app_is_private_ipv4_prefix("172.31") is True
    assert incus_app_is_private_ipv4_prefix("172.32") is False
    assert incus_app_is_private_ipv4_prefix("192.168") is True
    assert incus_app_is_private_ipv4_prefix("192.169") is False
    assert incus_app_is_private_ipv4_prefix("010.210") is False


def test_service_order_preserves_stable_ready_order():
    services = {
        "web": {"depends_on": {"postgres": {}, "meilisearch": {}}},
        "postgres": {},
        "meilisearch": {},
    }

    assert incus_app_service_order(services) == ["postgres", "meilisearch", "web"]


def test_service_order_rejects_missing_dependency():
    with pytest.raises(AnsibleFilterError, match="unknown services"):
        incus_app_service_order({"web": {"depends_on": ["missing"]}})


def test_service_order_rejects_cycles():
    with pytest.raises(AnsibleFilterError, match="dependency cycle"):
        incus_app_service_order(
            {
                "one": {"depends_on": ["two"]},
                "two": {"depends_on": ["one"]},
            }
        )


def test_network_names_are_namespaced_unless_external():
    assert incus_app_network_name("gitea", "private", {}) == "gitea-private"
    assert incus_app_network_name("linkwarden", "private", {}) == "linkward-cdac1c"
    assert incus_app_network_name("linkwarden", "proxy", {"external": True}) == "proxy"
    assert incus_app_network_name("linkwarden", "private", {"name": "custom"}) == "custom"


def test_oci_defaults_preserve_image_metadata_and_overlay_environment():
    current = {
        "environment.PATH": "/usr/bin:/bin",
        "environment.USER": "git",
        "environment.APP_MODE": "old",
        "oci.cwd": "/",
        "oci.entrypoint": "/entrypoint server",
        "limits.cpu": "1",
        "volatile.uuid": "ignored",
    }

    assert incus_app_oci_defaults(
        current, {"APP_MODE": "production", "SECRET": 123}
    ) == {
        "oci.cwd": "/",
        "oci.entrypoint": "/entrypoint server",
        "environment_variables": [
            {"name": "APP_MODE", "value": "production"},
            {"name": "PATH", "value": "/usr/bin:/bin"},
            {"name": "SECRET", "value": "123"},
            {"name": "USER", "value": "git"},
        ],
    }


def test_oci_raw_config_preserves_incus_metadata_and_manages_native_values():
    current = {
        "environment.PATH": "/usr/bin:/bin",
        "environment.APP_MODE": "old",
        "image.id": "example:1",
        "oci.entrypoint": "/entrypoint",
        "volatile.uuid": "uuid",
        "limits.cpu": "1",
        "stale.setting": "remove-me",
    }

    assert incus_app_oci_raw_config(
        current,
        {"APP_MODE": "production"},
        {"boot.autostart": True, "limits.cpu": "2", "optional": None},
    ) == {
        "environment.PATH": "/usr/bin:/bin",
        "environment.APP_MODE": "production",
        "image.id": "example:1",
        "oci.entrypoint": "/entrypoint",
        "volatile.uuid": "uuid",
        "boot.autostart": "true",
        "limits.cpu": "2",
    }


def test_devices_are_converted_to_api_mapping():
    assert incus_app_devices_config(
        [
            {"name": "root", "type": "disk", "path": "/", "size": None},
            {"name": "data", "type": "disk", "readonly": False},
        ]
    ) == {
        "root": {"type": "disk", "path": "/"},
        "data": {"type": "disk", "readonly": "false"},
    }


def _ingress_catalog():
    return [
        {
            "application": {
                "name": "ingress",
                "defaults": {"network": "ingress"},
            },
            "services": {"caddy": {"image": "images:debian/13"}},
            "networks": {
                "ingress": {
                    "name": "ingress",
                    "config": {"ipv4.address": "10.210.250.1/24"},
                }
            },
        },
        {
            "application": {
                "name": "linkwarden",
                "defaults": {"network": "private"},
            },
            "services": {
                "postgres": {
                    "image": "docker:postgres:16",
                    "networks": ["private", {"name": "ingress"}],
                    "ingress": {
                        "host": "postgres.fqdn.dev",
                        "port": 5432,
                    },
                },
                "web": {
                    "image": "ghcr:linkwarden/linkwarden:v2.16.0",
                    "networks": [
                        "private",
                        {"name": "ingress", "ipv4_address": "10.210.250.11"},
                    ],
                    "ingress": [
                        {"host": "linkwarden.fqdn.dev", "port": 3000}
                    ],
                },
            },
            "networks": {
                "private": {"config": {"ipv4.address": "10.210.2.1/24"}},
                "ingress": {"external": True, "name": "ingress"},
            },
        },
    ]


def test_network_attachment_accepts_dhcp_and_optional_static_addresses():
    assert incus_app_network_attachment("ingress") == {"name": "ingress"}
    assert incus_app_network_attachment(
        {"name": "ingress", "ipv4_address": "10.210.250.11"}
    ) == {"name": "ingress", "ipv4_address": "10.210.250.11"}


def test_catalog_allows_any_service_to_declare_ingress():
    assert incus_app_validate_catalog(_ingress_catalog()) is True
    assert incus_app_ingress_count(_ingress_catalog()) == 2


def test_catalog_secret_requirements_are_normalized():
    catalog = _ingress_catalog()
    catalog[1]["secrets"] = {
        "path": "incus-apps/apps/linkwarden",
        "required": ["linkwarden_database_password"],
    }

    assert incus_app_secret_specs(catalog) == [
        {
            "path": "incus-apps/apps/linkwarden",
            "required": ["linkwarden_database_password"],
            "optional": False,
            "generate": {},
        }
    ]


def test_catalog_rejects_duplicate_secret_paths():
    catalog = _ingress_catalog()
    catalog[0]["secrets"] = {"path": "incus-apps/shared"}
    catalog[1]["secrets"] = {"path": "incus-apps/shared"}

    with pytest.raises(AnsibleFilterError, match="secret path.*declared by both"):
        incus_app_secret_specs(catalog)


def test_catalog_rejects_duplicate_explicit_addresses():
    catalog = _ingress_catalog()
    catalog[1]["services"]["postgres"]["networks"][1] = {
        "name": "ingress",
        "ipv4_address": "10.210.250.11",
    }
    with pytest.raises(AnsibleFilterError, match="explicit address.*used by both"):
        incus_app_validate_catalog(catalog)


def test_catalog_rejects_duplicate_http_hosts():
    catalog = _ingress_catalog()
    catalog[1]["services"]["postgres"]["ingress"]["host"] = (
        "linkwarden.fqdn.dev"
    )
    with pytest.raises(AnsibleFilterError, match="HTTP ingress host.*declared by both"):
        incus_app_validate_catalog(catalog)


def test_catalog_rejects_colliding_raw_exposures():
    catalog = _ingress_catalog()
    catalog[1]["services"]["web"]["expose"] = {
        "name": "ssh",
        "protocol": "tcp",
        "listen_port": 2222,
        "target_port": 22,
    }
    catalog[1]["services"]["postgres"]["expose"] = {
        "name": "other-ssh",
        "protocol": "tcp",
        "listen_port": 2222,
        "target_port": 22,
        "listen_address": "192.0.2.10",
    }
    with pytest.raises(AnsibleFilterError, match="raw exposure.*collides"):
        incus_app_validate_catalog(catalog)


def test_raw_udp_exposure_becomes_proxy_device():
    assert incus_app_exposure_device(
        {
            "name": "dns",
            "protocol": "udp",
            "listen_address": "192.0.2.10",
            "listen_port": 53,
            "target_port": 5353,
        }
    ) == {
        "name": "dns",
        "type": "proxy",
        "listen": "udp:192.0.2.10:53",
        "connect": "udp:127.0.0.1:5353",
    }


def test_ingress_bindings_use_live_incus_network_state():
    instances = [
        {
            "project": "linkwarden",
            "name": "postgres",
            "status": "Running",
            "expanded_devices": {
                "eth0": {"type": "nic", "network": "linkward-cdac1c"},
                "eth1": {"type": "nic", "network": "ingress"},
            },
            "state": {
                "network": {
                    "eth1": {
                        "addresses": [
                            {
                                "family": "inet",
                                "scope": "global",
                                "address": "10.210.250.21",
                            }
                        ]
                    }
                }
            },
        },
        {
            "project": "linkwarden",
            "name": "web",
            "status": "Running",
            "expanded_devices": {
                "eth0": {"type": "nic", "network": "linkward-cdac1c"},
                "eth1": {"type": "nic", "network": "ingress"},
            },
            "state": {
                "network": {
                    "eth1": {
                        "addresses": [
                            {
                                "family": "inet",
                                "scope": "global",
                                "address": "10.210.250.11",
                            }
                        ]
                    }
                }
            },
        },
    ]

    assert incus_app_ingress_bindings(_ingress_catalog(), instances) == [
        {
            "application": "linkwarden",
            "project": "linkwarden",
            "service": "web",
            "instance": "web",
            "host": "linkwarden.fqdn.dev",
            "scheme": "http",
            "port": 3000,
            "network": "ingress",
            "address": "10.210.250.11",
        },
        {
            "application": "linkwarden",
            "project": "linkwarden",
            "service": "postgres",
            "instance": "postgres",
            "host": "postgres.fqdn.dev",
            "scheme": "http",
            "port": 5432,
            "network": "ingress",
            "address": "10.210.250.21",
        },
    ]
