import { createAiHarness, directLlmRunner, type AgAiEvent, type AgAiHarnessSetup, type AgAiOutputItem, type AgAiTool,
         type AgLlmAdapter, type AgLlmResponse } from "ag-studio";
import { getInvoices, studioTurn, type StudioTurnCost } from "../../api";
const OWNER_VIEW_EVENT = "contabu:owner-view";   // el mismo de OwnerApp (sin importarlo: evita un ciclo)

// EL ASISTENTE DEL TABLERO (FASE 16, rama `paypal-contest`, 6-nov) con el Agent Framework de AG Studio.
// - El agente corre en el navegador; cada turno del modelo va a NUESTRO backend (`/studio-ai`), que llama a Nemotron en
//   Nebius: la llave nunca llega aquí.
// - Sus herramientas solo LEEN (el esquema, el tablero, consultas del motor de AG Studio, filtros de la página) más dos
//   nuestras: `reminder_candidates` (a quién recordarle, ordenado y con el tono que sugiere el CÓDIGO) y
//   `open_collections` (abre Cobros). Ninguna envía nada: el recordatorio lo revisa y lo manda el dueño.
// - La IA no calcula: cada cifra sale de una herramienta.

const INSTRUCTIONS = (lang: string) => `You are the collections assistant of a small business that gets paid with PayPal. You live inside its dashboard in ContaBu and answer questions about its invoices, its payments to contractors and the periods its bookkeeper closed.
Rules:
- Never calculate, estimate, compare or guess a number. The code's tools already give totals, rankings and the highest and lowest values: read them from there. Every figure you give must come from a tool result, copied exactly as the tool returned it. Do not add, subtract or compute percentages yourself: if you need a total, run execute_query with only that measure and the filter you need (for example the month), without grouping by category, so the query returns the total. The code checks every figure you write against the tool results and hides answers with figures it cannot find.
- Prefer the code's tools: customer_totals (customers), contractor_totals (contractors), month_totals (anything about months), category_totals (categories, biggest expense), reminder_candidates (who to remind). Only if none of them fits, call view_schema and then execute_query.
- If no tool can answer the question, say that you can't answer it from the dashboard data. Never answer a different question.
- You cannot send, create, cancel or change anything. Never say you did.
- To help collect: call reminder_candidates and recommend who to remind first, using the order and the suggested tone it returns (the code decides them from each customer's payment history). Tell the owner to press Remind next to that customer in "Who owes me". Call open_collections ONLY if the owner asks to send the reminder or to open the invoices: it leaves the dashboard.
- To show the owner something on the dashboard, call view_schema and then add_page_filter with the exact field id and values; remove_page_filter clears it.
- Keep answers short: two to five sentences or a short list. Use the customer names and invoice numbers from the tools.
- Always reply in ${lang === "es" ? "Spanish" : "English"}, even if the tools return English.
- Call the tools first, then answer once. Do not narrate what you are about to do.`;

const id = (p: string) => `${p}_${Math.random().toString(36).slice(2, 10)}`;

/** El adaptador: un turno de AG Studio → `/studio-ai` → la respuesta y sus eventos (sin streaming: llega entera). */
export function contabuAdapter(onTurn: (c: StudioTurnCost) => void, lang: string): AgLlmAdapter {
  return {
    executeTurn(request, options) {
      const done: Promise<AgLlmResponse> = studioTurn({
        instructions: request.instructions, input: request.input, tools: request.tools,
        toolChoice: request.toolChoice, model: request.model, lang,
      }, options?.signal).then((r) => { onTurn(r.contabu); return r as unknown as AgLlmResponse; })
        .catch((e: Error) => ({ id: id("resp"), createdAt: Date.now(), status: "failed" as const, output: [],
                                error: { code: e.message || "ai_failed", message: e.message || "ai_failed" } }));
      async function* events(): AsyncGenerator<AgAiEvent> {
        const r = await done;
        for (const o of r.output as AgAiOutputItem[]) {
          if (o.type === "message") {
            const text = o.content.map((c) => (c.type === "text" ? c.text : c.refusal)).join("");
            yield { type: "TEXT_MESSAGE_START", messageId: o.id, role: "assistant" };
            // 8-nov: el texto aparece por partes. No es el streaming del modelo: el backend revisa cada cifra ANTES de
            // mostrar nada, así que la respuesta llega completa y ya verificada; aquí solo se va mostrando.
            for (const piece of text.match(/\S+\s*/g) ?? [text]) {
              yield { type: "TEXT_MESSAGE_CONTENT", messageId: o.id, delta: piece };
              await new Promise((r) => setTimeout(r, 18));
            }
            yield { type: "TEXT_MESSAGE_END", messageId: o.id };
          } else if (o.type === "function_call") {
            yield { type: "TOOL_CALL_START", toolCallId: o.callId, toolCallName: o.name };
            yield { type: "TOOL_CALL_ARGS", toolCallId: o.callId, delta: o.arguments };
            yield { type: "TOOL_CALL_END", toolCallId: o.callId };
          }
        }
      }
      return { stream: { [Symbol.asyncIterator]: () => events() }, complete: done };
    },
  };
}

const PAID = new Set(["PAID", "MARKED_AS_PAID", "PAID_EXTERNAL"]);
const OPEN = new Set(["SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED", "SHARED"]);

/** A quién recordarle: lo calcula el CÓDIGO (días de atraso, historial de pago y tono sugerido); la IA solo lo cuenta. */
const reminderCandidates: AgAiTool = {
  name: "reminder_candidates", kind: "client",
  description: "Unpaid invoices the owner could send a reminder for, already ordered by the code (most overdue first), with days overdue, the customer's payment history and the reminder tone the code suggests.",
  schema: () => ({ name: "reminder_candidates", kind: "client",
                   description: "Unpaid invoices to remind, ordered by the code, with payment history and suggested tone.",
                   parameters: { type: "object", properties: {}, additionalProperties: false } }),
  async execute(_call, ctx) {
    const { invoices } = await getInvoices();
    const today = new Date(new Date().toISOString().slice(0, 10)).getTime();
    const rows = invoices.filter((i) => OPEN.has(i.status) && !PAID.has(i.status)).map((i) => {
      const days = Math.round((today - new Date(i.due_date).getTime()) / 86_400_000);
      return {
        invoice: i.number, customer: i.customer?.name ?? null, amount_usd: i.amount, due_date: i.due_date,
        overdue: i.overdue, days_overdue: i.overdue ? Math.max(days, 0) : 0,
        customer_history: i.payer ? { paid_invoices: i.payer.paid, paid_late: i.payer.late, pattern: i.payer.kind } : null,
        suggested_tone: i.payer?.tone ?? "friendly",
        reminders_sent: i.reminder_count, last_reminder: i.last_reminder_at,
      };
    }).sort((a, b) => Number(b.overdue) - Number(a.overdue) || b.days_overdue - a.days_overdue
                      || a.due_date.localeCompare(b.due_date));
    return ctx.success(JSON.stringify(rows.length ? rows : { none: "No unpaid invoices: nothing to remind." }));
  },
};

// 8-nov: las herramientas con cifras corren EN EL SERVIDOR (`backend/studio_tools.py`, con SQL): aquí solo se declaran.
// AG Studio las anuncia al modelo y no las ejecuta; las ejecuta `studio_ai.py` en su propio ciclo. Dan totales, orden,
// máximo y mínimo: el modelo no suma ni compara (juego de 10 preguntas, DEVPOST §4.4).
const serverTool = (name: string, description: string): AgAiTool => ({
  name, kind: "server", description,
  schema: () => ({ name, kind: "server", description, parameters: { type: "object", properties: {}, additionalProperties: false } }),
});
const SERVER_TOOLS = [
  serverTool("customer_totals", "Per customer: collected, outstanding and overdue USD and invoice count, ranked, with the total of all customers. Computed by SQL. Use it for anything about customers or the total collected."),
  serverTool("contractor_totals", "Per contractor: USD paid with PayPal Payouts and PayPal fees, with the total. Computed by SQL."),
  serverTool("month_totals", "Income, costs and their difference per closed month, with the highest and lowest months and the totals. Use it for anything about months."),
  serverTool("category_totals", "Costs by accounting category over the closed months, largest first, with the total. Use it for categories or the biggest expense."),
];

/** Abre Cobros para que el dueño revise y mande el recordatorio (no envía nada). */
const openCollections: AgAiTool = {
  name: "open_collections", kind: "client",
  description: "Open the owner's Invoices screen, where the owner reviews and sends a reminder with the Collect button. Sends nothing.",
  schema: () => ({ name: "open_collections", kind: "client",
                   description: "Open the Invoices screen so the owner can review and send a reminder. Sends nothing.",
                   parameters: { type: "object", properties: { invoice: { type: "string", description: "The invoice number to look at" } },
                                 additionalProperties: false } }),
  async execute(call, ctx) {
    window.dispatchEvent(new CustomEvent(OWNER_VIEW_EVENT, { detail: "invoices" }));
    return ctx.success(`Opened Invoices${call.args.invoice ? ` (invoice ${String(call.args.invoice)})` : ""}. The owner will review and send the reminder.`);
  },
};

// 8-nov: los modelos del asistente, elegidos con la prueba de modelos (DEVPOST §4.4): los únicos que filtraron Y consultaron
// el tablero. El primero es el de siempre. Con `?ai=all-models` el selector muestra también los que fallaron (para repetir la
// prueba). El backend solo acepta su lista cerrada (`studio_ai.STUDIO_MODELS`).
const PASSED = [
  { id: "Qwen/Qwen3-235B-A22B-Instruct-2507", label: "Qwen3 235B" },
  { id: "deepseek-ai/DeepSeek-V4-Flash-0731", label: "DeepSeek V4 Flash" },
  { id: "zai-org/GLM-5.3-Flash", label: "GLM 5.3 Flash" },
];
const FAILED = [
  { id: "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", label: "Nemotron 3 Nano" },
  { id: "nvidia/Nemotron-3_5-Lightning", label: "Nemotron 3.5 Lightning" },
  { id: "nvidia/nemotron-3-super-120b-a12b", label: "Nemotron 3 Super" },
  { id: "openai/gpt-oss-120b", label: "gpt-oss 120B" },
];
const ALL_MODELS = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("ai") === "all-models";
const MODELS = ALL_MODELS ? [...PASSED, ...FAILED] : PASSED;

/** Descripciones cortas de las herramientas de AG Studio (8-nov): las originales son largas y un modelo se pierde. */
export const STUDIO_AI_TEXT = {
  "tools.view_schema.description": "List the tables and fields of this dashboard (ids like invoices.customer). Call it first.",
  "tools.execute_query.description": "Run a query on the dashboard data: pick field ids from view_schema, group by category fields and sum number fields.",
  "tools.add_page_filter.description": "Filter the whole dashboard to some values of one field, e.g. invoices.customer = Oak Street Bakery.",
  "tools.remove_page_filter.description": "Remove a filter from the dashboard.",
  "tools.view_report.description": "See the widgets on the dashboard and what each one shows.",
};

/** Cómo se ven nuestras herramientas en el chat (8-nov): nombres legibles, no `customer_totals`. */
export function toolDisplay(t: (k: string) => string) {
  const label = (key: string) => ({ label: () => ({ text: t(`owner.dashboard.ai.tool.${key}`) }) });
  return {
    customer_totals: label("customerTotals"), contractor_totals: label("contractorTotals"),
    month_totals: label("monthTotals"), category_totals: label("categoryTotals"),
    reminder_candidates: label("reminderCandidates"), open_collections: label("openCollections"),
  };
}

export function collectionsAssistant(lang: string, onTurn: (c: StudioTurnCost) => void,
                                     starters: { label: string; prompt: string }[]): AgAiHarnessSetup {
  const adapter = contabuAdapter(onTurn, lang);
  return ({ api }) => createAiHarness(api as unknown as Parameters<typeof createAiHarness>[0], ({ tools }) => ({
    agents: [directLlmRunner({
      id: "collections", name: "ContaBu", adapter, maxTurns: 8,
      description: "Collections assistant for the owner's dashboard",
      instructions: () => INSTRUCTIONS(lang),
      // Nuestras herramientas (cifras sumadas por el código) y las de AG Studio que solo leen o filtran el tablero.
      tools: () => [...SERVER_TOOLS, reminderCandidates, openCollections,
                    tools.studio.viewSchema(), tools.studio.viewReport(), tools.studio.executeQuery(),
                    tools.studio.addPageFilter(), tools.studio.removePageFilter()],
    })],
    primary: "collections",
    models: MODELS,
    promptStarters: starters,
  }));
}
