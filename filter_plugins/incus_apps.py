"""Filters used by the Incus application role."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Mapping

from ansible.errors import AnsibleFilterError

VALID_KINDS = {
    "application-container",
    "system-container",
    "virtual-machine",
}

VALID_INGRESS_SCHEMES = {"http", "https"}
VALID_EXPOSURE_PROTOCOLS = {"tcp", "udp"}
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


def _application_context(definition: Mapping) -> tuple[Mapping, Mapping, Mapping]:
    if not isinstance(definition, Mapping):
        raise AnsibleFilterError("each application definition must be a mapping")

    application = definition.get("application")
    if not isinstance(application, Mapping) or not application.get("name"):
        raise AnsibleFilterError("application.name is required")

    services = definition.get("services", {})
    networks = definition.get("networks", {})
    if not isinstance(services, Mapping):
        raise AnsibleFilterError(
            f"application '{application['name']}'.services must be a mapping"
        )
    if not isinstance(networks, Mapping):
        raise AnsibleFilterError(
            f"application '{application['name']}'.networks must be a mapping"
        )
    for name, network in networks.items():
        if not isinstance(network, Mapping):
            raise AnsibleFilterError(
                f"application '{application['name']}' network '{name}' must be a mapping"
            )
    return application, services, networks


def incus_app_network_attachment(value) -> dict:
    """Normalize a service network entry while retaining legacy strings."""
    if isinstance(value, str):
        if not value:
            raise AnsibleFilterError("service network names cannot be empty")
        return {"name": value}
    if not isinstance(value, Mapping) or not value.get("name"):
        raise AnsibleFilterError(
            "each service network must be a name or a mapping with a name"
        )

    result = dict(value)
    for family in ("ipv4", "ipv6"):
        key = f"{family}_address"
        if key not in result:
            continue
        try:
            address = ipaddress.ip_address(str(result[key]))
        except ValueError as exc:
            raise AnsibleFilterError(
                f"service network {key} '{result[key]}' is invalid"
            ) from exc
        expected_version = 4 if family == "ipv4" else 6
        if address.version != expected_version:
            raise AnsibleFilterError(
                f"service network {key} must be an IPv{expected_version} address"
            )
        result[key] = str(address)
    return result


def _service_networks(service: Mapping, defaults: Mapping) -> list[dict]:
    raw_networks = service.get("networks")
    if raw_networks is None:
        raw_networks = [defaults["network"]] if defaults.get("network") else []
    if not isinstance(raw_networks, list):
        raise AnsibleFilterError("service.networks must be a list")

    attachments = [incus_app_network_attachment(item) for item in raw_networks]
    names = [item["name"] for item in attachments]
    if len(names) != len(set(names)):
        raise AnsibleFilterError("a service cannot attach the same network more than once")
    return attachments


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
    entries = _mapping_list(
        definition.get("secrets"),
        f"application '{application_name}'.secrets",
    )
    paths: set[str] = set()
    for entry in entries:
        path = str(entry.get("path", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9_./-]*", path):
            raise AnsibleFilterError(
                f"application '{application_name}'.secrets path '{path}' is invalid"
            )
        if path in paths:
            raise AnsibleFilterError(
                f"application '{application_name}'.secrets path '{path}' is duplicated"
            )
        paths.add(path)
        required = entry.get("required", [])
        if not isinstance(required, list) or any(
            not isinstance(name, str)
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name)
            for name in required
        ):
            raise AnsibleFilterError(
                f"application '{application_name}'.secrets.required must be a list "
                "of Ansible variable names"
            )
        if len(required) != len(set(required)):
            raise AnsibleFilterError(
                f"application '{application_name}'.secrets.required contains duplicates"
            )
        generate = entry.get("generate", {})
        if not isinstance(generate, Mapping):
            raise AnsibleFilterError(
                f"application '{application_name}'.secrets.generate must be a mapping"
            )
        for variable_name, generator in generate.items():
            if variable_name not in required:
                raise AnsibleFilterError(
                    f"generated secret '{variable_name}' must also be listed as required"
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
        application, _services, _networks = _application_context(definition)
        application_name = str(application["name"])
        for entry in _secret_entries(definition, application_name):
            if entry["path"] in owners:
                raise AnsibleFilterError(
                    f"OpenBao secret path '{entry['path']}' is declared by both "
                    f"{owners[entry['path']]} and {application_name}"
                )
            owners[entry["path"]] = application_name
            result.append(entry)
    return result


def _port(value, label: str) -> int:
    if isinstance(value, bool):
        raise AnsibleFilterError(f"{label} must be an integer between 1 and 65535")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AnsibleFilterError(
            f"{label} must be an integer between 1 and 65535"
        ) from exc
    if result < 1 or result > 65535:
        raise AnsibleFilterError(f"{label} must be an integer between 1 and 65535")
    return result


def _ingress_entries(service: Mapping, label: str) -> list[dict]:
    entries = _mapping_list(service.get("ingress"), f"{label}.ingress")
    for entry in entries:
        host = str(entry.get("host", "")).lower()
        if not re.fullmatch(
            r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
            host,
        ):
            raise AnsibleFilterError(f"{label}.ingress host '{host}' is not a valid FQDN")
        entry["host"] = host
        entry["port"] = _port(entry.get("port"), f"{label}.ingress.port")
        scheme = str(entry.get("scheme", "http")).lower()
        if scheme not in VALID_INGRESS_SCHEMES:
            raise AnsibleFilterError(
                f"{label}.ingress.scheme must be one of {sorted(VALID_INGRESS_SCHEMES)}"
            )
        entry["scheme"] = scheme
        entry["network"] = str(entry.get("network", "ingress"))
    return entries


def _exposure_entries(service: Mapping, label: str) -> list[dict]:
    entries = _mapping_list(service.get("expose"), f"{label}.expose")
    names: set[str] = set()
    for entry in entries:
        name = str(entry.get("name", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise AnsibleFilterError(
                f"{label}.expose.name must contain lowercase letters, digits, or hyphens"
            )
        if name in names:
            raise AnsibleFilterError(f"{label}.expose name '{name}' is duplicated")
        names.add(name)

        protocol = str(entry.get("protocol", "tcp")).lower()
        if protocol not in VALID_EXPOSURE_PROTOCOLS:
            raise AnsibleFilterError(
                f"{label}.expose.protocol must be one of "
                f"{sorted(VALID_EXPOSURE_PROTOCOLS)}"
            )
        entry["protocol"] = protocol
        entry["listen_port"] = _port(
            entry.get("listen_port"), f"{label}.expose.listen_port"
        )
        entry["target_port"] = _port(
            entry.get("target_port"), f"{label}.expose.target_port"
        )
        for key, default in (
            ("listen_address", "0.0.0.0"),
            ("target_address", "127.0.0.1"),
        ):
            raw_address = str(entry.get(key, default))
            try:
                entry[key] = str(ipaddress.ip_address(raw_address))
            except ValueError as exc:
                raise AnsibleFilterError(
                    f"{label}.expose.{key} '{raw_address}' is invalid"
                ) from exc
    return entries


def _physical_network_name(
    application_name: str, logical_name: str, networks: Mapping
) -> str:
    if logical_name not in networks:
        raise AnsibleFilterError(
            f"application '{application_name}' references undefined network "
            f"'{logical_name}'"
        )
    return incus_app_network_name(
        application_name, logical_name, networks[logical_name]
    )


def incus_app_validate_catalog(definitions: list) -> bool:
    """Validate cross-application network and ingress invariants."""
    if not isinstance(definitions, list):
        raise AnsibleFilterError("the application catalog must be a list")

    explicit_addresses: dict[tuple[str, str], str] = {}
    ingress_hosts: dict[str, str] = {}
    exposures: list[tuple[str, int, str, str]] = []
    managed_networks: dict[str, Mapping] = {}

    contexts = []
    for definition in definitions:
        application, services, networks = _application_context(definition)
        app_name = str(application["name"])
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", app_name):
            raise AnsibleFilterError(f"application name '{app_name}' is invalid")
        incus_app_service_order(services)
        _secret_entries(definition, app_name)
        contexts.append((definition, application, services, networks))

        for logical_name, network in networks.items():
            if not network.get("external", False):
                physical_name = incus_app_network_name(app_name, logical_name, network)
                previous = managed_networks.get(physical_name)
                if previous is not None and previous != network:
                    raise AnsibleFilterError(
                        f"managed network '{physical_name}' has conflicting definitions"
                    )
                managed_networks[physical_name] = network

    for _definition, application, services, networks in contexts:
        app_name = str(application["name"])
        defaults = application.get("defaults", {})
        if not isinstance(defaults, Mapping):
            raise AnsibleFilterError(f"application '{app_name}'.defaults must be a mapping")

        for service_name, service in services.items():
            label = f"application '{app_name}' service '{service_name}'"
            if not isinstance(service, Mapping):
                raise AnsibleFilterError(f"{label} must be a mapping")
            if service.get("network_manager") not in (None, "systemd-networkd"):
                raise AnsibleFilterError(
                    f"{label}.network_manager must be 'systemd-networkd' when set"
                )
            attachments = _service_networks(service, defaults)
            attachment_names = {item["name"] for item in attachments}
            for attachment in attachments:
                logical_name = attachment["name"]
                physical_name = _physical_network_name(app_name, logical_name, networks)
                for key in ("ipv4_address", "ipv6_address"):
                    if key not in attachment:
                        continue
                    address = attachment[key]
                    address_key = (physical_name, address)
                    if address_key in explicit_addresses:
                        raise AnsibleFilterError(
                            f"explicit address {address} on network '{physical_name}' "
                            f"is used by both {explicit_addresses[address_key]} and {label}"
                        )
                    explicit_addresses[address_key] = label

                    network = managed_networks.get(physical_name)
                    family = "ipv4" if key.startswith("ipv4") else "ipv6"
                    subnet_value = (network or {}).get("config", {}).get(
                        f"{family}.address"
                    )
                    if subnet_value and subnet_value not in ("auto", "none"):
                        subnet = ipaddress.ip_interface(str(subnet_value)).network
                        if ipaddress.ip_address(address) not in subnet:
                            raise AnsibleFilterError(
                                f"explicit address {address} is outside network "
                                f"'{physical_name}' subnet {subnet}"
                            )

            native_names = {
                str(device.get("name"))
                for device in service.get("devices", [])
                if isinstance(device, Mapping) and device.get("name")
            }
            for entry in _exposure_entries(service, label):
                if entry["name"] in native_names:
                    raise AnsibleFilterError(
                        f"{label} uses device name '{entry['name']}' in both expose and devices"
                    )
                current = (
                    entry["protocol"],
                    entry["listen_port"],
                    entry["listen_address"],
                    label,
                )
                for protocol, port, address, owner in exposures:
                    wildcard = {"0.0.0.0", "::"}
                    if (
                        protocol == current[0]
                        and port == current[1]
                        and (
                            address == current[2]
                            or address in wildcard
                            or current[2] in wildcard
                        )
                    ):
                        raise AnsibleFilterError(
                            f"raw exposure {protocol}:{address}:{port} for {owner} "
                            f"collides with {label}"
                        )
                exposures.append(current)

            for entry in _ingress_entries(service, label):
                if entry["network"] not in attachment_names:
                    raise AnsibleFilterError(
                        f"{label} declares ingress on network '{entry['network']}' "
                        "but is not attached to it"
                    )
                host = entry["host"]
                if host in ingress_hosts:
                    raise AnsibleFilterError(
                        f"HTTP ingress host '{host}' is declared by both "
                        f"{ingress_hosts[host]} and {label}"
                    )
                ingress_hosts[host] = label
    return True


def _format_proxy_address(address: str, port: int) -> str:
    parsed = ipaddress.ip_address(address)
    return f"[{parsed}]:{port}" if parsed.version == 6 else f"{parsed}:{port}"


def incus_app_exposure_device(exposure: Mapping) -> dict:
    """Convert a structured TCP/UDP exposure into an Incus proxy device."""
    entries = _exposure_entries({"expose": exposure}, "service")
    if len(entries) != 1:
        raise AnsibleFilterError("one exposure mapping is required")
    entry = entries[0]
    device = {
        "name": entry["name"],
        "type": "proxy",
        "listen": (
            f"{entry['protocol']}:"
            f"{_format_proxy_address(entry['listen_address'], entry['listen_port'])}"
        ),
        "connect": (
            f"{entry['protocol']}:"
            f"{_format_proxy_address(entry['target_address'], entry['target_port'])}"
        ),
    }
    for key in ("bind", "gid", "mode", "nat", "proxy_protocol", "security.gid"):
        if key in exposure:
            device[key] = exposure[key]
    return device


def incus_app_ingress_count(definitions: list) -> int:
    """Return the number of desired running HTTP ingress routes."""
    incus_app_validate_catalog(definitions)
    count = 0
    for definition in definitions:
        application, services, _networks = _application_context(definition)
        if application.get("state", "present") != "present":
            continue
        for service_name, service in services.items():
            if service.get("state", "started") != "started":
                continue
            count += len(
                _ingress_entries(
                    service,
                    f"application '{application['name']}' service '{service_name}'",
                )
            )
    return count


def incus_app_ingress_bindings(
    definitions: list, instances: list, strict: bool = True
) -> list[dict]:
    """Resolve desired HTTP routes to live addresses reported by Incus."""
    incus_app_validate_catalog(definitions)
    if not isinstance(instances, list):
        raise AnsibleFilterError("live Incus instances must be a list")
    instance_index = {
        (str(instance.get("project", "default")), str(instance.get("name", ""))): instance
        for instance in instances
        if isinstance(instance, Mapping)
    }
    bindings = []

    for definition in definitions:
        application, services, networks = _application_context(definition)
        if application.get("state", "present") != "present":
            continue
        app_name = str(application["name"])
        project = str(application.get("project") or app_name)
        defaults = application.get("defaults", {})
        for service_name, service in services.items():
            if service.get("state", "started") != "started":
                continue
            label = f"application '{app_name}' service '{service_name}'"
            entries = _ingress_entries(service, label)
            if not entries:
                continue
            instance_name = str(service.get("name") or service_name)
            instance = instance_index.get((project, instance_name))
            if not instance or instance.get("status") != "Running":
                if strict:
                    raise AnsibleFilterError(f"{label} is not running in Incus")
                continue

            attachments = {
                item["name"]: item for item in _service_networks(service, defaults)
            }
            expanded_devices = instance.get("expanded_devices", {})
            state_networks = (instance.get("state") or {}).get("network", {})
            for entry in entries:
                logical_network = entry["network"]
                if logical_network not in attachments:
                    if strict:
                        raise AnsibleFilterError(
                            f"{label} is not attached to ingress network "
                            f"'{logical_network}'"
                        )
                    continue
                physical_network = _physical_network_name(
                    app_name, logical_network, networks
                )
                matching_devices = [
                    name
                    for name, device in expanded_devices.items()
                    if device.get("type") == "nic"
                    and device.get("network") == physical_network
                ]
                addresses = []
                if len(matching_devices) == 1:
                    addresses = [
                        address.get("address")
                        for address in state_networks.get(matching_devices[0], {}).get(
                            "addresses", []
                        )
                        if address.get("family") == "inet"
                        and address.get("scope") == "global"
                    ]
                if len(addresses) != 1:
                    if strict:
                        raise AnsibleFilterError(
                            f"{label} does not have exactly one live IPv4 address on "
                            f"network '{physical_network}'"
                        )
                    continue
                bindings.append(
                    {
                        "application": app_name,
                        "project": project,
                        "service": str(service_name),
                        "instance": instance_name,
                        "host": entry["host"],
                        "scheme": entry["scheme"],
                        "port": entry["port"],
                        "network": physical_network,
                        "address": addresses[0],
                    }
                )
    return sorted(bindings, key=lambda item: item["host"])


def incus_app_service_order(services: Mapping) -> list[str]:
    """Return a stable topological ordering of an application's services."""
    if not isinstance(services, Mapping):
        raise AnsibleFilterError("application.services must be a mapping")

    service_names = list(services)
    dependencies: dict[str, list[str]] = {}

    for name, definition in services.items():
        if not isinstance(definition, Mapping):
            raise AnsibleFilterError(f"service '{name}' must be a mapping")

        kind = definition.get("kind", "application-container")
        if kind not in VALID_KINDS:
            raise AnsibleFilterError(
                f"service '{name}' has unsupported kind '{kind}'; "
                f"expected one of {sorted(VALID_KINDS)}"
            )

        raw_dependencies = definition.get("depends_on", {})
        if isinstance(raw_dependencies, list):
            service_dependencies = raw_dependencies
        elif isinstance(raw_dependencies, Mapping):
            service_dependencies = list(raw_dependencies)
        else:
            raise AnsibleFilterError(
                f"service '{name}'.depends_on must be a list or mapping"
            )

        unknown = [item for item in service_dependencies if item not in services]
        if unknown:
            raise AnsibleFilterError(
                f"service '{name}' depends on unknown services: {', '.join(unknown)}"
            )
        dependencies[name] = service_dependencies

    ordered: list[str] = []
    remaining = service_names.copy()
    while remaining:
        ready = [
            name
            for name in remaining
            if all(dependency in ordered for dependency in dependencies[name])
        ]
        if not ready:
            raise AnsibleFilterError(
                "application.services contains a dependency cycle involving: "
                + ", ".join(remaining)
            )
        for name in ready:
            ordered.append(name)
            remaining.remove(name)

    return ordered


def incus_app_network_name(application_name: str, logical_name: str, definition: Mapping) -> str:
    """Resolve a logical network to its physical Incus network name."""
    if definition.get("external", False):
        return definition.get("name", logical_name)
    configured_name = definition.get("name")
    if configured_name:
        return configured_name

    candidate = f"{application_name}-{logical_name}"
    if len(candidate) <= 15:
        return candidate

    suffix = hashlib.sha256(candidate.encode()).hexdigest()[:6]
    return f"{candidate[:8].rstrip('-')}-{suffix}"


def incus_app_oci_defaults(
    current_config: Mapping, desired_environment: Mapping
) -> dict:
    """Preserve OCI image defaults and overlay application environment values.

    Incus materializes an OCI image's entrypoint, uid/gid, working directory,
    and environment in instance configuration.  The damex.incus instance
    module manages configuration strictly, so callers must include those
    image-derived values on every subsequent update.
    """
    if not isinstance(current_config, Mapping):
        raise AnsibleFilterError("current OCI instance config must be a mapping")
    if not isinstance(desired_environment, Mapping):
        raise AnsibleFilterError("service.environment must be a mapping")

    result = {
        key: value
        for key, value in current_config.items()
        if isinstance(key, str) and key.startswith("oci.")
    }
    environment = {
        key.removeprefix("environment."): value
        for key, value in current_config.items()
        if isinstance(key, str) and key.startswith("environment.")
    }
    environment.update(
        {str(key): str(value) for key, value in desired_environment.items()}
    )
    if environment:
        result["environment_variables"] = [
            {"name": name, "value": str(environment[name])}
            for name in sorted(environment)
        ]
    return result


def _incus_string(value) -> str:
    """Render an Incus config/device scalar the same way as its API."""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def incus_app_oci_raw_config(
    current_config: Mapping,
    desired_environment: Mapping,
    native_config: Mapping,
) -> dict[str, str]:
    """Build a complete raw Incus config for an OCI instance API update."""
    if not isinstance(current_config, Mapping):
        raise AnsibleFilterError("current OCI instance config must be a mapping")
    if not isinstance(desired_environment, Mapping):
        raise AnsibleFilterError("service.environment must be a mapping")
    if not isinstance(native_config, Mapping):
        raise AnsibleFilterError("native instance config must be a mapping")

    preserved_prefixes = ("image.", "volatile.", "oci.", "environment.")
    result = {
        str(key): _incus_string(value)
        for key, value in current_config.items()
        if isinstance(key, str) and key.startswith(preserved_prefixes)
    }
    for name, value in desired_environment.items():
        result[f"environment.{name}"] = _incus_string(value)
    for key, value in native_config.items():
        if key == "environment_variables":
            continue
        if value is not None:
            result[str(key)] = _incus_string(value)
    return result


def incus_app_devices_config(devices: list) -> dict[str, dict[str, str]]:
    """Convert the role's named device list to the Incus API device mapping."""
    if not isinstance(devices, list):
        raise AnsibleFilterError("instance devices must be a list")

    result = {}
    for device in devices:
        if not isinstance(device, Mapping) or not device.get("name"):
            raise AnsibleFilterError("every instance device must be a mapping with a name")
        result[str(device["name"])] = {
            str(key): _incus_string(value)
            for key, value in device.items()
            if key != "name" and value is not None
        }
    return result


class FilterModule:
    """Expose Incus application filters to Ansible."""

    def filters(self):
        return {
            "incus_app_is_ipv4_address": incus_app_is_ipv4_address,
            "incus_app_is_private_ipv4_prefix": incus_app_is_private_ipv4_prefix,
            "incus_app_service_order": incus_app_service_order,
            "incus_app_network_name": incus_app_network_name,
            "incus_app_network_attachment": incus_app_network_attachment,
            "incus_app_validate_catalog": incus_app_validate_catalog,
            "incus_app_secret_specs": incus_app_secret_specs,
            "incus_app_exposure_device": incus_app_exposure_device,
            "incus_app_ingress_count": incus_app_ingress_count,
            "incus_app_ingress_bindings": incus_app_ingress_bindings,
            "incus_app_oci_defaults": incus_app_oci_defaults,
            "incus_app_oci_raw_config": incus_app_oci_raw_config,
            "incus_app_devices_config": incus_app_devices_config,
        }
