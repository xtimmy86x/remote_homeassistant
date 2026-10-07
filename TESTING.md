# Testing with Home Assistant Core

The registry tests run the integration inside Home Assistant Core's test
fixture. They use Home Assistant's real entity registry and configuration
entries. The remote WebSocket responses are simulated so that success,
failure, and stale entries can be checked without connecting to a live home.

Install [uv](https://docs.astral.sh/uv/), then run from the repository root:

```sh
uv venv --python 3.14.2 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

The local `.venv` is ignored by Git. These tests do not replace an end-to-end
check between two running Home Assistant instances for WebSocket behavior.
