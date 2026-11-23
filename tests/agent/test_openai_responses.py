"""OpenAIProvider on the Responses API, through the real SDK over a simulated httpx transport (no network)."""

from __future__ import annotations

import pytest

from asistente.nucleo.agent.providers import create_provider
from asistente.nucleo.agent.providers.base import Attachment, ProviderError, ToolResult, ToolSpec, Usage
from asistente.nucleo.agent.providers.openai_provider import OpenAIProvider, resolve_api_style
from asistente.plataforma import settings
from tests.agent.fakes_http import FakeOpenAIAPI, llamada_openai, mensaje_openai, respuesta_openai

TOOLS = [ToolSpec(name="buscar_fuentes", description="busca", input_schema={"type": "object", "properties": {}})]


@pytest.fixture(autouse=True)
def _sin_estilo_forzado(monkeypatch) -> None:
    monkeypatch.setattr(settings, "OPENAI_API_STYLE", "")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def _uso(entrada: int, salida: int, *, cacheados: int = 0, razonamiento: int = 0) -> dict:
    return {
        "input_tokens": entrada,
        "input_tokens_details": {"cached_tokens": cacheados},
        "output_tokens": salida,
        "output_tokens_details": {"reasoning_tokens": razonamiento},
        "total_tokens": entrada + salida,
    }


def test_text_turn_is_a_stateless_responses_request() -> None:
    api = FakeOpenAIAPI(respuestas=[respuesta_openai([mensaje_openai(" El tipo general es el 21 %. ")])])
    provider = OpenAIProvider(model="gpt-4o-mini", client=api.client())
    assert provider.api_style == "responses"

    turn = provider.start(
        system="SYS", tools=[], history=[{"role": "assistant", "content": "x"}], user_message="¿IVA?"
    ).step()

    assert turn.text == "El tipo general es el 21 %." and turn.stop_reason == "end" and turn.tool_calls == []
    assert turn.usage == {"input_tokens": 10, "output_tokens": 2}
    assert api.rutas == ["/v1/responses"]
    (cuerpo,) = api.cuerpos
    assert cuerpo["model"] == "gpt-4o-mini"
    assert cuerpo["instructions"] == "SYS"
    assert cuerpo["input"] == [{"role": "user", "content": "¿IVA?"}]  # leading assistant turn dropped
    assert cuerpo["store"] is False and "previous_response_id" not in cuerpo
    assert cuerpo["prompt_cache_key"].startswith("hgpt-")
    # gpt-4o-mini is not a reasoning model: neither reasoning nor encrypted items are asked for.
    assert "reasoning" not in cuerpo and "include" not in cuerpo and "tools" not in cuerpo


def test_tool_calls_round_trip_as_function_call_output_items_over_several_turns() -> None:
    razonamiento = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "cifrado"}
    api = FakeOpenAIAPI(
        respuestas=[
            respuesta_openai(
                [
                    razonamiento,
                    mensaje_openai("Voy a buscar."),
                    llamada_openai("c1", "buscar_fuentes", '{"consulta": "iva"}'),
                    llamada_openai("c2", "buscar_fuentes", "{consulta: roto"),
                ],
                usage=_uso(500, 120, cacheados=300, razonamiento=80),
            ),
            respuesta_openai([llamada_openai("c3", "buscar_fuentes", "[1]")]),
            respuesta_openai([mensaje_openai("El tipo general es el 21 % [F1].")]),
        ]
    )
    provider = OpenAIProvider(model="gpt-5.2", client=api.client(), effort="max")
    seen: list[Usage] = []
    provider.on_usage = seen.append
    conversation = provider.start(system="SYS", tools=TOOLS, history=[], user_message="¿IVA?")

    first = conversation.step()
    assert first.stop_reason == "tool_use" and first.text == "Voy a buscar."
    assert [(c.id, c.arguments, c.arguments_error) for c in first.tool_calls[:1]] == [("c1", {"consulta": "iva"}, None)]
    assert first.tool_calls[1].arguments == {} and "JSON" in first.tool_calls[1].arguments_error
    # Cached input split out; reasoning tokens already inside output_tokens.
    assert first.usage == {
        "input_tokens": 200,
        "output_tokens": 120,
        "cache_read_input_tokens": 300,
        "reasoning_tokens": 80,
    }
    assert seen == [Usage("openai", "gpt-5.2", input_tokens=200, output_tokens=120, cache_read_tokens=300)]

    conversation.add_tool_results(
        [ToolResult(call_id="c1", content="[F1] …"), ToolResult(call_id="c2", content="mal", is_error=True)]
    )
    second = conversation.step()
    assert second.tool_calls[0].arguments_error == "los argumentos deben ser un objeto JSON"
    conversation.add_tool_results([ToolResult(call_id="c3", content="mal", is_error=True)])
    third = conversation.step(allow_tools=False)
    assert third.stop_reason == "end" and third.text == "El tipo general es el 21 % [F1]."

    call_1, call_2, call_3 = api.cuerpos
    assert call_1["tools"] == [
        {
            "type": "function",
            "name": "buscar_fuentes",
            "description": "busca",
            "parameters": TOOLS[0].input_schema,
            "strict": False,
        }
    ]
    assert call_1["tool_choice"] == "auto" and call_3["tool_choice"] == "none"
    assert call_1["reasoning"] == {"effort": "xhigh"}  # the system's "max" is OpenAI's "xhigh"
    assert call_1["include"] == ["reasoning.encrypted_content"]
    # The whole conversation goes back each time, in order: reasoning replayed verbatim,
    # text, calls (arguments as sent, no server-side ids) and their outputs.
    assert call_2["input"] == [
        {"role": "user", "content": "¿IVA?"},
        razonamiento,
        {"role": "assistant", "content": "Voy a buscar."},
        {"type": "function_call", "call_id": "c1", "name": "buscar_fuentes", "arguments": '{"consulta": "iva"}'},
        {"type": "function_call", "call_id": "c2", "name": "buscar_fuentes", "arguments": "{consulta: roto"},
        {"type": "function_call_output", "call_id": "c1", "output": "[F1] …"},
        {"type": "function_call_output", "call_id": "c2", "output": "ERROR: mal"},
    ]
    assert call_3["input"][-2:] == [
        {"type": "function_call", "call_id": "c3", "name": "buscar_fuentes", "arguments": "[1]"},
        {"type": "function_call_output", "call_id": "c3", "output": "ERROR: mal"},
    ]


def test_truncation_wins_over_tool_calls_and_refusals_map_to_refusal() -> None:
    api = FakeOpenAIAPI(
        respuestas=[
            respuesta_openai(
                [llamada_openai("c1", "buscar_fuentes", '{"consulta": "i')],
                status="incomplete",
                incomplete="max_output_tokens",
            ),
            respuesta_openai([], status="incomplete", incomplete="content_filter"),
            respuesta_openai(
                [
                    {
                        "type": "message",
                        "id": "msg_r",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "No puedo ayudar con eso."}],
                    }
                ]
            ),
        ]
    )
    conversation = OpenAIProvider(model="gpt-4o-mini", client=api.client()).start(
        system="S", tools=TOOLS, history=[], user_message="x"
    )
    truncated = conversation.step()
    # A turn cut off mid tool call must surface as truncated, not as runnable tools.
    assert truncated.stop_reason == "max_tokens" and truncated.tool_calls[0].arguments_error is not None
    conversation.add_user_message("sigue")
    assert conversation.step().stop_reason == "refusal"
    conversation.add_user_message("otra")
    assert conversation.step().stop_reason == "refusal"


def test_streaming_forwards_text_deltas_and_reports_the_final_usage_once() -> None:
    final = respuesta_openai(
        [mensaje_openai("Voy a buscarlo."), llamada_openai("c1", "buscar_fuentes", "{}")],
        usage=_uso(40, 9, cacheados=8),
    )
    eventos = [
        {"type": "response.created", "sequence_number": 0, "response": {**final, "status": "in_progress"}},
        {
            "type": "response.output_text.delta",
            "sequence_number": 1,
            "item_id": "msg_fake",
            "output_index": 0,
            "content_index": 0,
            "delta": "Voy a ",
            "logprobs": [],
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 2,
            "item_id": "msg_fake",
            "output_index": 0,
            "content_index": 0,
            "delta": "buscarlo.",
            "logprobs": [],
        },
        {"type": "response.completed", "sequence_number": 3, "response": final},
    ]
    api = FakeOpenAIAPI(respuestas=[eventos])
    provider = OpenAIProvider(model="gpt-4o-mini", client=api.client())
    seen: list[Usage] = []
    provider.on_usage = seen.append
    chunks: list[str] = []

    turn = provider.start(system="S", tools=TOOLS, history=[], user_message="¿IVA?").step(on_text=chunks.append)

    assert chunks == ["Voy a ", "buscarlo."]
    assert turn.text == "Voy a buscarlo." and turn.stop_reason == "tool_use"
    assert [c.id for c in turn.tool_calls] == ["c1"]
    assert seen == [Usage("openai", "gpt-4o-mini", input_tokens=32, output_tokens=9, cache_read_tokens=8)]
    assert api.cuerpos[0]["stream"] is True


def test_a_failed_streamed_response_is_a_retryable_provider_error() -> None:
    fallida = respuesta_openai([], status="failed")
    fallida["error"] = {"code": "server_error", "message": "boom"}
    api = FakeOpenAIAPI(respuestas=[[{"type": "response.failed", "sequence_number": 0, "response": fallida}]])
    conversation = OpenAIProvider(model="gpt-4o-mini", client=api.client()).start(
        system="S", tools=[], history=[], user_message="x"
    )
    with pytest.raises(ProviderError) as excinfo:
        conversation.step(on_text=lambda chunk: None)
    assert excinfo.value.retryable and "boom" in str(excinfo.value)


def test_complete_json_uses_text_format_reads_pdfs_natively_and_degrades_on_a_rejected_schema() -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    api = FakeOpenAIAPI(
        respuestas=[
            respuesta_openai([mensaje_openai('{"a": 1}')]),
            400,
            respuesta_openai([mensaje_openai('Claro:\n```json\n{"b": 2}\n```')]),
        ]
    )
    provider = OpenAIProvider(model="gpt-4o-mini", client=api.client())

    data = provider.complete_json(
        system="S",
        user="U",
        schema=schema,
        schema_name="informe",
        attachments=[Attachment("application/pdf", b"%PDF-1.7 x", "a.pdf"), Attachment("image/png", b"png", "b.png")],
    )
    assert data == {"a": 1}
    assert provider.complete_json(system="S", user="U", schema={"type": "object"}, schema_name="x") == {"b": 2}

    strict, rejected, fallback = api.cuerpos
    assert strict["text"] == {"format": {"type": "json_schema", "name": "informe", "schema": schema, "strict": True}}
    assert strict["instructions"] == "S" and strict["store"] is False
    (user_item,) = strict["input"]
    assert user_item["content"] == [
        {"type": "input_text", "text": "U"},
        {"type": "input_file", "filename": "a.pdf", "file_data": "data:application/pdf;base64,JVBERi0xLjcgeA=="},
        {"type": "input_image", "image_url": "data:image/png;base64,cG5n"},
    ]
    assert "text" in rejected and "text" not in fallback
    assert "JSON Schema" in fallback["instructions"]


def test_local_servers_keep_chat_completions() -> None:
    local = FakeOpenAIAPI("Hola.")
    provider = OpenAIProvider(model="qwen3", client=local.client("http://127.0.0.1:11434/v1"), name="local")
    assert provider.api_style == "chat"
    turn = provider.start(system="S", tools=TOOLS, history=[], user_message="x").step()
    assert turn.text == "Hola." and turn.stop_reason == "end"
    assert local.rutas == ["/v1/chat/completions"]
    assert local.cuerpos[0]["messages"][0] == {"role": "system", "content": "S"}
    assert "store" not in local.cuerpos[0] and "prompt_cache_key" not in local.cuerpos[0]


def test_api_style_resolution(monkeypatch) -> None:
    assert resolve_api_style(name="openai") == "responses"
    assert resolve_api_style(name="local") == "chat"
    assert resolve_api_style(name="openai", base_url="http://localhost:1234/v1") == "chat"
    assert resolve_api_style(name="openai", base_url="https://api.openai.com/v1") == "responses"
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8080/v1")  # mlx_lm.server, vLLM…
    assert resolve_api_style(name="openai") == "chat"
    assert resolve_api_style(name="local", api_style="responses") == "responses"  # an explicit setting wins
    with pytest.raises(ValueError, match="OPENAI_API_STYLE"):
        resolve_api_style(name="openai", api_style="grpc")
    # The factory passes OPENAI_API_STYLE down to the provider.
    monkeypatch.setattr(settings, "OPENAI_API_STYLE", "chat")
    assert create_provider("openai", model="gpt-4o-mini").api_style == "chat"  # type: ignore[attr-defined]


def test_effort_reaches_reasoning_models_only_and_routing_variants_keep_the_style() -> None:
    api = FakeOpenAIAPI("ok")
    base = create_provider("openai", model="gpt-4o-mini", effort="low")
    assert base.effort == "low"  # type: ignore[attr-defined]
    provider = OpenAIProvider(model="gpt-4o-mini", client=api.client(), effort="low")
    provider.start(system="S", tools=[], history=[], user_message="x").step()
    razonador = provider.con_modelo("o4-mini")
    assert razonador.effort == "low" and razonador.api_style == "responses"
    assert provider.con_modelo("o4-mini") is razonador
    razonador.start(system="S", tools=[], history=[], user_message="x").step()
    provider.con_modelo("gpt-5.2", effort="medium").start(system="S", tools=[], history=[], user_message="x").step()
    plano, o4, gpt5 = api.cuerpos
    assert "reasoning" not in plano
    assert (o4["model"], o4["reasoning"]) == ("o4-mini", {"effort": "low"})
    assert (gpt5["model"], gpt5["reasoning"]) == ("gpt-5.2", {"effort": "medium"})
