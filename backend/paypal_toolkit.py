"""
EL PAYPAL AGENT TOOLKIT EN CONTABU (FASE 16, área PayPal / portal del dueño · 5-nov-2026).

`paypal-agent-toolkit` (oficial de PayPal, Apache-2.0, github.com/paypal/agent-toolkit) convierte las APIs de PayPal en
herramientas para un modelo de IA. ContaBu usa **sus definiciones** (nombre, descripción y esquema de cada herramienta) y
**su modelo de permisos** (`Configuration.actions`), nunca sus funciones que ejecutan:

- ✅ Parte 0 (5-nov, paquete 1.1.0 leído): el `create_invoice` del Toolkit **ENVÍA la factura solo** después de crearla
  (`send_invoice` con `send_to_recipient=True`), aunque su descripción dice que "can be sent". Eso rompe la regla de oro.
- Por eso: la IA solo puede recibir lo que crea un BORRADOR o lee (`AI_ACTIONS`); enviar, recordar, cancelar y el QR son
  del código, cuando el dueño toca el botón (`CODE_ONLY`). Una prueba (`check_voice.py`) falla si eso cambia.
- El borrador del dictado se arma como los argumentos de `create_invoice` y se VALIDA con el esquema oficial
  (`CreateInvoiceParameters`); lo crea `invoices.create_invoice`, que no envía. El esquema del Toolkit no tiene fecha de
  vencimiento: la pone el código (`voice_invoice.resolve_due`).

Licencia: Apache-2.0, compatible con la MPL-2.0 de ContaBu; se usa como dependencia sin copiar su código.
"""

from importlib.metadata import PackageNotFoundError, version

from paypal_agent_toolkit.shared.configuration import Configuration, is_tool_allowed
from paypal_agent_toolkit.shared.invoices.parameters import CreateInvoiceParameters
from paypal_agent_toolkit.shared.tools import tools as TOOLKIT_TOOLS

try:
    TOOLKIT_VERSION = version("paypal-agent-toolkit")
except PackageNotFoundError:   # pragma: no cover — instalado por requirements.txt
    TOOLKIT_VERSION = "?"

# Lo único que la IA puede recibir: crear (un BORRADOR, porque ContaBu no usa el handler que envía) y leer.
AI_ACTIONS = Configuration(actions={"invoices": {"create": True, "get": True, "list": True}})
# Lo que solo llama el código tras la confirmación del dueño (`invoices.send_invoice`, `invoice_reminders`, cancelar, QR).
CODE_ONLY = frozenset({"send_invoice", "send_invoice_reminder", "cancel_sent_invoice", "generate_invoice_qr_code"})


def ai_tools() -> list[str]:
    """Las herramientas del Toolkit que la configuración deja ofrecer a la IA."""
    return [t["method"] for t in TOOLKIT_TOOLS if is_tool_allowed(t, AI_ACTIONS)]


def openai_spec(method: str) -> dict:
    """La definición oficial de una herramienta, en el formato de herramientas de OpenAI (el que entiende Nemotron).
    Solo para las que la IA puede recibir."""
    if method not in ai_tools():
        raise ValueError(f"{method} is not offered to the AI")
    t = next(t for t in TOOLKIT_TOOLS if t["method"] == method)
    return {"type": "function", "function": {"name": method, "description": t["description"],
                                             "parameters": t["args_schema"].model_json_schema()}}


def draft_call(*, customer_name: str, customer_email: str, concept: str, amount: str, business_name: str | None) -> dict:
    """Los argumentos de `create_invoice` para el borrador del dictado, validados con el esquema OFICIAL del Toolkit.
    El cliente y su correo salen de la lista guardada (nunca del dictado ni de la IA); el monto y el concepto, de lo que
    el código ya validó. Error del esquema → ValueError (no se crea nada)."""
    args = {
        "detail": {"currency_code": "USD"},
        "primary_recipients": [{"billing_info": {"name": {"given_name": customer_name}, "email_address": customer_email}}],
        "items": [{"name": concept, "quantity": "1", "unit_amount": {"currency_code": "USD", "value": amount}}],
    }
    if business_name:
        args["invoicer"] = {"business_name": business_name[:300]}
    CreateInvoiceParameters.model_validate(args)
    return {"tool": "create_invoice", "toolkit": f"paypal-agent-toolkit {TOOLKIT_VERSION}", "mode": "draft",
            "arguments": args}
