#!/usr/bin/python
"""Read application identity and secret metadata without rendering Jinja values."""

from __future__ import annotations

import glob
import os

import yaml
from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = r"""
---
module: incus_apps_catalog_metadata
short_description: Read safe metadata from Incus application YAML files
options:
  directory:
    type: path
    required: true
  application:
    type: str
    required: false
"""


def run_module() -> None:
    module = AnsibleModule(
        argument_spec={
            "directory": {"type": "path", "required": True},
            "application": {"type": "str", "required": False},
        },
        supports_check_mode=True,
    )

    directory = os.path.abspath(module.params["directory"])
    requested_application = module.params["application"]
    metadata = []

    for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        try:
            with open(path, encoding="utf-8") as application_file:
                definition = yaml.safe_load(application_file) or {}
        except (OSError, yaml.YAMLError) as exc:
            module.fail_json(msg=f"cannot read application metadata from {path}: {exc}")

        application = definition.get("application", {})
        name = application.get("name") if isinstance(application, dict) else None
        if not name:
            module.fail_json(msg=f"{path} does not declare application.name")
        if requested_application and name != requested_application:
            continue
        metadata.append(
            {
                "application": {
                    "name": name,
                    "state": application.get("state", "present"),
                },
                "secrets": definition.get("secrets", []),
                "source": path,
            }
        )

    if requested_application and not metadata:
        module.fail_json(
            msg=f"application '{requested_application}' does not exist in {directory}"
        )

    module.exit_json(changed=False, catalog_metadata=metadata)


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
