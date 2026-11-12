"""
EL ASISTENTE DEL TABLERO (FASE 16, rama `paypal-contest`, 6-nov): el puente entre el Agent Framework de AG Studio y
el modelo en Nebius Token Factory (Qwen3 235B, `DEFAULT_MODEL`, elegido entre siete el 8-nov).

AG Studio corre el agente en el NAVEGADOR (sus herramientas leen y filtran el tablero ahí) y le pide a un "adaptador" cada
turno del modelo. El adaptador del portal manda aquí el turno (`AgLlmRequest`: la conversación, las instrucciones y las
herramientas) y esta ruta lo traduce al formato de chat de Nebius, llama al modelo y devuelve la respuesta como la
espera AG Studio (`AgLlmResponse`: texto y llamadas a herramientas). Así la llave de Nebius nunca llega al navegador.

Lo que NO hace, a propósito (reglas de oro):
- No ejecuta acciones: las herramientas de AG Studio corren en el navegador y las que devuelven cifras
  (`studio_tools.py`) corren aquí en SQL; todas son de LECTURA (ver el tablero, consultar sus datos, filtrarlo) o abren
  Cobros. Ninguna envía, cobra ni cambia el libro; el recordatorio lo manda el dueño.
- No calcula: las cifras salen de SQL, y antes de devolver la respuesta el código busca cada monto en esos resultados.
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
import studio_tools
from auth import current_user
from db import get_session
from invoices import _owner_only
from models import Company, User

router = APIRouter()

AGENT = agent_calls.STUDIO_AGENT
MAX_ITEMS = 80          # turnos de conversación que se aceptan por petición
MAX_CHARS = 120_000     # tope del pedido completo (instrucciones + conversación + herramientas)
MAX_TOKENS = 1500
MAX_SERVER_LOOPS = 4    # vueltas del ciclo en el servidor por cada llamada del navegador
# 8-nov: con las consultas de AG Studio (`execute_query`, esquema grande y anidado) fallaron Nano (8 turnos, 105k tokens) y
# Super (10 turnos, 130k tokens). Para comparar modelos, el chat puede pedir uno de esta lista CERRADA (✅ catálogo de Nebius
# Token Factory, 8-nov, con precio por millón de tokens: entrada, salida); cualquier otro se ignora y se usa el de siempre.
STUDIO_MODELS: dict[str, tuple[float, float]] = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "Qwen/Qwen3-235B-A22B-Instruct-2507": (0.20, 0.60),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "deepseek-ai/DeepSeek-V4-Flash-0731": (0.14, 0.28),
    "zai-org/GLM-5.3-Flash": (0.15, 0.50),
}
# 8-nov (Jose aprobó): Qwen3 235B por defecto (✅ licencia apache-2.0 en su ficha de Hugging Face): el único que filtró y
# consultó el tablero sin un intento fallido. DeepSeek V4 Flash (mit) y GLM 5.3 Flash quedan en el selector.
DEFAULT_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"
for _m, _p in STUDIO_MODELS.items():          # para que `Usage.cost_usd` sepa el precio de cada uno
    nemotron.PRICES_PER_MTOK.setdefault(_m, _p)


def _model(requested: Optional[dict] = None) -> str:
    import os
    want = (requested or {}).get("id")
    if want in STUDIO_MODELS:
        return want
    return os.getenv("STUDIO_AI_MODEL") or DEFAULT_MODEL


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


# EL CÓDIGO REVISA CADA CIFRA (8-nov): la IA no calcula. En la respuesta final, cada monto que escribe el modelo tiene que
# aparecer, tal cual, en lo que devolvieron las herramientas en esta conversación. Si alguno no aparece (lo inventó o lo
# sumó él), la respuesta NO se muestra: se reemplaza por un aviso con las cifras que no se pudieron verificar.
# Montos: con "$", con separador de miles, con decimales, o enteros de 4+ cifras que no son un año ("5810 USD"; ✅ 8-nov se
# colaba sin "$" ni coma).
# 8-nov (revisión como cliente): en español el modelo escribió "2.330" (punto de miles, coma decimal) y la revisión lo leyó
# como 2.33 y ocultó una respuesta correcta. Ese formato se captura entero ("2.330,50") y se prueba de las dos maneras.
_EURO_RE = re.compile(r"^\$?\s?-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$")
# 12-nov (revisión como juez): el modelo escribió "$2.330.00", punto de miles Y punto decimal. Se lee como un monto entero
# (antes la revisión tomaba "$2.330" y dejaba ".00" suelto) y, ya verificado, se reescribe como "$2,330.00" (`us_money`).
_MIXED_RE = re.compile(r"\$?\s?-?\d{1,3}(?:\.\d{3})+\.\d{2}(?![\d.])")
_AMOUNT_RE = re.compile(r"(\$?\s?-?\d{1,3}(?:\.\d{3})+\.\d{2}(?![\d.])|\$\s?-?\d{1,3}(?:\.\d{3})+,\d{1,2}|-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?(?![\d.])|\$\s?-?\d[\d,]*(?:\.\d+)?|-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|(?<![\d-])(?!0)(?!(?:19|20)\d{2}\b)\d{4,}(?![\d-]))")
_ANY_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _num(text: str) -> Optional[float]:
    if _MIXED_RE.fullmatch(text.strip()):
        whole, cents = text.replace("$", "").strip().rsplit(".", 1)
        return round(float(whole.replace(".", "") + "." + cents), 2)
    try:
        return round(float(text.replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return _euro(text)


def amounts_in(text: str) -> list[tuple[str, float]]:
    """Los montos de un texto: con "$", con separador de miles o con decimales. Los enteros sueltos (cantidades, días,
    años) no cuentan como montos."""
    out = []
    for m in _AMOUNT_RE.finditer(text or ""):
        v = _num(m.group(0))
        if v is not None:
            out.append((m.group(0).strip(), v))
    return out


def tool_numbers(items: list[dict]) -> set[float]:
    """Cada número que devolvió una herramienta en esta conversación (sin las comas de miles)."""
    nums: set[float] = set()
    for it in items:
        text = it.get("output") if it.get("type") == "function_call_output" else it.get("result")
        if it.get("type") in ("function_call_output", "function_call") and text is not None:
            for m in _ANY_NUM_RE.finditer(str(text).replace(",", "")):
                try:
                    nums.add(round(float(m.group(0)), 2))
                except ValueError:
                    pass
    return nums


def _euro(raw: str) -> Optional[float]:
    """"2.330" o "$2.330,50" leídos con punto de miles y coma decimal (como los escribe el modelo en español)."""
    if not _EURO_RE.match(raw.strip()):
        return None
    try:
        return round(float(raw.replace("$", "").strip().replace(".", "").replace(",", ".")), 2)
    except ValueError:
        return None


def us_money(text: str) -> str:
    """Los montos con "$" en formato de EE. UU. ($2,330.00), como pide la instrucción del asistente, también en español:
    el mezclado ("$2.330.00") y el europeo con decimales ("$2.330,50"). Se aplica a una respuesta YA verificada."""
    def fix(read):
        def one(m: re.Match) -> str:
            v = read(m.group(0))
            return m.group(0) if v is None else ("-" if v < 0 else "") + f"${abs(v):,.2f}"
        return one
    text = re.sub(r"\$\s?-?\d{1,3}(?:\.\d{3})+\.\d{2}(?![\d.])", fix(_num), text)
    return re.sub(r"\$\s?-?\d{1,3}(?:\.\d{3})+,\d{2}(?!\d)", fix(_euro), text)


def unverified_amounts(text: str, items: list[dict]) -> list[str]:
    known = tool_numbers(items)
    bad = []
    for raw, v in amounts_in(text):
        alt = _euro(raw)
        if any(x is not None and (x in known or -x in known) for x in (v, alt)):
            continue
        bad.append(raw)
    return bad


_TOO_MANY = {
    "es": "No pude terminar esa respuesta en los pasos permitidos. Pregúntame algo más concreto.",
    "en": "I couldn't finish that answer within the allowed steps. Try asking something narrower.",
}
_UNVERIFIED = {
    "es": "No pude verificar estas cifras con tus datos ({figures}), así que no te muestro esa respuesta. "
          "Pregúntame de otra forma o mira el tablero.",
    "en": "I couldn't verify these figures against your data ({figures}), so I won't show that answer. "
          "Try asking another way or check the dashboard.",
}


class StudioTurn(BaseModel):
    instructions: Optional[str] = None
    input: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    toolChoice: Optional[Union[str, dict[str, Any]]] = None
    model: Optional[dict[str, Any]] = None      # `{id}` del selector de modelos del chat (solo de STUDIO_MODELS)
    lang: str = "en"                            # el de la pantalla: solo manda si la pregunta no deja ver su idioma


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
            if it.get("result") is not None:          # herramienta del SERVIDOR (8-nov): su resultado viaja en la llamada
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": str(it["result"])})
        elif kind == "function_call_output":
            msgs.append({"role": "tool", "tool_call_id": it.get("callId"), "content": str(it.get("output") or "")})
    return msgs


def to_tools(tools: list[dict]) -> list[dict]:
    """Las herramientas que declara AG Studio → `tools` de chat. Las de tipo `provided` (del proveedor) no aplican."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description") or "",
                                              "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
            for t in tools if t.get("name") and t.get("kind") != "provided"]


def _new_question(items: list[dict]) -> bool:
    """¿El último elemento es un mensaje del dueño (y no el resultado de una herramienta del navegador)?"""
    last = items[-1] if items else {}
    return last.get("role") == "user" and last.get("type", "message") == "message"


# 12-nov (Jose): el asistente responde en el idioma de la PREGUNTA, no en el de la pantalla. La instrucción sola no bastó
# (pantalla en español + "How much did PayPal charge me in fees?" → respondió en español), así que el CÓDIGO decide el
# idioma de la última pregunta y se lo dice al modelo; si no se distingue ("Maple Grove"), manda el de la pantalla.
_ES_WORDS = {"que", "cuanto", "cuantos", "quien", "quienes", "cual", "cuales", "como", "donde", "cuando", "mi", "mis",
             "te", "de", "del", "la", "las", "los", "el", "en", "por", "para", "con", "es", "son", "fue", "y", "hay",
             "esta", "estan", "debe", "deben", "pagan", "gaste", "gane", "mes", "meses", "factura", "facturas", "cliente",
             "clientes", "comisiones", "cobro", "recuerdo", "ingresos", "gastos", "mejor", "dinero"}
_EN_WORDS = {"what", "how", "who", "which", "where", "when", "much", "many", "my", "i", "the", "of", "in", "on", "for",
             "to", "is", "are", "was", "did", "do", "does", "with", "and", "pay", "paid", "owe", "owes", "month",
             "months", "invoice", "invoices", "customer", "customers", "fees", "spend", "spent", "earn", "earned",
             "remind", "best", "money", "income", "costs"}
_ES_MARKS = re.compile(r"[¿¡ñáéíóú]", re.IGNORECASE)
_PLAIN = str.maketrans("áéíóúü", "aeiouu")


def question_lang(items: list[dict]) -> Optional[str]:
    """El idioma ("es" o "en") de la última pregunta del dueño, o None si no se distingue."""
    text = next((_text_of(it.get("content")) for it in reversed(items)
                 if it.get("role") == "user" and it.get("type", "message") == "message"), "")
    if _ES_MARKS.search(text):
        return "es"
    words = re.findall(r"[a-zñ]+", text.lower().translate(_PLAIN))
    es, en = sum(w in _ES_WORDS for w in words), sum(w in _EN_WORDS for w in words)
    return "es" if es > en else "en" if en > es else None


_REPLY_IN = {"es": "\n- The owner's last question is in Spanish: reply in Spanish.",
             "en": "\n- The owner's last question is in English: reply in English."}


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
        agent_calls.check_cap(session, company, AGENT)   # 8-nov: su tope propio, y el del demo entero
    except agent_calls.CapReached:
        raise HTTPException(status_code=429, detail="ai_cap_reached")

    model = _model(payload.model)
    lang = question_lang(payload.input) or ("es" if payload.lang.startswith("es") else "en")
    messages = to_messages((payload.instructions or "") + _REPLY_IN[lang], payload.input)
    tools = to_tools(payload.tools)
    choice = to_tool_choice(payload.toolChoice)

    # EL CICLO EN EL SERVIDOR (8-nov): si el modelo pide herramientas del SERVIDOR (SQL, `studio_tools`), este backend las
    # ejecuta y vuelve a llamar al modelo, hasta que responda o pida una herramienta del navegador (las de AG Studio o abrir
    # Cobros). Cada paso queda medido y registrado; el número de vueltas tiene tope aquí, no en el navegador.
    output: list[dict] = []
    steps: list[dict] = []
    server_results: list[dict] = []
    total = {"latency_ms": 0, "tokens": 0, "cost_usd": 0.0}
    usage = None
    data: dict = {}
    for loop in range(MAX_SERVER_LOOPS):
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0, "max_tokens": MAX_TOKENS}
        if model.startswith("nvidia/"):                   # el razonamiento apagado (✅ así se usa Nemotron en todo ContaBu)
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"] = tools
            if choice and loop == 0:
                body["tool_choice"] = choice
            elif loop == 0 and _new_question(payload.input):
                # 8-nov (juego de 10 preguntas, 2ª vuelta): con la conversación avanzada el modelo respondió sin llamar
                # ninguna herramienta e inventó cifras (la revisión las ocultó). Ante una pregunta nueva, primero consulta.
                body["tool_choice"] = "required"
        started = time.monotonic()
        try:
            r = nemotron._post(body)
        except nemotron.NemotronUnavailable:
            raise HTTPException(status_code=503, detail="ai_unavailable")
        except nemotron.NemotronError as e:
            agent_calls.record(session, company, AGENT, None, outcome=e.code, user_id=user.id, model=model,
                               latency_ms=int((time.monotonic() - started) * 1000))
            session.commit()
            raise HTTPException(status_code=502, detail="ai_failed")
        if r.status_code >= 400:
            agent_calls.record(session, company, AGENT, None, outcome=f"http_{r.status_code}", user_id=user.id,
                               model=model, latency_ms=int((time.monotonic() - started) * 1000))
            session.commit()
            raise HTTPException(status_code=502, detail="ai_failed")
        data = r.json()
        usage = nemotron._usage_from(data, model, "tools", False, started)
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        turn_out = to_output(message)
        calls = [o for o in turn_out if o["type"] == "function_call"]
        agent_calls.record(session, company, AGENT, usage, user_id=user.id, items_out=len(turn_out), items_valid=len(calls),
                           outcome="ok" if usage.finish_reason != "length" else "out_of_tokens")
        tok = usage.prompt_tokens + usage.completion_tokens
        total["latency_ms"] += usage.latency_ms
        total["tokens"] += tok
        total["cost_usd"] += usage.cost_usd or 0
        steps.append({"kind": "model", "model": usage.model, "ms": usage.latency_ms, "tokens": tok, "cost_usd": usage.cost_usd,
                      "tools": [c["name"] for c in calls]})
        server_calls = [c for c in calls if c["name"] in studio_tools.SERVER_TOOLS]
        if not server_calls:
            output += turn_out
            break
        # Las herramientas del servidor: SQL aquí mismo. Su resultado va en la llamada (`result`), para el chat de AG Studio
        # y para la revisión de cifras.
        messages.append({"role": "assistant", "content": message.get("content"),
                         "tool_calls": [{"id": c["callId"], "type": "function",
                                         "function": {"name": c["name"], "arguments": c["arguments"]}} for c in server_calls]})
        for c in server_calls:
            t0 = time.monotonic()
            result = json.dumps(studio_tools.SERVER_TOOLS[c["name"]](session, company))
            ms = int((time.monotonic() - t0) * 1000)
            c["result"] = result
            # No va en `output`: con una llamada `server`, AG Studio le da un resultado de relleno y vuelve a llamar al modelo
            # (✅ `executeToolCall.d.ts`). Se muestra en "Lo que hizo la IA" (`steps`) y cuenta para la revisión de cifras.
            server_results.append(c)
            messages.append({"role": "tool", "tool_call_id": c["callId"], "content": result})
            steps.append({"kind": "sql", "tool": c["name"], "ms": ms})
        client_calls = [c for c in calls if c["name"] not in studio_tools.SERVER_TOOLS]
        if client_calls:                                   # lo del navegador lo ejecuta el navegador
            output += client_calls
            break
    else:
        output.append({"id": f"msg_{uuid.uuid4().hex[:12]}", "kind": "output", "type": "message", "role": "assistant",
                       "status": "completed", "content": [{"type": "text", "text": _TOO_MANY[lang], "annotations": []}]})

    final = not any(o["type"] == "function_call" for o in output)
    unverified: list[str] = []
    if final:
        for o in output:
            if o["type"] == "message":
                text = o["content"][0]["text"]
                bad = unverified_amounts(text, payload.input + server_results)
                if bad:
                    unverified += bad
                    o["content"][0]["text"] = _UNVERIFIED[lang].format(figures=", ".join(bad))
                else:
                    o["content"][0]["text"] = us_money(text)
        steps.append({"kind": "check", "figures": sum(len(amounts_in(o["content"][0]["text"])) for o in output
                                                      if o["type"] == "message") if not unverified else 0,
                      "unverified": unverified})
        if unverified:
            agent_calls.record(session, company, AGENT, None, outcome="unverified", user_id=user.id, model=model)
    session.commit()
    length = bool(usage and usage.finish_reason == "length")
    return {
        "id": data.get("id") or f"resp_{uuid.uuid4().hex[:12]}", "createdAt": int(time.time() * 1000),
        "status": "incomplete" if length else "completed",
        **({"incompleteDetails": {"reason": "max_output_tokens"}} if length else {}),
        "output": output, "model": model,
        # Lo medido de ESTA llamada (todas sus vueltas), para "Lo que hizo la IA": cada paso con su tiempo.
        "contabu": {"latency_ms": total["latency_ms"], "tokens": total["tokens"], "cost_usd": total["cost_usd"],
                    "model": model, "final": final, "steps": steps,
                    "verified": (not unverified) if final else None, "unverified": unverified},
    }
