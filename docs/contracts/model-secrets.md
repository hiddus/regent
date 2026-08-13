# Model Secret Configuration

Model credentials must not be committed, uploaded in deployment archives, stored
in the database, or printed in logs.

The production secret file is:

```text
/opt/regent/.secrets.env
```

Required OpenAI-compatible fields:

```text
REGENT_MODEL_PROVIDER=openai-compatible
REGENT_MODEL_BASE_URL=https://provider.example/v1
REGENT_MODEL_NAME=provider-model-name
# Optional aliases kept in sync with secrets (not used as primary unless wired):
# REGENT_MODEL_NAME_2=provider-model-name-2
# REGENT_MODEL_NAME_3=provider-model-name-3
REGENT_MODEL_API_KEY=secret

IMPORTANT: ``REGENT_MODEL_BASE_URL`` domain must match the gateway that owns
``REGENT_MODEL_NAME`` (e.g. showmac gateway + ``tencent/...`` ids). Mixing
``api.deepseek.com`` with a third-party gateway model id causes provider
domain/name mismatch failures.
```

The file must be owned by root with mode `0600`. S1 loads and validates the
settings but does not call a model. S2 introduces the provider adapter.
