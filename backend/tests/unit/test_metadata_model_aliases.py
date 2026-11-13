from dataclasses import replace

import pytest

from app.modules.llm_functions.repository import AliasDefinition


@pytest.mark.parametrize(
    "changes",
    [
        {"alias_name": ""},
        {"alias_name": "a" * 129},
        {"alias_name": "bad\x00value"},
        {"function_type": "unknown"},
        {"function_type": ["complete"]},
        {"provider_id": ""},
        {"provider_id": "p" * 65},
        {"model_id": ""},
        {"model_id": "m" * 65},
        {"system_prompt": "p" * 65534},
        {"system_prompt": "\ud800"},
        {"system_prompt": "bad\x00prompt"},
        {"is_default": 1},
        {"is_default": None},
        {"is_active": "true"},
        {"default_params": "[]"},
        {"default_params": '{"x":NaN}'},
        {"default_params": '{"x":1,"x":2}'},
        {"default_params": '{"x":"\\ud800"}'},
        {"default_params": '{"x":1e999}'},
    ],
)
def test_invalid_alias_input_is_rejected_without_value_echo(changes):
    with pytest.raises(ValueError) as error:
        replace(AliasDefinition("Example", "complete", "provider"), **changes)
    for value in ("bad\x00value", "bad\x00prompt", "unknown", "NaN"):
        assert value not in str(error.value)


def test_prompt_and_raw_json_are_preserved_without_repr_readback():
    raw = '{ "temperature" : 0.2500, "nested" : {"label":"漢字"} }'
    definition = AliasDefinition(
        "Example", "complete", "provider", system_prompt="synthetic-prompt", default_params=raw
    )
    assert definition.default_params == raw
    assert definition.system_prompt == "synthetic-prompt"
    assert raw not in repr(definition) and "synthetic-prompt" not in repr(definition)
    assert replace(definition, system_prompt="").system_prompt == ""
