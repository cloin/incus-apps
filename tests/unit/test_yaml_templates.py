from pathlib import Path

import yaml
from jinja2 import Environment


def _strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def test_yaml_jinja_expressions_parse():
    repository_root = Path(__file__).resolve().parents[2]
    environment = Environment()

    for path in repository_root.rglob("*.yml"):
        if any(
            part in {".ansible", ".venv", ".cache", "recovery"}
            for part in path.parts
        ):
            continue
        for document in yaml.safe_load_all(path.read_text()):
            for value in _strings(document):
                if "{{" in value or "{%" in value:
                    environment.parse(value)
