import { createAiHarness, directLlmRunner, type AgAiEvent, type AgAiHarness, type AgAiHarnessSetup, type AgAiOutputItem, type AgAiTool,
         type AgLlmAdapter, type AgLlmResponse } from "ag-studio";
import { getInvoices, studioTurn, type StudioTurnCost } from "../../api";
import i18n from "../../i18n";
const OWNER_VIEW_EVENT = "contabu:owner-view";   // el mismo de OwnerApp (sin importarlo: evita un ciclo)

// EL ASISTENTE DEL TABLERO (FASE 16, rama `paypal-contest`, 6-nov) con el Agent Framework de AG Studio.
// - El agente corre en el navegador; cada turno del modelo va a NUESTRO backend (`/studio-ai`), que llama a Nemotron en
//   Nebius: la llave nunca llega aquí.
// - Sus herramientas solo LEEN (el esquema, el tablero, consultas del motor de AG Studio, filtros de la página) más dos
//   nuestras: `reminder_candidates` (a quién recordarle, ordenado y con el tono que sugiere el CÓDIGO) y
//   `open_collections` (abre Cobros). Ninguna envía nada: el recordatorio lo revisa y lo manda el dueño.
// - La IA no calcula: cada cifra sale de una herramienta.

// 10-nov: las categorías llegan en inglés (datos en inglés, regla 5); en español, el asistente las nombra como la pantalla.
// Solo las del negocio (las de sus meses cerrados), para no mandar las 128 en cada turno.
let CATEGORIES: string[] = [];
export const setAssistantCategories = (cats: string[]) => { CATEGORIES = cats; };
const glossary = (lang: string) => lang !== "es" || !CATEGORIES.length ? ""
  : `
- Name accounting categories in Spanish exactly as the screen does: ${CATEGORIES
      .map((c) => `"${c}" = "${i18n.t(`cat.${c}`, { lng: "es", defaultValue: c })}"`).join("; ")}.`;

const INSTRUCTIONS = (lang: string) => `You are the finance assistant of a small business that gets paid with PayPal. You live in its ContaBu portal and answer questions about its finances: income and costs by month and by category, its invoices and who owes it, its payments to contractors, and the periods its bookkeeper closed.
Rules:
- Never calculate, estimate, compare or guess a number. The code's tools already give totals, rankings and the highest and lowest values: read them from there. Every figure you give must come from a tool result, copied exactly as the tool returned it. Do not add, subtract or compute percentages yourself: if you need a total, run execute_query with only that measure and the filter you need (for example the month), without grouping by category, so the query returns the total. The code checks every figure you write against the tool results and hides answers with figures it cannot find.
- Prefer the code's tools: customer_totals (customers), contractor_totals (contractors), month_totals (income and costs per month, best and worst months), category_totals (income and costs by category, for the year and for each closed month), merchant_totals (how much was spent at each store or supplier), reminder_candidates (who to remind). Only if none of them fits, call view_schema and then execute_query.
- If month_totals or category_totals returns pending_to_classify with transactions above 0, say how much is still pending to classify: it is not included in income, costs or categories until the bookkeeper classifies it.
- Never decide what kind of business a merchant is (for example, which ones are gas stations): that would be categorizing, and only rules or the bookkeeper categorize. For a question like "how much on gas", give the category that holds it (car and truck expenses) with its total, and list its merchants with their amounts exactly as merchant_totals returns them, so the owner can see.
- "Who pays late" is about payment history: use payment_habits_latest_payers_first from customer_totals (paid_after_due_date and habit). Describe each habit in plain words (on_time = always paid on time, sometimes_late = sometimes paid after the due date, often_late = usually pays late); never write the codes. Separately, you may add who has an OVERDUE invoice right now (overdue_usd above 0). An invoice that is not yet due is never late and never overdue: do not list it with the overdue ones.
- To compare a month with the previous one, use vs_previous_month from month_totals (the code computed the change; positive means it went up). Never subtract months yourself. For two months that are not consecutive, give both figures and say you can't compute the difference.
- You cannot forecast. If the owner asks about the future (next month, how much they will earn), say that you only see closed periods and invoices, and that you can't predict income. You may add which open invoices are still to collect, with their due date exactly as the tool returns it, without calling it income or earnings.
- If no tool can answer the question, say that you can't answer it from the dashboard data. Never answer a different question.
- You cannot send, create, cancel or change anything. Never say you did.
- To help collect: call reminder_candidates and recommend who to remind first, using the order and the suggested tone it returns (the code decides them from each customer's payment history). Tell the owner to press Remind next to that customer in "Who owes me". Call open_collections ONLY if the owner asks to send the reminder or to open the invoices: it leaves the dashboard.
- To show the owner something on the dashboard, call view_schema and then add_page_filter with the exact field id and values; remove_page_filter clears it.
- Write every amount in US dollars with a dollar sign, comma thousands and two decimals, for example $2,330.00, also in Spanish.
- Keep answers short: two to five sentences or a short list. Use the customer names and invoice numbers from the tools.
- Always reply in ${lang === "es" ? "Spanish" : "English"}, even if the tools return English.${glossary(lang)}
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
        .catch((e: Error): AgLlmResponse => {
          // 8-nov: el tope diario (429 `ai_cap_reached`) se dice en el chat, en el idioma del dueño; no es un error técnico.
          if (e.message === "ai_cap_reached") {
            const text = i18n.t("owner.dashboard.ai.capReached", { lng: lang });
            return { id: id("resp"), createdAt: Date.now(), status: "completed",
                     output: [{ type: "message", id: id("msg"), role: "assistant", content: [{ type: "text", text }] }] } as unknown as AgLlmResponse;
          }
          return { id: id("resp"), createdAt: Date.now(), status: "failed" as const, output: [],
                   error: { code: e.message || "ai_failed", message: e.message || "ai_failed" } } as unknown as AgLlmResponse;
        });
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
  serverTool("customer_totals", "Per customer: collected, outstanding and overdue USD and invoice count, ranked, with the total of all customers, and each customer's payment habit (paid invoices, how many after the due date: on_time, sometimes_late, often_late). Computed by SQL. Use it for anything about customers, who pays late, or the total collected."),
  serverTool("contractor_totals", "Per contractor: USD paid with PayPal Payouts and PayPal fees, with the total. Computed by SQL."),
  serverTool("month_totals", "Income, costs and their difference per closed month, with the highest and lowest months, the totals, and the change versus the previous closed month (vs_previous_month, computed by the code). Use it for anything about months or to compare a month with the one before."),
  serverTool("merchant_totals", "Costs per merchant or store (e.g. Home Depot, Shell) over the closed months: grouped by accounting category, the top merchants, and per closed month. Totals only. Use it for how much was spent at a given store or with a given supplier."),
  serverTool("category_totals", "Income and costs by accounting category, largest first: for all closed months together and for each closed month (by_month). Use it for categories, the biggest expense or income source, or what a given month's income or costs were made of."),
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
/** El nombre corto de un modelo para la pantalla ("Qwen3 235B", no "Qwen/Qwen3-235B-A22B-Instruct-2507"). */
export const modelLabel = (id: string) => [...PASSED, ...FAILED].find((m) => m.id === id || m.id.endsWith(`/${id}`))?.label ?? id.split("/").pop() ?? id;

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
                                     starters: { label: string; prompt: string }[],
                                     onHarness?: (h: AgAiHarness) => void): AgAiHarnessSetup {
  const adapter = contabuAdapter(onTurn, lang);
  // 8-nov: el harness que arma AG Studio se le pasa también a nuestro chat (`OwnerChat`), que lo usa en modo vista.
  return ({ api }) => keep(onHarness, createAiHarness(api as unknown as Parameters<typeof createAiHarness>[0], ({ tools }) => ({
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
  })));
}
const keep = (cb: ((h: AgAiHarness) => void) | undefined, h: AgAiHarness) => { cb?.(h); return h; };
