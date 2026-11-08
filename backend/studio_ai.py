"""
EL ASISTENTE DEL TABLERO (FASE 16, rama `paypal-contest`, 6-nov): el puente entre el Agent Framework de AG Studio y
Nemotron en Nebius Token Factory.

AG Studio corre el agente en el NAVEGADOR (sus herramientas leen y filtran el tablero ahí) y le pide a un "adaptador" cada
turno del modelo. El adaptador del portal manda aquí el turno (`AgLlmRequest`: la conversación, las instrucciones y las
herramientas) y esta ruta lo traduce al formato de chat de Nebius, llama a Nemotron y devuelve la respuesta como la
espera AG Studio (`AgLlmResponse`: texto y llamadas a herramientas). Así la llave de Nebius nunca llega al navegador.

Lo que NO hace, a propósito (reglas de oro):
- No ejecuta herramientas: las ejecuta AG Studio en el navegador, y todas son de LECTURA (ver el tablero, consultar sus
  datos, filtrarlo) o abren Cobros. Ninguna envía, cobra ni cambia el libro; el recordatorio lo manda el dueño.
- No calcula: las cifras salen de las consultas del motor de AG Studio sobre datos que ya calculó el código (SQL).
Cada turno se cuenta en `agentcall` (tope diario de la oficina) con su tiempo y su costo reales.
"""

import json
import re
import time
import uuid
from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

import agent_calls
import demo
import nemotron
from auth import current_user
from db import get_session
from invoices import _owner_only
from models import Company, User

router = APIRouter()

AGENT = "studio_assistant"
MAX_ITEMS = 80          # turnos de conversación que se aceptan por petición
MAX_CHARS = 120_000     # tope del pedido completo (instrucciones + conversación + herramientas)
MAX_TOKENS = 1500
# 8-nov: con las consultas de AG Studio (`execute_query`, esquema grande y anidado) fallaron Nano (8 turnos, 105k tokens) y
# Super (10 turnos, 130k tokens): el asistente quedó con herramientas propias y simples, y con Nano basta. STUDIO_AI_MODEL
# permite cambiarlo.
def _model() -> str:
    import os
    return os.getenv("STUDIO_AI_MODEL") or nemotron.model_name()
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class StudioTurn(BaseModel):
    instructions: Optional[str] = None
    input: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    toolChoice: Optional[Union[str, dict[str, Any]]] = None


def _text_of(content: Any) -> str:
    """El texto de un mensaje de AG Studio (lista de partes `{type: 'text', text}`) o una cadena."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or p.get("refusal") or "") for p in content if isinstance(p, dict))
    return ""


def to_messages(instructions: Optional[str], items: list[dict]) -> list[dict]:
    """La conversación de AG Studio → mensajes de chat (OpenAI/Nebius). Las llamadas seguidas a herramientas van en UN
    mensaje del asistente, y cada resultado, en un mensaje `tool` con su `tool_call_id`. El razonamiento se omite."""
    msgs: list[dict] = []
    if instructions:
        msgs.append({"role": "system", "content": instructions})
    for it in items:
        kind = it.get("type")
        if kind == "message":
            role = it.get("role")
            text = _text_of(it.get("content"))
            if role in ("user", "system"):
                msgs.append({"role": role, "content": text})
            elif role == "assistant" and text:
                msgs.append({"role": "assistant", "content": text})
        elif kind == "function_call":
            call = {"id": it.get("callId") or it.get("id"), "type": "function",
                    "function": {"name": it.get("name"), "arguments": it.get("arguments") or "{}"}}
            last = msgs[-1] if msgs else None
            if last and last.get("role") == "assistant" and last.get("tool_calls") is not None and not last.get("content"):
                last["tool_calls"].append(call)
            else:
                msgs.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            msgs.append({"role": "tool", "tool_call_id": it.get("callId"), "content": str(it.get("output") or "")})
    return msgs


def to_tools(tools: list[dict]) -> list[dict]:
    """Las herramientas que declara AG Studio → `tools` de chat. Las de tipo `provided` (del proveedor) no aplican."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description") or "",
                                              "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
            for t in tools if t.get("name") and t.get("kind") != "provided"]


def to_tool_choice(choice: Union[str, dict, None]) -> Union[str, dict, None]:
    if isinstance(choice, dict) and choice.get("name"):
        return {"type": "function", "function": {"name": choice["name"]}}
    return choice if choice in ("auto", "none", "required") else None


def to_output(message: dict) -> list[dict]:
    """La respuesta de Nemotron → los `AgAiOutputItem` que espera AG Studio (texto y llamadas a herramientas)."""
    out: list[dict] = []
    text = _THINK_RE.sub("", message.get("content") or "").strip()
    if text:
        out.append({"id": f"msg_{uuid.uuid4().hex[:12]}", "kind": "output", "type": "message", "role": "assistant",
                    "status": "completed", "content": [{"type": "text", "text": text, "annotations": []}]})
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
        out.append({"id": f"fc_{uuid.uuid4().hex[:12]}", "kind": "output", "type": "function_call", "callId": call_id,
                    "name": fn.get("name") or "", "arguments": fn.get("arguments") or "{}", "status": "completed"})
    return out


@router.post("/api/owner/companies/{company_id}/studio-ai")
def studio_turn(company_id: int, payload: StudioTurn, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Un turno del asistente del Tablero (solo el dueño; en la visita guiada no hay IA)."""
    company: Company = _owner_only(company_id, user, session)
    if company.firm_id in demo._tour_ids(session):
        raise HTTPException(status_code=403, detail="tour_read_only")
    if not payload.input:
        raise HTTPException(status_code=422, detail="input_required")
    if len(payload.input) > MAX_ITEMS or len(json.dumps(payload.model_dump())) > MAX_CHARS:
        raise HTTPException(status_code=413, detail="too_long")
    try:
        agent_calls.check_cap(session, company)
    except agent_calls.CapReached:
        raise HTTPException(status_code=429, detail="ai_cap_reached")

    body: dict[str, Any] = {"model": _model(), "messages": to_messages(payload.instructions, payload.input),
                            "temperature": 0, "max_tokens": MAX_TOKENS,
                            "chat_template_kwargs": {"enable_thinking": False}}
    tools = to_tools(payload.tools)
    if tools:
        body["tools"] = tools
        choice = to_tool_choice(payload.toolChoice)
        if choice:
            body["tool_choice"] = choice
    started = time.monotonic()
    try:
        r = nemotron._post(body)
    except nemotron.NemotronUnavailable:
        raise HTTPException(status_code=503, detail="ai_unavailable")
    except nemotron.NemotronError as e:
        agent_calls.record(session, company, AGENT, None, outcome=e.code, user_id=user.id, model=_model(),
                           latency_ms=int((time.monotonic() - started) * 1000))
        session.commit()
        raise HTTPException(status_code=502, detail="ai_failed")
    if r.status_code >= 400:
        agent_calls.record(session, company, AGENT, None, outcome=f"http_{r.status_code}", user_id=user.id,
                           model=_model(), latency_ms=int((time.monotonic() - started) * 1000))
        session.commit()
        raise HTTPException(status_code=502, detail="ai_failed")
    data = r.json()
    usage = nemotron._usage_from(data, _model(), "tools", False, started)
    message = ((data.get("choices") or [{}])[0].get("message")) or {}
    output = to_output(message)
    agent_calls.record(session, company, AGENT, usage, user_id=user.id, items_out=len(output),
                       items_valid=sum(1 for o in output if o["type"] == "function_call"),
                       outcome="ok" if usage.finish_reason != "length" else "out_of_tokens")
    session.commit()
    return {
        "id": data.get("id") or f"resp_{uuid.uuid4().hex[:12]}", "createdAt": int(time.time() * 1000),
        "status": "incomplete" if usage.finish_reason == "length" else "completed",
        **({"incompleteDetails": {"reason": "max_output_tokens"}} if usage.finish_reason == "length" else {}),
        "output": output, "model": usage.model,
        # Lo medido de ESTE turno, para mostrarlo junto a la respuesta (como el panel "What the AI did").
        "contabu": {"latency_ms": usage.latency_ms, "tokens": usage.prompt_tokens + usage.completion_tokens,
                    "cost_usd": usage.cost_usd},
    }
