import { createAiHarness, directLlmRunner, type AgAiEvent, type AgAiHarnessSetup, type AgAiOutputItem, type AgAiTool,
         type AgLlmAdapter, type AgLlmResponse } from "ag-studio";
import { getContractors, getInvoices, studioTurn, type OwnerHome, type StudioTurnCost } from "../../api";
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
- To help collect: call reminder_candidates, recommend who to remind first using the order and the suggested tone it returns (the code decides them from each customer's payment history), and then call open_collections so the owner can review and send the reminder themselves.
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
            yield { type: "TEXT_MESSAGE_CONTENT", messageId: o.id, delta: text };
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

// 8-nov: las consultas de AG Studio (`execute_query`) tienen un esquema grande y anidado que Nemotron (Nano y Super)
// arma mal. El asistente usa herramientas NUESTRAS, simples, con las cifras ya sumadas por el código.
const money = (n: number) => n.toFixed(2);

/** Lo cobrado, por cobrar y vencido por cliente: sumado por el CÓDIGO sobre las facturas del dueño. */
const customerTotals: AgAiTool = {
  name: "customer_totals", kind: "client",
  description: "Per customer: collected, outstanding and overdue amounts (USD) and invoice count, already added up by the code.",
  schema: () => ({ name: "customer_totals", kind: "client",
                   description: "Per customer: collected, outstanding and overdue USD and invoice count, added up by the code.",
                   parameters: { type: "object", properties: {}, additionalProperties: false } }),
  async execute(_call, ctx) {
    const { invoices } = await getInvoices();
    const by = new Map<string, { customer: string; invoices: number; collected: number; outstanding: number; overdue: number }>();
    for (const i of invoices) {
      if (i.status === "DRAFT") continue;
      const k = i.customer?.name ?? "—";
      const r = by.get(k) ?? { customer: k, invoices: 0, collected: 0, outstanding: 0, overdue: 0 };
      const a = Number(i.amount);
      r.invoices += 1;
      if (PAID.has(i.status)) r.collected += a;
      else if (OPEN.has(i.status)) { if (i.overdue) r.overdue += a; else r.outstanding += a; }
      by.set(k, r);
    }
    const list = [...by.values()].sort((a, b) => b.collected - a.collected);
    const rows = list.map((r, i) => ({ rank_by_collected: i + 1, customer: r.customer, invoices: r.invoices, collected_usd: money(r.collected),
                                       outstanding_usd: money(r.outstanding), overdue_usd: money(r.overdue) }));
    // 8-nov: el total y el orden también los da el código (el modelo sumaba él mismo y la revisión de cifras lo ocultaba).
    const sum = (k: "collected" | "outstanding" | "overdue") => money(list.reduce((t, r) => t + r[k], 0));
    return ctx.success(JSON.stringify({ customers_ordered_by_collected: rows,
      all_customers_total: { collected_usd: sum("collected"), outstanding_usd: sum("outstanding"), overdue_usd: sum("overdue") } }));
  },
};

/** Lo pagado a cada contratista con PayPal Payouts: sumado por el CÓDIGO. */
const contractorTotals: AgAiTool = {
  name: "contractor_totals", kind: "client",
  description: "Per contractor: amount paid with PayPal Payouts and PayPal fees (USD), already added up by the code.",
  schema: () => ({ name: "contractor_totals", kind: "client",
                   description: "Per contractor: USD paid with PayPal Payouts and fees, added up by the code.",
                   parameters: { type: "object", properties: {}, additionalProperties: false } }),
  async execute(_call, ctx) {
    const v = await getContractors();
    if (!v.enabled) return ctx.success(JSON.stringify({ none: "Contractor payments are not enabled." }));
    const by = new Map<string, { contractor: string; payments: number; paid: number; fees: number }>();
    for (const p of v.payments) {
      if (p.status !== "SUCCESS") continue;
      const k = p.contractor ?? "—";
      const r = by.get(k) ?? { contractor: k, payments: 0, paid: 0, fees: 0 };
      r.payments += 1; r.paid += Number(p.amount); r.fees += Number(p.fee ?? 0);
      by.set(k, r);
    }
    const list = [...by.values()].sort((a, b) => b.paid - a.paid);
    const rows = list.map((r) => ({ contractor: r.contractor, payments: r.payments, paid_usd: money(r.paid), paypal_fee_usd: money(r.fees) }));
    if (!rows.length) return ctx.success(JSON.stringify({ none: "No contractor payments yet." }));
    return ctx.success(JSON.stringify({ contractors_ordered_by_paid: rows,
      all_contractors_total: { paid_usd: money(list.reduce((t, r) => t + r.paid, 0)), paypal_fee_usd: money(list.reduce((t, r) => t + r.fees, 0)) } }));
  },
};

// 8-nov (prueba de 10 preguntas): con `execute_query` el modelo a veces sumaba él mismo (y una vez mal: $12,943.10 contra
// $12,883.10; la revisión de cifras lo ocultó) o ordenaba mal. Los totales por mes y por categoría salen del CÓDIGO, de
// los periodos cerrados que ya calculó SQL (`OwnerHome.closed_months`).
const MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const monthLabel = (ym: string) => `${MONTH[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}`;

function monthTotalsTool(home: () => OwnerHome): AgAiTool {
  return {
    name: "month_totals", kind: "client",
    description: "Income, costs and expenses, and their difference (USD) for each month the bookkeeper closed, computed by the code. Use it for any question about months: best month, costs in a month, income in a month.",
    schema: () => ({ name: "month_totals", kind: "client",
                     description: "Income, costs and their difference per closed month, computed by the code.",
                     parameters: { type: "object", properties: {}, additionalProperties: false } }),
    async execute(_call, ctx) {
      const ms = [...home().closed_months].sort((a, b) => a.month.localeCompare(b.month)).map((m) => ({
        month: monthLabel(m.month), income: Number(m.total_in), costs: Number(m.total_out), net: Number(m.total_in) - Number(m.total_out),
      }));
      if (!ms.length) return ctx.success(JSON.stringify({ none: "No closed months yet." }));
      const fmt = (m: typeof ms[number]) => ({ month: m.month, income_usd: m.income.toFixed(2), costs_usd: m.costs.toFixed(2),
                                               income_minus_costs_usd: m.net.toFixed(2) });
      // 8-nov: el modelo comparó mal ("septiembre" en vez de julio): el código dice cuál es el mayor y el menor.
      const top = (k: "income" | "costs" | "net", hi: boolean) => fmt([...ms].sort((a, b) => (hi ? b[k] - a[k] : a[k] - b[k]))[0]);
      return ctx.success(JSON.stringify({
        months_in_order: ms.map(fmt),
        highest_income_month: top("income", true), lowest_income_month: top("income", false),
        highest_costs_month: top("costs", true), lowest_costs_month: top("costs", false),
        best_month_income_minus_costs: top("net", true),
        all_closed_months_total: { income_usd: ms.reduce((t, m) => t + m.income, 0).toFixed(2), costs_usd: ms.reduce((t, m) => t + m.costs, 0).toFixed(2),
                                   income_minus_costs_usd: ms.reduce((t, m) => t + m.net, 0).toFixed(2) },
      }));
    },
  };
}

function categoryTotalsTool(home: () => OwnerHome): AgAiTool {
  return {
    name: "category_totals", kind: "client",
    description: "Costs and expenses by accounting category (USD) over all closed months, largest first, computed by the code. Use it for questions about categories or the biggest expense.",
    schema: () => ({ name: "category_totals", kind: "client",
                     description: "Costs by category over all closed months, largest first, computed by the code.",
                     parameters: { type: "object", properties: {}, additionalProperties: false } }),
    async execute(_call, ctx) {
      const by = new Map<string, number>();
      for (const m of home().closed_months) for (const c of m.out) by.set(c.category, (by.get(c.category) ?? 0) + Number(c.amount));
      const list = [...by.entries()].sort((a, b) => b[1] - a[1]);
      if (!list.length) return ctx.success(JSON.stringify({ none: "No closed months yet." }));
      return ctx.success(JSON.stringify({
        categories_largest_first: list.map(([category, v], i) => ({ rank: i + 1, category, costs_usd: v.toFixed(2) })),
        all_categories_total_usd: list.reduce((t, [, v]) => t + v, 0).toFixed(2),
      }));
    },
  };
}

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
                                     starters: { label: string; prompt: string }[], home: () => OwnerHome): AgAiHarnessSetup {
  const adapter = contabuAdapter(onTurn, lang);
  return ({ api }) => createAiHarness(api as unknown as Parameters<typeof createAiHarness>[0], ({ tools }) => ({
    agents: [directLlmRunner({
      id: "collections", name: "ContaBu", adapter, maxTurns: 8,
      description: "Collections assistant for the owner's dashboard",
      instructions: () => INSTRUCTIONS(lang),
      // Nuestras herramientas (cifras sumadas por el código) y las de AG Studio que solo leen o filtran el tablero.
      tools: () => [customerTotals, contractorTotals, monthTotalsTool(home), categoryTotalsTool(home), reminderCandidates, openCollections,
                    tools.studio.viewSchema(), tools.studio.viewReport(), tools.studio.executeQuery(),
                    tools.studio.addPageFilter(), tools.studio.removePageFilter()],
    })],
    primary: "collections",
    models: MODELS,
    promptStarters: starters,
  }));
}
