import yaml

from action_plugins.incus_apps_vault_file import _plain_yaml_value


class WrappedString(str):
    """Stand in for Ansible's template-aware string subclasses."""


def test_plain_yaml_value_removes_wrapped_string_types_recursively():
    wrapped = {
        WrappedString("openbao_bootstrap_root_token"): WrappedString("secret"),
        WrappedString("nested"): [WrappedString("value")],
    }

    normalized = _plain_yaml_value(wrapped)

    assert all(type(key) is str for key in normalized)
    assert type(normalized["openbao_bootstrap_root_token"]) is str
    assert type(normalized["nested"][0]) is str
    assert yaml.safe_load(yaml.safe_dump(normalized)) == normalized
