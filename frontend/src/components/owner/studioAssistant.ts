import { createAiHarness, directLlmRunner, type AgAiEvent, type AgAiHarnessSetup, type AgAiOutputItem, type AgAiTool,
         type AgLlmAdapter, type AgLlmResponse } from "ag-studio";
import { getContractors, getInvoices, studioTurn, type StudioTurnCost } from "../../api";
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
- Never calculate, estimate or guess a number. Every figure you give must come from a tool result (customer_totals, contractor_totals or reminder_candidates). Do not add numbers up yourself.
- You cannot send, create, cancel or change anything. Never say you did.
- To help collect: call reminder_candidates, recommend who to remind first using the order and the suggested tone it returns (the code decides them from each customer's payment history), and then call open_collections so the owner can review and send the reminder themselves.
- Keep answers short: two to five sentences or a short list. Use the customer names and invoice numbers from the tools.
- Always reply in ${lang === "es" ? "Spanish" : "English"}, even if the tools return English.
- Call the tool first, then answer once. Do not narrate what you are about to do.`;

const id = (p: string) => `${p}_${Math.random().toString(36).slice(2, 10)}`;

/** El adaptador: un turno de AG Studio → `/studio-ai` → la respuesta y sus eventos (sin streaming: llega entera). */
export function contabuAdapter(onTurn: (c: StudioTurnCost) => void): AgLlmAdapter {
  return {
    executeTurn(request, options) {
      const done: Promise<AgLlmResponse> = studioTurn({
        instructions: request.instructions, input: request.input, tools: request.tools,
        toolChoice: request.toolChoice,
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
    const rows = [...by.values()].sort((a, b) => b.collected - a.collected)
      .map((r) => ({ customer: r.customer, invoices: r.invoices, collected_usd: money(r.collected), outstanding_usd: money(r.outstanding), overdue_usd: money(r.overdue) }));
    return ctx.success(JSON.stringify(rows));
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
    const rows = [...by.values()].map((r) => ({ contractor: r.contractor, payments: r.payments, paid_usd: money(r.paid), paypal_fee_usd: money(r.fees) }));
    return ctx.success(JSON.stringify(rows.length ? rows : { none: "No contractor payments yet." }));
  },
};

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

export function collectionsAssistant(lang: string, onTurn: (c: StudioTurnCost) => void,
                                     starters: { label: string; prompt: string }[]): AgAiHarnessSetup {
  const adapter = contabuAdapter(onTurn);
  return ({ api }) => createAiHarness(api as unknown as Parameters<typeof createAiHarness>[0], () => ({
    agents: [directLlmRunner({
      id: "collections", name: "ContaBu", adapter, maxTurns: 8,
      description: "Collections assistant for the owner's dashboard",
      instructions: () => INSTRUCTIONS(lang),
      tools: () => [customerTotals, contractorTotals, reminderCandidates, openCollections],
    })],
    primary: "collections",
    promptStarters: starters,
  }));
}
