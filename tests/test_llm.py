"""Tests for LLM client."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.model.llm import LLMClient, VLMClient, get_llm_client, vLLMClient


def test_llm_client_init():
    """Test LLMClient initialization."""
    client = LLMClient()
    assert client.model == "qwen-vl-plus"


def test_vllm_client_init():
    """Test vLLMClient initialization."""
    client = vLLMClient()
    assert client.model == "qwen-vl-plus"
    assert "localhost:8000" in client.api_base
    assert client.api_key == "EMPTY"
    assert client.temperature == 0.7
    assert client.max_tokens == 2048


def test_vllm_client_custom_params():
    """Test vLLMClient with custom parameters."""
    client = vLLMClient(model="custom-model", api_base="http://custom:8000/v1", temperature=0.5, max_tokens=1024)
    assert client.model == "custom-model"
    assert client.api_base == "http://custom:8000/v1"
    assert client.temperature == 0.5
    assert client.max_tokens == 1024


def test_get_llm_client_returns_vllm_client():
    """Test get_llm_client returns vLLMClient instance."""
    client = get_llm_client()
    assert isinstance(client, vLLMClient)


def test_vlm_client_inheritance():
    """Test VLMClient inherits from vLLMClient."""
    client = VLMClient()
    assert isinstance(client, vLLMClient)
    assert client.model == "qwen-vl-plus"


def test_responses_api_maps_json_and_image_inputs():
    settings = SimpleNamespace(
        VLLM_MODEL="gpt-5.6-sol",
        llm_api_base="https://proxy.example/v1",
        llm_api_key="test-key",
        OPENAI_RESPONSES_URL="https://proxy.example/v1/responses",
        REPORT_PARSE_TIMEOUT_SECONDS=180,
    )
    response = MagicMock()
    response.json.return_value = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"metrics": []}'}],
            }
        ],
    }

    with (
        patch("app.model.llm.get_settings", return_value=settings),
        patch("app.model.llm.httpx.post", return_value=response) as post,
    ):
        client = VLMClient()
        assert client.chat_with_json([{"role": "user", "content": "extract"}], {"type": "object"}) == {"metrics": []}
        client.chat_with_image(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "extract"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AA=="},
                        },
                    ],
                }
            ]
        )

    text_request, image_request = [call.kwargs["json"] for call in post.call_args_list]
    assert post.call_args_list[0].args[0] == "https://proxy.example/v1/responses"
    assert post.call_args_list[0].kwargs["timeout"] == 180
    assert text_request["text"]["format"] == {"type": "json_object"}
    assert image_request["input"][0]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,AA==",
        "detail": "original",
    }
    assert image_request["store"] is False


def test_chat_with_image_uses_report_parse_timeout_for_chat_api():
    settings = SimpleNamespace(
        VLLM_MODEL="qwen-vl-plus",
        llm_api_base="https://proxy.example/v1",
        llm_api_key="test-key",
        OPENAI_RESPONSES_URL="",
        REPORT_PARSE_TIMEOUT_SECONDS=180,
    )
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"metrics": []}'))])
    openai_client = MagicMock()
    openai_client.chat.completions.create.return_value = response

    with (
        patch("app.model.llm.get_settings", return_value=settings),
        patch("openai.OpenAI", return_value=openai_client) as openai_factory,
    ):
        client = VLMClient()
        client.chat_with_image([{"role": "user", "content": [{"type": "text", "text": "extract"}]}])

    assert openai_factory.call_args.kwargs["timeout"] == 180
