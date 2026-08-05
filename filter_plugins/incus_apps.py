"""Project-specific filters that remain outside cloin.incus_compose."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping

from ansible.errors import AnsibleFilterError

RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def incus_app_is_ipv4_address(value) -> bool:
    """Return whether a value is one unambiguous IPv4 address."""
    try:
        return ipaddress.ip_address(str(value)).version == 4
    except ValueError:
        return False


def incus_app_is_private_ipv4_prefix(value) -> bool:
    """Validate the two-octet RFC1918 prefix used by bundled applications."""
    parts = str(value).split(".")
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        return False
    if any(part != str(int(part)) or not 0 <= int(part) <= 255 for part in parts):
        return False
    address = ipaddress.ip_address(f"{value}.0.0")
    return any(address in network for network in RFC1918_NETWORKS)


def _mapping_list(value, label: str) -> list[dict]:
    if value is None:
        return []
    values = [value] if isinstance(value, Mapping) else value
    if not isinstance(values, list) or any(
        not isinstance(item, Mapping) for item in values
    ):
        raise AnsibleFilterError(f"{label} must be a mapping or list of mappings")
    return [dict(item) for item in values]


def _secret_entries(definition: Mapping, application_name: str) -> list[dict]:
    extension = definition.get("x-incus-apps", {})
    if not isinstance(extension, Mapping):
        raise AnsibleFilterError(
            f"application '{application_name}'.x-incus-apps must be a mapping"
        )
    entries = _mapping_list(
        extension.get("secrets"),
        f"application '{application_name}'.x-incus-apps.secrets",
    )
    paths: set[str] = set()
    for entry in entries:
        path = str(entry.get("path", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9_./-]*", path):
            raise AnsibleFilterError(
                f"application '{application_name}' secret path '{path}' is invalid"
            )
        if path in paths:
            raise AnsibleFilterError(
                f"application '{application_name}' secret path '{path}' is duplicated"
            )
        paths.add(path)
        required = entry.get("required", [])
        if not isinstance(required, list) or any(
            not isinstance(name, str)
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name)
            for name in required
        ):
            raise AnsibleFilterError(
                f"application '{application_name}' secret required values must be "
                "Ansible variable names"
            )
        if len(required) != len(set(required)):
            raise AnsibleFilterError(
                f"application '{application_name}' secret required values are duplicated"
            )
        generate = entry.get("generate", {})
        if not isinstance(generate, Mapping):
            raise AnsibleFilterError(
                f"application '{application_name}' secret generate must be a mapping"
            )
        for variable_name, generator in generate.items():
            if variable_name not in required:
                raise AnsibleFilterError(
                    f"generated secret '{variable_name}' must also be required"
                )
            if not isinstance(generator, Mapping):
                raise AnsibleFilterError(
                    f"generator for '{variable_name}' must be a mapping"
                )
            if generator.get("type", "random_string") != "random_string":
                raise AnsibleFilterError(
                    f"generator for '{variable_name}' has an unsupported type"
                )
            length = generator.get("length", 48)
            if (
                isinstance(length, bool)
                or not isinstance(length, int)
                or not 16 <= length <= 256
            ):
                raise AnsibleFilterError(
                    f"generator for '{variable_name}' length must be between 16 and 256"
                )
        entry["path"] = path
        entry["required"] = required
        entry["optional"] = bool(entry.get("optional", False))
        entry["generate"] = dict(generate)
    return entries


def incus_app_secret_specs(definitions: list) -> list[dict]:
    """Return validated, path-unique OpenBao requirements for a catalog."""
    if not isinstance(definitions, list):
        raise AnsibleFilterError("the application catalog must be a list")

    result: list[dict] = []
    owners: dict[str, str] = {}
    for definition in definitions:
        if not isinstance(definition, Mapping):
            raise AnsibleFilterError(
                "each application metadata entry must be a mapping"
            )
        application_name = str(definition.get("name", ""))
        if not application_name:
            raise AnsibleFilterError("application metadata requires name")
        for entry in _secret_entries(definition, application_name):
            if entry["path"] in owners:
                raise AnsibleFilterError(
                    f"OpenBao secret path '{entry['path']}' is declared by both "
                    f"{owners[entry['path']]} and {application_name}"
                )
            owners[entry["path"]] = application_name
            result.append(entry)
    return result


class FilterModule:
    """Expose only lab-specific validation and OpenBao metadata filters."""

    def filters(self):
        return {
            "incus_app_is_ipv4_address": incus_app_is_ipv4_address,
            "incus_app_is_private_ipv4_prefix": incus_app_is_private_ipv4_prefix,
            "incus_app_secret_specs": incus_app_secret_specs,
        }
