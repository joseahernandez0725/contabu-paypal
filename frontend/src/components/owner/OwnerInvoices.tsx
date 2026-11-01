import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { addCustomer, createInvoice, getCustomers, getInvoices, money, refreshInvoices, sendInvoice,
         type Customer, type InvoicesView, type OwnerInvoice } from "../../api";
import ConnectPayPal from "./ConnectPayPal";
import VoiceDictation from "./VoiceDictation";
import { BTN_PRIMARY, BTN_SECONDARY, BTN_SMALL_PRIMARY, Badge, DetailCard, DetailHeader } from "../PageKit";

// COBROS, en el portal del dueño (FASE 16 Paso 5). El dueño factura a un cliente de su lista, REVISA la factura y la
// envía con PayPal; el cliente paga en el enlace de PayPal y la venta entra sola al libro (`backend/invoices.py`).
// Nada se envía sin confirmar en la pantalla de revisión. En local, el estado se consulta a PayPal al abrir Cobros y
// con "Actualizar estado" (Jose, 1-nov; publicado: webhook, deuda #37). En la vista previa del bookkeeper, todo se ve
// pero las acciones quedan apagadas (el backend además responde 403).

type Draft = { customer_id: number | ""; concept: string; amount: string; due_date: string };
// Fechas en la hora LOCAL del dueño (1-nov: con toISOString salían en UTC y de noche se corrían un día).
const localISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const in15 = () => { const d = new Date(); d.setDate(d.getDate() + 15); return localISO(d); };

// Franja de color por estado (borde izquierdo): se lee la lista de un vistazo, sin perder sobriedad.
const ACCENT: Record<string, string> = {
  DRAFT: "border-s-slate-300", SENT: "border-s-sky-500", PAID: "border-s-emerald-500", OVERDUE: "border-s-rose-500",
  PARTIALLY_PAID: "border-s-amber-500", CANCELLED: "border-s-slate-200",
};

const STATUS_CLS: Record<string, string> = {
  DRAFT: "bg-slate-50 text-slate-600 ring-slate-200", SENT: "bg-sky-50 text-sky-700 ring-sky-200",
  PAID: "bg-emerald-50 text-emerald-700 ring-emerald-200", OVERDUE: "bg-rose-50 text-rose-700 ring-rose-200",
  PARTIALLY_PAID: "bg-amber-50 text-amber-700 ring-amber-200", CANCELLED: "bg-slate-50 text-slate-500 ring-slate-200",
};

export default function OwnerInvoices({ preview, businessName, autoOpen = 0 }: { preview: boolean; businessName: string; autoOpen?: number }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language || "en";
  const [v, setV] = useState<InvoicesView | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState(false);
  const [d, setD] = useState<Draft>({ customer_id: "", concept: "", amount: "", due_date: in15() });
  const [newCust, setNewCust] = useState<{ name: string; email: string } | null>(null);
  const [confirmSend, setConfirmSend] = useState<number | null>(null);
  const [suggested, setSuggested] = useState<number[]>([]);   // clientes que nombró el dictado (si dudó entre varios)
  useEffect(() => { if (autoOpen > 0 && !preview) setOpen(true); }, [autoOpen, preview]);   // "+ Nueva factura" del Resumen
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getInvoices().then(setV).catch(() => setErr(t("owner.invoices.err.load")));
    getCustomers().then(setCustomers).catch(() => {});
    if (!preview) refreshInvoices().then(setV).catch(() => {});   // en local, en lugar del webhook
  }, [preview, t]);

  async function run<T>(fn: () => Promise<T>, after?: (x: T) => void) {
    setBusy(true); setErr(null);
    try { const x = await fn(); after?.(x); }
    catch (e) {
      const c = e instanceof Error ? e.message : "";
      const field = (e as { field?: string | null })?.field;
      setErr(field ? t("owner.invoices.err.paypal_field", { field })
        : t(`owner.invoices.err.${["bad_amount", "bad_concept", "bad_due_date", "paypal_not_connected", "name_and_email_required"].includes(c) ? c : "other"}`));
    }
    finally { setBusy(false); }
  }

  const day = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
  const cust = customers.find((c) => c.id === d.customer_id);
  const canSubmit = !!cust && d.concept.trim() && /^\d+(\.\d{1,2})?$/.test(d.amount.trim()) && Number(d.amount) > 0 && d.due_date;

  async function submit(send: boolean) {
    await run(() => createInvoice({ customer_id: Number(d.customer_id), concept: d.concept, amount: d.amount.trim(), due_date: d.due_date }),
      async (view) => {
        setV(view);
        if (send && view.invoices[0]) await run(() => sendInvoice(view.invoices[0].id), setV);
        setOpen(false); setReview(false); setD({ customer_id: "", concept: "", amount: "", due_date: in15() });
      });
  }

  if (!v) return err ? <p className="text-sm text-rose-600">{err}</p> : <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>;
  const blocked = preview || !v.paypal_connected;

  return (
    <div className="space-y-3">
      {/* Las cifras de Cobros (calculadas por SQL en el backend) como tarjetas, y las acciones a la vista. */}
      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label={t("owner.invoices.outstandingTitle")} value={money(Number(v.summary.outstanding))} accent="border-t-sky-500" />
        <Kpi label={t("owner.invoices.overdueTitle")} value={String(v.summary.overdue)} accent={v.summary.overdue ? "border-t-rose-500" : "border-t-slate-300"}
             tone={v.summary.overdue ? "text-rose-700" : "text-slate-900"} />
        <Kpi label={t("owner.invoices.collectedTitle")} value={money(Number(v.summary.collected_this_year))} accent="border-t-emerald-500" tone="text-emerald-700" />
      </div>
      {!open && (
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setOpen(true)} disabled={blocked} className={BTN_PRIMARY} title={preview ? t("owner.preview.disabled") : undefined}>
            + {t("owner.invoices.new")}
          </button>
          {!preview && (
            <button onClick={() => void run(refreshInvoices, setV)} disabled={busy} className={BTN_SECONDARY}>{t("owner.invoices.refresh")}</button>
          )}
          <span className="text-xs text-slate-500">{t("owner.invoices.howItWorks")}</span>
        </div>
      )}

      {!v.paypal_connected && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[#009CDE]/30 bg-[#009CDE]/5 px-3 py-2.5">
          <span className="flex-1 text-sm text-slate-700">{t("owner.invoices.connectFirst")}</span>
          <ConnectPayPal from="owner" size="sm" disabled={preview} title={preview ? t("owner.preview.disabled") : undefined} />
        </div>
      )}
      {err && <p className="text-sm text-rose-600">{err}</p>}

      {open && (
        <DetailCard>
          <DetailHeader kicker={t("owner.nav.invoices")} title={review ? t("owner.invoices.reviewTitle") : t("owner.invoices.new")} />
          {!review ? (
            <div className="mt-3 grid gap-3 sm:grid-cols-2 [&>*]:min-w-0">
              {/* Paso 6: dictar llena ESTE formulario (no envía nada; después viene la revisión). */}
              <VoiceDictation customerId={d.customer_id} onFill={(r) => {
                setD((x) => ({
                  ...x,
                  customer_id: r.customer ? r.customer.id : x.customer_id,
                  amount: r.fields?.amount ?? x.amount,
                  concept: r.fields?.concept ?? x.concept,
                  due_date: r.fields?.due_date ?? x.due_date,
                }));
                setSuggested(r.candidates.map((c) => c.id));
              }} />
              <label className="text-xs text-slate-600 space-y-1 sm:col-span-2">
                <span>{t("owner.invoices.customer")}</span>
                <div className="flex gap-2 min-w-0">
                  <select value={d.customer_id} onChange={(e) => { setD({ ...d, customer_id: e.target.value ? Number(e.target.value) : "" }); setSuggested([]); }}
                          className={`flex-1 min-w-0 rounded-lg border px-2 py-2 text-sm ${suggested.length && !d.customer_id ? "border-amber-400 ring-2 ring-amber-200" : "border-slate-200"}`}>
                    <option value="">{t("owner.invoices.pickCustomer")}</option>
                    {/* Los que nombró el dictado van primero (si eran dos o más parecidos). */}
                    {[...customers].sort((a, b) => Number(suggested.includes(b.id)) - Number(suggested.includes(a.id))).map((c) =>
                      <option key={c.id} value={c.id}>{suggested.includes(c.id) ? "★ " : ""}{c.name} · {c.email}</option>)}
                  </select>
                  <button type="button" onClick={() => setNewCust({ name: "", email: "" })} className={BTN_SECONDARY}>{t("owner.invoices.newCustomer")}</button>
                </div>
              </label>
              {newCust && (
                <div className="sm:col-span-2 flex flex-wrap gap-2 items-end bg-slate-50 rounded-lg p-2">
                  <input placeholder={t("owner.invoices.customerName")} value={newCust.name} onChange={(e) => setNewCust({ ...newCust, name: e.target.value })}
                         className="flex-1 min-w-[10rem] rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
                  <input type="email" placeholder={t("owner.invoices.customerEmail")} value={newCust.email} onChange={(e) => setNewCust({ ...newCust, email: e.target.value })}
                         className="flex-1 min-w-[12rem] rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
                  <button type="button" disabled={busy} className={BTN_SMALL_PRIMARY}
                          onClick={() => void run(() => addCustomer(newCust.name, newCust.email), (c) => { setCustomers((xs) => [...xs, c]); setD((x) => ({ ...x, customer_id: c.id })); setNewCust(null); })}>
                    {t("owner.invoices.saveCustomer")}
                  </button>
                </div>
              )}
              <label className="text-xs text-slate-600 space-y-1 sm:col-span-2">
                <span>{t("owner.invoices.concept")}</span>
                <input value={d.concept} maxLength={200} onChange={(e) => setD({ ...d, concept: e.target.value })}
                       className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm" />
              </label>
              <label className="text-xs text-slate-600 space-y-1">
                <span>{t("owner.invoices.amount")}</span>
                <input inputMode="decimal" placeholder="0.00" value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })}
                       className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm tabular-nums" />
              </label>
              <label className="text-xs text-slate-600 space-y-1">
                <span>{t("owner.invoices.dueDate")}</span>
                <input type="date" value={d.due_date} min={localISO(new Date())} onChange={(e) => setD({ ...d, due_date: e.target.value })}
                       className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm" />
              </label>
              <div className="sm:col-span-2 flex gap-2">
                <button disabled={!canSubmit} onClick={() => setReview(true)} className={BTN_PRIMARY}>{t("owner.invoices.review")}</button>
                <button onClick={() => { setOpen(false); setNewCust(null); }} className={BTN_SECONDARY}>{t("owner.invoices.cancel")}</button>
              </div>
            </div>
          ) : (
            <div className="mt-3 space-y-3">
              <InvoicePreview business={businessName} customer={cust} concept={d.concept} amount={d.amount} due={day(d.due_date)} issued={day(localISO(new Date()))} />
              <p className="text-xs text-slate-500">{t("owner.invoices.reviewNote", { email: cust?.email })}</p>
              <div className="flex flex-wrap gap-2">
                <button disabled={busy} onClick={() => void submit(true)} className={BTN_PRIMARY}>{busy ? t("owner.paypal.working") : t("owner.invoices.send")}</button>
                <button disabled={busy} onClick={() => void submit(false)} className={BTN_SECONDARY}>{t("owner.invoices.saveDraft")}</button>
                <button disabled={busy} onClick={() => setReview(false)} className={BTN_SECONDARY}>{t("owner.invoices.edit")}</button>
              </div>
            </div>
          )}
        </DetailCard>
      )}

      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-3 py-2 border-b border-slate-100 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.invoices.listTitle")}</div>
        {v.invoices.length === 0 ? (
          <div className="px-3 py-8 text-center">
            <p className="text-sm text-slate-500">{t("owner.invoices.none")}</p>
            <p className="text-xs text-slate-400 mt-1">{t("owner.invoices.noneHint")}</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {v.invoices.map((inv) => <Row key={inv.id} inv={inv} day={day} preview={preview} busy={busy}
                                         confirming={confirmSend === inv.id} onAskSend={() => setConfirmSend(inv.id)}
                                         onCancelSend={() => setConfirmSend(null)}
                                         onSend={() => void run(() => sendInvoice(inv.id), (x) => { setV(x); setConfirmSend(null); })} />)}
          </ul>
        )}
      </div>
    </div>
  );
}

function Row({ inv, day, preview, busy, confirming, onAskSend, onCancelSend, onSend }: {
  inv: OwnerInvoice; day: (iso: string) => string; preview: boolean; busy: boolean; confirming: boolean;
  onAskSend: () => void; onCancelSend: () => void; onSend: () => void;
}) {
  const { t } = useTranslation();
  const key = inv.overdue ? "OVERDUE" : inv.status;
  const name = inv.customer?.name ?? "—";
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "·";
  return (
    <li className={`px-3 py-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm border-s-4 ${ACCENT[key] ?? "border-s-slate-200"}`}>
      <span className="size-9 shrink-0 rounded-full bg-slate-100 text-slate-600 text-xs font-bold flex items-center justify-center">{initials}</span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-slate-900 truncate">{name}</span>
        <span className="block text-xs text-slate-500 truncate">
          {inv.number ? `#${inv.number} · ` : ""}{inv.concept} · {t("owner.invoices.due", { date: day(inv.due_date) })}
        </span>
      </span>
      <span className="text-end">
        <span className="block tabular-nums text-base font-semibold text-slate-900">{money(Number(inv.amount))}</span>
        <span className="flex items-center justify-end gap-1.5">
          <Badge cls={STATUS_CLS[key] ?? STATUS_CLS.DRAFT}>{t(`owner.invoices.status.${key}`, { defaultValue: inv.status })}</Badge>
          {inv.in_books && <span className="text-[11px] text-emerald-700" title={t("owner.invoices.inBooks")}>✓ {t("owner.invoices.inBooksShort")}</span>}
        </span>
      </span>
      <span className="flex items-center gap-2 basis-full sm:basis-auto justify-end">
        {inv.status === "DRAFT" && !preview && (confirming ? (
          <>
            <span className="text-xs text-slate-600">{t("owner.invoices.confirmSend", { email: inv.customer?.email })}</span>
            <button disabled={busy} onClick={onSend} className={BTN_SMALL_PRIMARY}>{t("owner.invoices.send")}</button>
            <button disabled={busy} onClick={onCancelSend} className={BTN_SECONDARY}>{t("owner.invoices.cancel")}</button>
          </>
        ) : (
          <button onClick={onAskSend} className={BTN_SMALL_PRIMARY}>{t("owner.invoices.send")}</button>
        ))}
        {inv.recipient_view_url && (
          <a href={inv.recipient_view_url} target="_blank" rel="noreferrer" className="text-xs font-medium text-brand-green hover:underline">
            {t("owner.invoices.view")}
          </a>
        )}
      </span>
    </li>
  );
}

function Kpi({ label, value, accent, tone = "text-slate-900" }: { label: string; value: string; accent: string; tone?: string }) {
  return (
    <div className={`bg-white rounded-xl border border-slate-200 border-t-4 ${accent} px-4 py-3`}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-2xl font-semibold mt-0.5 ${tone}`}>{value}</div>
    </div>
  );
}

// Comisión de PayPal por factura en EE. UU. (✅ paypal.com/us/business/paypal-business-fees, 1-nov-2026): 3.49% + $0.49.
// Es una ESTIMACIÓN para el dueño (la real la trae PayPal y entra al libro aparte, en Merchant fees).
const FEE_RATE = 0.0349, FEE_FIXED = 0.49;

/** La revisión antes de enviar, con forma de FACTURA: quién emite, a quién, el renglón, el total y el vencimiento. */
function InvoicePreview({ business, customer, concept, amount, due, issued }: {
  business: string; customer?: Customer; concept: string; amount: string; due: string; issued: string;
}) {
  const { t } = useTranslation();
  const total = Number(amount);
  const fee = Math.round((total * FEE_RATE + FEE_FIXED) * 100) / 100;
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 bg-slate-50 border-b border-slate-200">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">{t("owner.invoices.from")}</div>
          <div className="text-base font-semibold text-slate-900">{business}</div>
        </div>
        <div className="text-end">
          <div className="text-xl font-bold tracking-tight text-slate-900">{t("owner.invoices.docTitle")}</div>
          <div className="text-xs text-slate-500">{t("owner.invoices.issued", { date: issued })}</div>
        </div>
      </div>
      <div className="px-5 py-4 grid gap-4 sm:grid-cols-2 text-sm">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">{t("owner.invoices.billTo")}</div>
          <div className="font-semibold text-slate-900">{customer?.name}</div>
          <div className="text-slate-600">{customer?.email}</div>
        </div>
        <div className="sm:text-end">
          <div className="text-[11px] uppercase tracking-wide text-slate-500">{t("owner.invoices.dueDate")}</div>
          <div className="font-semibold text-slate-900">{due}</div>
        </div>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-wide text-slate-500 border-y border-slate-100">
            <th className="px-5 py-2 text-start font-medium">{t("owner.invoices.concept")}</th>
            <th className="px-5 py-2 text-end font-medium">{t("owner.invoices.qty")}</th>
            <th className="px-5 py-2 text-end font-medium">{t("owner.invoices.price")}</th>
            <th className="px-5 py-2 text-end font-medium">{t("owner.invoices.total")}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="px-5 py-3 text-slate-900">{concept}</td>
            <td className="px-5 py-3 text-end tabular-nums">1</td>
            <td className="px-5 py-3 text-end tabular-nums">{money(total)}</td>
            <td className="px-5 py-3 text-end tabular-nums">{money(total)}</td>
          </tr>
        </tbody>
      </table>
      <div className="px-5 py-3 border-t border-slate-200 flex flex-wrap items-end justify-between gap-3">
        <div className="text-xs text-slate-500 max-w-sm">
          {t("owner.invoices.payWith")}<br />
          {t("owner.invoices.feeEstimate", { fee: money(fee), net: money(Math.max(total - fee, 0)) })}
        </div>
        <div className="text-end">
          <div className="text-xs text-slate-500">{t("owner.invoices.totalDue")}</div>
          <div className="text-2xl font-bold tabular-nums text-slate-900">{money(total)} <span className="text-xs font-medium text-slate-500">USD</span></div>
        </div>
      </div>
    </div>
  );
}
