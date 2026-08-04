"""Write structured recovery data directly as an Ansible Vault document."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping

import yaml
from ansible.errors import AnsibleActionFail
from ansible.parsing.vault import VaultLib, VaultSecret
from ansible.plugins.action import ActionBase


def _plain_yaml_value(value):
    """Convert Ansible's wrapped scalar types to PyYAML-safe primitives."""

    if isinstance(value, Mapping):
        return {
            _plain_yaml_value(key): _plain_yaml_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_plain_yaml_value(item) for item in value]
    if isinstance(value, str):
        return value.encode("utf-8").decode("utf-8")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise AnsibleActionFail(
        f"data contains an unsupported value of type {type(value).__name__}"
    )


class ActionModule(ActionBase):
    """Encrypt on the controller so plaintext is never written to disk."""

    TRANSFERS_FILES = False

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        arguments = self._task.args

        missing = [
            name
            for name in ("path", "password", "data")
            if name not in arguments
        ]
        if missing:
            raise AnsibleActionFail(
                f"missing required arguments: {', '.join(sorted(missing))}"
            )

        path = os.path.abspath(os.path.expanduser(str(arguments["path"])))
        password = str(arguments["password"])
        data = arguments["data"]
        if not password:
            raise AnsibleActionFail("the Ansible Vault password cannot be empty")
        if not isinstance(data, Mapping):
            raise AnsibleActionFail("data must be a mapping")
        data = _plain_yaml_value(data)

        try:
            mode = int(str(arguments.get("mode", "0600")), 8)
        except ValueError as exc:
            raise AnsibleActionFail("mode must be an octal string") from exc

        secret = VaultSecret(password.encode("utf-8"))
        vault = VaultLib([("default", secret)])
        plaintext = yaml.safe_dump(
            dict(data),
            default_flow_style=False,
            explicit_start=True,
            sort_keys=True,
        ).encode("utf-8")

        existing_matches = False
        current_mode = None
        if os.path.exists(path):
            try:
                with open(path, "rb") as existing_file:
                    existing_plaintext = vault.decrypt(existing_file.read())
                existing_matches = (yaml.safe_load(existing_plaintext) or {}) == data
                current_mode = stat.S_IMODE(os.stat(path).st_mode)
            except Exception as exc:
                raise AnsibleActionFail(
                    f"cannot decrypt existing recovery vault: {exc}"
                ) from exc

        result.update(
            changed=not existing_matches or current_mode != mode,
            path=path,
        )
        if not result["changed"] or self._task.check_mode:
            return result

        directory = os.path.dirname(path)
        os.makedirs(directory, mode=0o700, exist_ok=True)

        if existing_matches:
            os.chmod(path, mode)
            return result

        ciphertext = vault.encrypt(plaintext, secret=secret)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".openbao-vault-",
            dir=directory,
        )
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(ciphertext)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
            os.chmod(path, mode)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

        return result
