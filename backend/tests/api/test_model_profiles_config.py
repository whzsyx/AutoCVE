import json
from types import SimpleNamespace

from app.api.v1.endpoints.config import (
    LLMConfigSchema,
    LLMConnectionTestRequest,
    SENSITIVE_LLM_FIELDS,
    _apply_llm_connection_test_overrides,
    _explicit_sampling_updates,
    _decrypt_config,
    _encrypt_config,
    _merge_user_config,
    _normalize_model_profiles,
    get_default_config,
)


def test_llm_connection_test_uses_unsaved_temperature_override():
    llm_config = {
        "llmProvider": "deepseek",
        "llmModel": "deepseek-chat",
        "llmTemperature": 0.1,
    }
    payload = LLMConnectionTestRequest(
        provider="moonshot",
        model="kimi-k2.6",
        temperature=1,
        topP=0.95,
    )

    _apply_llm_connection_test_overrides(llm_config, payload)

    assert llm_config["llmProvider"] == "moonshot"
    assert llm_config["llmModel"] == "kimi-k2.6"
    assert llm_config["llmTemperature"] == 1
    assert llm_config["llmTopP"] == 0.95


def test_llm_connection_test_can_clear_sampling_values_for_auto_mode():
    llm_config = {"llmTemperature": 0.1, "llmTopP": 1.0}
    payload = LLMConnectionTestRequest(
        provider="moonshot",
        temperature=None,
        topP=None,
    )

    _apply_llm_connection_test_overrides(llm_config, payload)

    assert llm_config["llmTemperature"] is None
    assert llm_config["llmTopP"] is None


def test_sampling_fields_preserve_explicit_null_for_persistence():
    incoming = LLMConfigSchema(llmTemperature=None, llmTopP=None)

    assert _explicit_sampling_updates(incoming) == {
        "llmTemperature": None,
        "llmTopP": None,
    }


def test_default_config_exposes_empty_model_profiles():
    config = get_default_config()

    assert config["llmConfig"]["modelProfiles"] == []
    assert config["llmConfig"]["llmTemperature"] is None
    assert config["llmConfig"]["llmTopP"] is None


def test_normalize_model_profiles_defaults_first_when_missing():
    profiles = _normalize_model_profiles(
        [
            {"id": "claude-profile", "name": "Claude"},
            {"id": "deepseek-profile", "name": "DeepSeek"},
        ]
    )

    assert profiles[0]["isDefault"] is True
    assert profiles[1]["isDefault"] is False


def test_normalize_model_profiles_keeps_single_default():
    profiles = _normalize_model_profiles(
        [
            {"id": "claude-profile", "name": "Claude", "isDefault": False},
            {"id": "deepseek-profile", "name": "DeepSeek", "isDefault": True},
            {"id": "openai-profile", "name": "OpenAI", "isDefault": True},
        ]
    )

    assert profiles[0]["isDefault"] is False
    assert profiles[1]["isDefault"] is True
    assert profiles[2]["isDefault"] is False


def test_encrypt_config_encrypts_model_profile_secrets():
    encrypted = _encrypt_config(
        {
            "modelProfiles": [
                {
                    "id": "deepseek-profile",
                    "name": "DeepSeek",
                    "llmProvider": "deepseek",
                    "llmApiKey": "plain-profile-key",
                    "llmModel": "deepseek-chat",
                    "llmBaseUrl": "https://api.deepseek.com/v1",
                    "env": {
                        "DEEPSEEK_API_KEY": "plain-env-key",
                        "EMPTY": "",
                    },
                }
            ]
        },
        SENSITIVE_LLM_FIELDS,
    )

    profile = encrypted["modelProfiles"][0]
    assert profile["isDefault"] is True
    assert profile["llmApiKey"] != "plain-profile-key"
    assert profile["env"]["DEEPSEEK_API_KEY"] != "plain-env-key"
    assert "EMPTY" not in profile["env"]

    decrypted = _decrypt_config(encrypted, SENSITIVE_LLM_FIELDS)
    assert decrypted["modelProfiles"][0]["llmApiKey"] == "plain-profile-key"
    assert decrypted["modelProfiles"][0]["env"]["DEEPSEEK_API_KEY"] == "plain-env-key"


def test_merge_user_config_preserves_model_profiles():
    record = SimpleNamespace(
        llm_config=json.dumps(
            _encrypt_config(
                {
                    "modelProfiles": [
                        {
                            "id": "claude-profile",
                            "name": "Claude",
                            "llmProvider": "claude",
                            "llmApiKey": "claude-key",
                            "llmModel": "claude-sonnet-4-5",
                            "llmBaseUrl": "https://api.anthropic.com",
                            "env": {"ANTHROPIC_AUTH_TOKEN": "claude-env-key"},
                        }
                    ]
                },
                SENSITIVE_LLM_FIELDS,
            )
        ),
        other_config=json.dumps({}),
    )

    merged = _merge_user_config(record)

    assert merged["llmConfig"]["modelProfiles"][0]["name"] == "Claude"
    assert merged["llmConfig"]["modelProfiles"][0]["llmApiKey"] == "claude-key"
    assert merged["llmConfig"]["modelProfiles"][0]["env"]["ANTHROPIC_AUTH_TOKEN"] == "claude-env-key"


def test_partial_llm_config_does_not_emit_unset_model_profiles():
    incoming = LLMConfigSchema(llmProvider="deepseek")

    assert "modelProfiles" not in incoming.model_dump(exclude_none=True, exclude_unset=True)


def test_decrypt_config_does_not_add_model_profiles_when_absent():
    decrypted = _decrypt_config({"githubToken": "token"}, ["githubToken"])

    assert "modelProfiles" not in decrypted
