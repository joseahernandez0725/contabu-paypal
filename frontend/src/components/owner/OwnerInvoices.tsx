import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { addCustomer, cancelInvoice, createInvoice, createSeries, discardInvoice, getCustomers, getInvoiceProfile, getInvoices,
         getNextInvoiceNumber,
         getSeries, logoSrc, money,
         recordOfflinePayment,
         recordOfflineRefund,
         refreshInvoices, sendInvoice,
         type Customer, type InvoiceProfile, type InvoiceSeries, type InvoicesView, type OfflineMethod, type OwnerInvoice,
         type PaymentTerm, type SeriesUnit } from "../../api";
import ConnectPayPal from "./ConnectPayPal";
import InvoiceCollect, { OFFLINE_METHODS, localToday } from "./InvoiceCollect";
import OwnerDisputes from "./OwnerDisputes";
import { formatPhone } from "./InvoiceProfileCard";
import SeriesList, { everyText } from "./SeriesList";
import VoiceDictation from "./VoiceDictation";
import { BTN_PRIMARY, BTN_SECONDARY, BTN_SMALL_PRIMARY, Badge, DetailCard, DetailHeader } from "../PageKit";

// COBROS, en el portal del dueño (FASE 16 Paso 5). El dueño factura a un cliente de su lista, REVISA la factura y la
// envía con PayPal; el cliente paga en el enlace de PayPal y la venta entra sola al libro (`backend/invoices.py`).
// Nada se envía sin confirmar en la pantalla de revisión. En local, el estado se consulta a PayPal al abrir Cobros y
// con "Actualizar estado" (Jose, 1-nov; publicado: webhook, deuda #37). En la vista previa del bookkeeper, todo se ve
// pero las acciones quedan apagadas (el backend además responde 403).

type Draft = { customer_id: number | ""; concept: string; amount: string; due_date: string; number: string; reference: string;
               repeat: string; start: string; cycles: number; term: PaymentTerm };   // repeat: "" o "MONTH:1" (4-nov)
const REPEATS = ["WEEK:1", "WEEK:2", "MONTH:1", "MONTH:3", "YEAR:1"];
const CYCLES = [0, 3, 6, 12, 24];
const TERMS: PaymentTerm[] = ["DUE_ON_RECEIPT", "NET_10", "NET_15", "NET_30"];
// Fechas en la hora LOCAL del dueño (1-nov: con toISOString salían en UTC y de noche se corrían un día).
const localISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const in15 = () => { const d = new Date(); d.setDate(d.getDate() + 15); return localISO(d); };

// Franja de color por estado (borde izquierdo): se lee la lista de un vistazo, sin perder sobriedad.
const ACCENT: Record<string, string> = {
  DRAFT: "border-s-slate-300", SENT: "border-s-sky-500", PAID: "border-s-emerald-500", OVERDUE: "border-s-rose-500",
  PARTIALLY_PAID: "border-s-amber-500", CANCELLED: "border-s-slate-200",
  MARKED_AS_PAID: "border-s-emerald-500", PAID_EXTERNAL: "border-s-emerald-500",
  REFUNDED: "border-s-slate-300", MARKED_AS_REFUNDED: "border-s-slate-300", PARTIALLY_REFUNDED: "border-s-amber-500",
};
// Pagada: por PayPal o marcada como cobrada fuera (✅ especificación de Invoicing: lo marcado a mano no es PAID).
const PAID_ANY = ["PAID", "MARKED_AS_PAID", "PAID_EXTERNAL"];

const STATUS_CLS: Record<string, string> = {
  DRAFT: "bg-slate-50 text-slate-600 ring-slate-200", SENT: "bg-sky-50 text-sky-700 ring-sky-200",
  PAID: "bg-emerald-50 text-emerald-700 ring-emerald-200", OVERDUE: "bg-rose-50 text-rose-700 ring-rose-200",
  PARTIALLY_PAID: "bg-amber-50 text-amber-700 ring-amber-200", CANCELLED: "bg-slate-50 text-slate-500 ring-slate-200",
  MARKED_AS_PAID: "bg-emerald-50 text-emerald-700 ring-emerald-200", PAID_EXTERNAL: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  REFUNDED: "bg-slate-50 text-slate-600 ring-slate-200", MARKED_AS_REFUNDED: "bg-slate-50 text-slate-600 ring-slate-200",
  PARTIALLY_REFUNDED: "bg-amber-50 text-amber-700 ring-amber-200",
};

export default function OwnerInvoices({ preview, businessName, autoOpen = 0 }: { preview: boolean; businessName: string; autoOpen?: number }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language || "en";
  const [v, setV] = useState<InvoicesView | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState(false);
  const [d, setD] = useState<Draft>({ customer_id: "", concept: "", amount: "", due_date: in15(), number: "", reference: "", repeat: "", start: localISO(new Date()), cycles: 12, term: "NET_15" });
  const [newCust, setNewCust] = useState<{ name: string; email: string } | null>(null);
  const [confirmSend, setConfirmSend] = useState<number | null>(null);
  // ¿Te paga ahora mismo? (3-nov): con tarjeta → se envía sin correo y se abre su QR; ya pagó → queda pagada al instante.
  const [payNow, setPayNow] = useState<{ method: OfflineMethod; note: string } | null>(null);
  const [qrFor, setQrFor] = useState<number | null>(null);
  const [profile, setProfile] = useState<InvoiceProfile | null>(null);   // los datos del negocio (4-nov), para la vista previa
  const [series, setSeries] = useState<InvoiceSeries[]>([]);             // facturas recurrentes (4-nov)
  // Al abrir "Nueva factura", el número que sigue en PayPal (respeta el formato del dueño); se puede cambiar.
  useEffect(() => {
    if (open && !preview) getNextInvoiceNumber().then((r) => setD((x) => (x.number ? x : { ...x, number: r.number ?? "" }))).catch(() => {});
  }, [open, preview]);
  const [suggested, setSuggested] = useState<number[]>([]);   // clientes que nombró el dictado (si dudó entre varios)
  useEffect(() => { if (autoOpen > 0 && !preview) setOpen(true); }, [autoOpen, preview]);   // "+ Nueva factura" del Resumen
  const [busy, setBusy] = useState(false);
  // 8-nov (Jose): las tarjetas de arriba filtran la lista. Otro clic en la misma, o "Ver todas", la quita.
  const [show, setShow] = useState<CardFilter | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  // En el celular las tarjetas ocupan la pantalla: al elegir una, la página baja hasta la lista para que se vea el cambio.
  const pick = (f: CardFilter) => {
    const next = show === f ? null : f;
    setShow(next);
    if (next && window.innerWidth < 640) setTimeout(() => listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getInvoices().then(setV).catch(() => setErr(t("owner.invoices.err.load")));
    getCustomers().then(setCustomers).catch(() => {});
    getSeries().then((r) => setSeries(r.series)).catch(() => {});
    getInvoiceProfile().then(setProfile).catch(() => {});   // los datos de la factura viven en Configuración (4-nov)
    if (!preview) refreshInvoices().then((x) => { setV(x); getSeries().then((r) => setSeries(r.series)).catch(() => {}); }).catch(() => {});   // en local, en lugar del webhook
  }, [preview, t]);

  async function run<T>(fn: () => Promise<T>, after?: (x: T) => void) {
    setBusy(true); setErr(null);
    try { const x = await fn(); after?.(x); }
    catch (e) {
      const c = e instanceof Error ? e.message : "";
      const field = (e as { field?: string | null })?.field;
      setErr(field ? t("owner.invoices.err.paypal_field", { field })
        : t(`owner.invoices.err.${["bad_amount", "bad_concept", "bad_due_date", "paypal_not_connected", "name_and_email_required", "duplicate_number", "bad_number", "bad_reference", "bad_frequency", "bad_cycles", "bad_term", "bad_start"].includes(c) ? c : "other"}`));
    }
    finally { setBusy(false); }
  }

  const day = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
  const cust = customers.find((c) => c.id === d.customer_id);
  const canSubmit = !!cust && d.concept.trim() && /^\d+(\.\d{1,2})?$/.test(d.amount.trim()) && Number(d.amount) > 0 && (d.repeat ? d.start : d.due_date);

  // Programar la serie (4-nov): PayPal la activa y envía cada factura sola.
  async function submitSeries() {
    const [unit, count] = d.repeat.split(":");
    await run(() => createSeries({ customer_id: Number(d.customer_id), concept: d.concept, amount: d.amount.trim(),
                                   interval_unit: unit as SeriesUnit, interval_count: Number(count), start_date: d.start,
                                   total_cycles: d.cycles, payment_term: d.term }),
      (r) => {
        setSeries(r.series); setOpen(false); setReview(false);
        setD({ customer_id: "", concept: "", amount: "", due_date: in15(), number: "", reference: "", repeat: "", start: localISO(new Date()), cycles: 12, term: "NET_15" });
        if (d.start === localISO(new Date())) setTimeout(() => {          // PayPal genera la primera en ~30 s
          refreshInvoices().then((x) => { setV(x); getSeries().then((r) => setSeries(r.series)).catch(() => {}); }).catch(() => {});
        }, 40000);
      });
  }

  async function submit(mode: "send" | "draft" | "card" | "paid") {
    await run(() => createInvoice({ customer_id: Number(d.customer_id), concept: d.concept, amount: d.amount.trim(), due_date: d.due_date,
                                    number: d.number.trim(), reference: d.reference.trim() }),
      async (view) => {
        setV(view);
        const id = view.invoices[0]?.id;
        if (id && mode === "send") await run(() => sendInvoice(id), setV);
        if (id && mode === "card") await run(() => sendInvoice(id, false), (x) => { setV(x); setQrFor(id); });
        if (id && mode === "paid" && payNow) await run(() => recordOfflinePayment(id, payNow.method, localToday(), payNow.note), setV);
        setOpen(false); setReview(false); setPayNow(null); setD({ customer_id: "", concept: "", amount: "", due_date: in15(), number: "", reference: "", repeat: "", start: localISO(new Date()), cycles: 12, term: "NET_15" });
      });
  }

  if (!v) return err ? <p className="text-sm text-rose-600">{err}</p> : <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>;
  const blocked = preview || !v.paypal_connected;
  const shown = show ? v.invoices.filter((inv) => matches(inv, show)) : v.invoices;

  return (
    <div className="space-y-3">
      {/* Las cifras de Cobros (calculadas por SQL en el backend) como tarjetas, y las acciones a la vista. */}
      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label={t("owner.invoices.outstandingTitle")} value={money(Number(v.summary.outstanding))} accent="border-t-sky-500"
             active={show === "outstanding"} onClick={() => pick("outstanding")} />
        <Kpi label={t("owner.invoices.overdueTitle")} value={String(v.summary.overdue)} accent={v.summary.overdue ? "border-t-rose-500" : "border-t-slate-300"}
             tone={v.summary.overdue ? "text-rose-700" : "text-slate-900"}
             active={show === "overdue"} onClick={() => pick("overdue")} />
        <Kpi label={t("owner.invoices.collectedTitle")} value={money(Number(v.summary.collected_this_year))} accent="border-t-emerald-500" tone="text-emerald-700" tour="pp-collected"
             active={show === "collected"} onClick={() => pick("collected")} />
      </div>
      {!open && (
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setOpen(true)} disabled={blocked} className={BTN_PRIMARY} title={preview ? t("owner.preview.disabled") : undefined}
                  data-tour="pp-new-invoice">
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
                <span>{t("owner.series.repeat")}</span>
                <select value={d.repeat} onChange={(e) => setD({ ...d, repeat: e.target.value })} className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm">
                  <option value="">{t("owner.series.noRepeat")}</option>
                  {REPEATS.map((r) => { const [u, c] = r.split(":"); return <option key={r} value={r}>{everyText(t, u, Number(c))}</option>; })}
                </select>
              </label>
              {d.repeat ? (
                <>
                  <label className="text-xs text-slate-600 space-y-1">
                    <span>{t("owner.series.start")}</span>
                    <input type="date" value={d.start} min={localISO(new Date())} onChange={(e) => setD({ ...d, start: e.target.value })}
                           className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm" />
                  </label>
                  <label className="text-xs text-slate-600 space-y-1">
                    <span>{t("owner.series.cycles")}</span>
                    <select value={d.cycles} onChange={(e) => setD({ ...d, cycles: Number(e.target.value) })} className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm">
                      {CYCLES.map((n) => <option key={n} value={n}>{n ? t("owner.series.cyclesN", { count: n }) : t("owner.series.noEnd")}</option>)}
                    </select>
                  </label>
                  <label className="text-xs text-slate-600 space-y-1">
                    <span>{t("owner.series.term")}</span>
                    <select value={d.term} onChange={(e) => setD({ ...d, term: e.target.value as PaymentTerm })} className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm">
                      {TERMS.map((x) => <option key={x} value={x}>{t(`owner.series.terms.${x}`)}</option>)}
                    </select>
                  </label>
                </>
              ) : (<>
              <label className="text-xs text-slate-600 space-y-1">
                <span>{t("owner.invoices.number")}</span>
                <input value={d.number} maxLength={25} onChange={(e) => setD({ ...d, number: e.target.value })} placeholder={t("owner.invoices.numberPh")}
                       className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm" />
              </label>
              <label className="text-xs text-slate-600 space-y-1">
                <span>{t("owner.invoices.reference")}</span>
                <input value={d.reference} maxLength={120} onChange={(e) => setD({ ...d, reference: e.target.value })} placeholder={t("owner.invoices.referencePh")}
                       className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm" />
              </label>
              <label className="text-xs text-slate-600 space-y-1">
                <span>{t("owner.invoices.dueDate")}</span>
                <input type="date" value={d.due_date} min={localISO(new Date())} onChange={(e) => setD({ ...d, due_date: e.target.value })}
                       className="w-full rounded-lg border border-slate-200 px-2 py-2 text-sm" />
              </label>
              </>)}
              <div className="sm:col-span-2 flex gap-2">
                <button disabled={!canSubmit} onClick={() => setReview(true)} className={BTN_PRIMARY}>{t("owner.invoices.review")}</button>
                <button onClick={() => { setOpen(false); setNewCust(null); }} className={BTN_SECONDARY}>{t("owner.invoices.cancel")}</button>
              </div>
            </div>
          ) : (
            <div className="mt-3 space-y-3">
              <InvoicePreview business={businessName} customer={cust} concept={d.concept} amount={d.amount}
                              due={d.repeat ? t(`owner.series.terms.${d.term}`) : day(d.due_date)} issued={day(d.repeat ? d.start : localISO(new Date()))}
                              profile={profile} number={d.repeat ? "" : d.number.trim()} reference={d.repeat ? "" : d.reference.trim()} />
              {d.repeat ? (
                <div className="space-y-2">
                  <p className="text-sm text-slate-700">{t("owner.series.reviewNote", { every: everyText(t, d.repeat.split(":")[0], Number(d.repeat.split(":")[1])).toLowerCase(),
                    start: day(d.start), times: d.cycles ? t("owner.series.cyclesN", { count: d.cycles }) : t("owner.series.noEnd").toLowerCase(),
                    term: t(`owner.series.terms.${d.term}`).toLowerCase(), email: cust?.email })}</p>
                  <div className="flex flex-wrap gap-2">
                    <button disabled={busy} onClick={() => void submitSeries()} className={BTN_PRIMARY}>{busy ? t("owner.paypal.working") : t("owner.series.schedule")}</button>
                    <button disabled={busy} onClick={() => setReview(false)} className={BTN_SECONDARY}>{t("owner.invoices.edit")}</button>
                  </div>
                </div>
              ) : (<>
              <p className="text-xs text-slate-500">{t("owner.invoices.reviewNote", { email: cust?.email })}</p>
              <div className="flex flex-wrap gap-2">
                <button disabled={busy} onClick={() => void submit("send")} className={BTN_PRIMARY}>{busy ? t("owner.paypal.working") : t("owner.invoices.send")}</button>
                <button disabled={busy} onClick={() => void submit("draft")} className={BTN_SECONDARY}>{t("owner.invoices.saveDraft")}</button>
                <button disabled={busy} onClick={() => setReview(false)} className={BTN_SECONDARY}>{t("owner.invoices.edit")}</button>
              </div>
              {/* El cliente paga AHÍ MISMO: con tarjeta (el QR en pantalla) o ya pagó en efectivo/cheque (pagada al instante). */}
              <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 space-y-2">
                <p className="text-sm font-semibold text-slate-900">{t("owner.payNow.title")}</p>
                <div className="flex flex-wrap gap-2">
                  <button disabled={busy} onClick={() => void submit("card")} className={BTN_SECONDARY}>
                    {t("owner.payNow.card")}
                  </button>
                  {!payNow && <button disabled={busy} onClick={() => setPayNow({ method: "CASH", note: "" })} className={BTN_SECONDARY}>{t("owner.payNow.paid")}</button>}
                </div>
                <p className="text-xs text-slate-500">{t("owner.payNow.cardHint")}</p>
                {payNow && (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <select value={payNow.method} onChange={(e) => setPayNow({ ...payNow, method: e.target.value as OfflineMethod })}
                            aria-label={t("owner.offline.method")} className="rounded-md border border-slate-300 px-2 py-1">
                      {OFFLINE_METHODS.map((m) => <option key={m} value={m}>{t(`owner.offline.methods.${m}`)}</option>)}
                    </select>
                    <input value={payNow.note} maxLength={500} placeholder={t("owner.offline.notePh")} onChange={(e) => setPayNow({ ...payNow, note: e.target.value })}
                           className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1" />
                    <button disabled={busy} onClick={() => void submit("paid")} className={BTN_SMALL_PRIMARY}>{t("owner.payNow.paidConfirm")}</button>
                    <button disabled={busy} onClick={() => setPayNow(null)} className={BTN_SECONDARY}>{t("owner.invoices.cancel")}</button>
                    <p className="basis-full text-xs text-slate-500">{t("owner.payNow.paidHint")}</p>
                  </div>
                )}
              </div>
              </>)}
            </div>
          )}
        </DetailCard>
      )}

      <div ref={listRef} className="scroll-mt-4 bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-100">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.invoices.listTitle")}</span>
          {show && (
            <>
              <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-[#0070BA] ring-1 ring-sky-200">
                {t(`owner.invoices.showing.${show}`, { count: shown.length })}
              </span>
              <button onClick={() => setShow(null)} className="ms-auto text-xs font-semibold text-[#0070BA] hover:underline">{t("owner.invoices.showAll")}</button>
            </>
          )}
        </div>
        {show && shown.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-slate-500">{t(`owner.invoices.showingNone.${show}`)}</p>
        ) : v.invoices.length === 0 ? (
          <div className="px-3 py-8 text-center">
            <p className="text-sm text-slate-500">{t("owner.invoices.none")}</p>
            <p className="text-xs text-slate-400 mt-1">{t("owner.invoices.noneHint")}</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {shown.map((inv) => <Row key={inv.id} inv={inv} day={day} preview={preview} busy={busy} autoQr={qrFor === inv.id}
                                         confirming={confirmSend === inv.id} onAskSend={() => setConfirmSend(inv.id)}
                                         onCancelSend={() => setConfirmSend(null)}
                                         onSend={() => void run(() => sendInvoice(inv.id), (x) => { setV(x); setConfirmSend(null); })}
                                         onView={setV} />)}
          </ul>
        )}
      </div>
      <SeriesList series={series} preview={preview} onChange={setSeries} />
      {v.paypal_connected && <OwnerDisputes preview={preview} />}
    </div>
  );
}

// Enviadas y sin cobrar del todo (las mismas que `OPEN` en `backend/invoices.py`): se pueden cobrar.
const OPEN = ["SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED"];

function Row({ inv, day, preview, busy, confirming, autoQr = false, onAskSend, onCancelSend, onSend, onView }: {
  inv: OwnerInvoice; day: (iso: string) => string; preview: boolean; busy: boolean; confirming: boolean; autoQr?: boolean;
  onAskSend: () => void; onCancelSend: () => void; onSend: () => void; onView: (v: InvoicesView) => void;
}) {
  const { t } = useTranslation();
  const [collecting, setCollecting] = useState(autoQr);
  const [discarding, setDiscarding] = useState(false);   // confirmar "Descartar" en la fila (5-nov; antes, window.confirm)
  useEffect(() => { if (autoQr) setCollecting(true); }, [autoQr]);
  const canCollect = OPEN.includes(inv.status) && !preview;
  const key = inv.overdue ? "OVERDUE" : inv.status;
  const name = inv.customer?.name ?? "—";
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "·";
  return (
    <li data-tour={inv.status === "PAID" && inv.in_books ? "pp-paid-invoice" : undefined}
        className={`px-3 py-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm border-s-4 ${ACCENT[key] ?? "border-s-slate-200"}`}>
      <span className="size-9 shrink-0 rounded-full bg-slate-100 text-slate-600 text-xs font-bold flex items-center justify-center">{initials}</span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-slate-900 truncate">{name}</span>
        <span className="block text-xs text-slate-500 truncate">
          {inv.series_id ? `↻ ` : ""}{inv.number ? `#${inv.number} · ` : ""}{inv.concept} · {t("owner.invoices.due", { date: day(inv.due_date) })}
        </span>
      </span>
      <span className="text-end">
        <span className="block tabular-nums text-base font-semibold text-slate-900">{money(Number(inv.amount))}</span>
        <span className="flex items-center justify-end gap-1.5">
          <Badge cls={STATUS_CLS[key] ?? STATUS_CLS.DRAFT}>{t(`owner.invoices.status.${key}`, { defaultValue: inv.status })}</Badge>
          {inv.refund_in_books === null ? (
            inv.in_books && <span className="text-[11px] text-emerald-700" title={t("owner.invoices.inBooks")}>✓ {t("owner.invoices.inBooksShort")}</span>
          ) : inv.refund_in_books ? (
            <span className="text-[11px] text-emerald-700">✓ {t("owner.invoices.refundInBooks")}</span>
          ) : (
            <span className="text-[11px] text-amber-700" title={t("owner.invoices.refundPendingHint")}>{t("owner.invoices.refundPending")}</span>
          )}
        </span>
      </span>
      <span className="flex items-center gap-2 basis-full sm:basis-auto justify-end">
        {inv.status === "DRAFT" && !preview && (discarding ? (
          <>
            <span className="text-xs text-rose-800">{t("owner.invoices.discardConfirm")}</span>
            <button disabled={busy} className={BTN_SMALL_PRIMARY}
                    onClick={() => void discardInvoice(inv.id).then(onView).catch(() => {}).finally(() => setDiscarding(false))}>
              {t("owner.invoices.discard")}
            </button>
            <button disabled={busy} onClick={() => setDiscarding(false)} className={BTN_SECONDARY}>{t("owner.invoices.keep")}</button>
          </>
        ) : confirming ? (
          <>
            <span className="text-xs text-slate-600">{t("owner.invoices.confirmSend", { email: inv.customer?.email })}</span>
            <button disabled={busy} onClick={onSend} className={BTN_SMALL_PRIMARY}>{t("owner.invoices.send")}</button>
            <button disabled={busy} onClick={onCancelSend} className={BTN_SECONDARY}>{t("owner.invoices.cancel")}</button>
          </>
        ) : (
          <>
            <button onClick={onAskSend} className={BTN_SMALL_PRIMARY}>{t("owner.invoices.send")}</button>
            <button disabled={busy} className={BTN_SECONDARY} title={t("owner.invoices.discardHint")} onClick={() => setDiscarding(true)}>
              {t("owner.invoices.discard")}
            </button>
          </>
        ))}
        {canCollect && (
          <button onClick={() => setCollecting(!collecting)} aria-expanded={collecting}
                  className={collecting ? BTN_SECONDARY : BTN_SMALL_PRIMARY}>
            {collecting ? t("owner.collect.close") : t("owner.collect.open")}
          </button>
        )}
        {inv.recipient_view_url && (
          <a href={inv.recipient_view_url} target="_blank" rel="noreferrer" className="text-xs font-medium text-brand-green hover:underline">
            {t("owner.invoices.view")}
          </a>
        )}
      </span>
      {canCollect && inv.reminder_count > 0 && !collecting && (
        <span className="basis-full text-end text-[11px] text-slate-500">{t("owner.collect.remindedShort", { count: inv.reminder_count })}</span>
      )}
      {canCollect && collecting && <InvoiceCollect inv={inv} busy={busy} onView={onView} autoQr={autoQr} />}
      {inv.offline && (
        <span className={`basis-full text-end text-[11px] ${inv.offline.in_bank ? "text-emerald-700" : "text-amber-700"}`}>
          {t(`owner.offline.line.${inv.offline.kind}`, { method: t(`owner.offline.methods.${inv.offline.method}`).toLowerCase(), date: day(inv.offline.day) })}
          {" · "}{t(inv.offline.in_bank ? "owner.offline.inBank" : "owner.offline.notInBank")}
        </span>
      )}
      {!preview && (PAID_ANY.includes(inv.status) || inv.status === "PARTIALLY_REFUNDED") && <RefundOffline inv={inv} busy={busy} onView={onView} />}
      {canCollect && inv.status !== "PARTIALLY_PAID" && !collecting && <CancelInvoice inv={inv} busy={busy} onView={onView} />}
    </li>
  );
}

/** Cancelar una factura enviada y sin cobrar (3-nov). PayPal no deja escribir el aviso: solo elegir si se manda. */
function CancelInvoice({ inv, busy, onView }: { inv: OwnerInvoice; busy: boolean; onView: (v: InvoicesView) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [notify, setNotify] = useState(true);
  const [working, setWorking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!open) return (
    <button onClick={() => setOpen(true)} className="basis-full text-end text-[11px] text-slate-500 hover:text-rose-700 hover:underline">
      {t("owner.invoices.cancelInvoice")}
    </button>
  );
  return (
    <div className="basis-full flex flex-wrap items-center justify-end gap-2 rounded-md bg-rose-50 p-2 text-xs">
      <span className="text-rose-800 basis-full text-end">{t("owner.invoices.cancelConfirm", { number: inv.number ?? inv.id })}</span>
      <label className="flex items-center gap-1.5 text-slate-700">
        <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />{t("owner.invoices.cancelNotify")}
      </label>
      <button disabled={busy || working} className={BTN_SMALL_PRIMARY} onClick={async () => {
        setWorking(true); setErr(null);
        try { onView(await cancelInvoice(inv.id, notify)); setOpen(false); }
        catch { setErr(t("owner.collect.err.other")); }
        finally { setWorking(false); }
      }}>{working ? "…" : t("owner.invoices.cancelDo")}</button>
      <button disabled={working} className={BTN_SECONDARY} onClick={() => setOpen(false)}>{t("owner.invoices.keep")}</button>
      {err && <span className="basis-full text-end text-rose-700">{err}</span>}
    </div>
  );
}

/** Devolver el dinero FUERA de PayPal (3-nov): PayPal la marca devuelta; la salida del banco queda como devolución. */
function RefundOffline({ inv, busy, onView }: { inv: OwnerInvoice; busy: boolean; onView: (v: InvoicesView) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState<OfflineMethod>("CHECK");
  const [d, setD] = useState(localToday());
  const [amount, setAmount] = useState("");     // vacío = todo lo cobrado sin devolver (5-nov: parciales)
  const [working, setWorking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!open) return (
    <button onClick={() => setOpen(true)} className="basis-full text-end text-[11px] text-slate-500 hover:text-slate-800 hover:underline">
      {t("owner.offline.refundOpen")}
    </button>
  );
  return (
    <div className="basis-full flex flex-wrap items-center justify-end gap-2 rounded-md bg-slate-50 p-2 text-xs">
      <span className="text-slate-600 basis-full text-end">{t("owner.offline.refundHint", { amount: money(Number(inv.amount)) })}</span>
      <select value={method} onChange={(e) => setMethod(e.target.value as OfflineMethod)} aria-label={t("owner.offline.method")}
              className="rounded-md border border-slate-300 px-2 py-1">
        {OFFLINE_METHODS.map((m) => <option key={m} value={m}>{t(`owner.offline.methods.${m}`)}</option>)}
      </select>
      <input type="date" value={d} max={localToday()} onChange={(e) => setD(e.target.value)} aria-label={t("owner.offline.day")}
             className="rounded-md border border-slate-300 px-2 py-1" />
      <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={t("owner.offline.refundAmountPh")}
             aria-label={t("owner.offline.amount")} className="w-32 rounded-md border border-slate-300 px-2 py-1 tabular-nums" />
      <button disabled={busy || working || !d} className={BTN_SMALL_PRIMARY} onClick={async () => {
        setWorking(true); setErr(null);
        try { onView(await recordOfflineRefund(inv.id, method, d, amount.trim())); setOpen(false); }
        catch { setErr(t("owner.collect.err.other")); }
        finally { setWorking(false); }
      }}>{working ? "…" : t("owner.offline.refundConfirm")}</button>
      <button disabled={working} className={BTN_SECONDARY} onClick={() => setOpen(false)}>{t("owner.invoices.cancel")}</button>
      {err && <span className="basis-full text-end text-rose-700">{err}</span>}
    </div>
  );
}

function Kpi({ label, value, accent, tone = "text-slate-900", tour, active = false, onClick }: {
  label: string; value: string; accent: string; tone?: string; tour?: string; active?: boolean; onClick?: () => void;
}) {
  return (
    <button type="button" data-tour={tour} onClick={onClick} aria-pressed={active}
            className={`text-left bg-white rounded-xl border border-t-4 ${accent} px-4 py-3 transition hover:shadow-sm
                        ${active ? "border-[#0070BA] ring-2 ring-sky-200" : "border-slate-200 hover:border-slate-300"}`}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-2xl font-semibold mt-0.5 ${tone}`}>{value}</div>
    </button>
  );
}

// Las tarjetas como filtros (8-nov). Las mismas reglas que las cifras del backend (`invoices.py`): por cobrar = enviadas sin
// cobrar del todo; vencidas = esas, pasada la fecha; cobrado = pagadas (por PayPal o fuera) en el año en curso.
type CardFilter = "outstanding" | "overdue" | "collected";
function matches(inv: OwnerInvoice, f: CardFilter): boolean {
  if (f === "outstanding") return OPEN.includes(inv.status);
  if (f === "overdue") return OPEN.includes(inv.status) && !!inv.overdue;
  return [...PAID_ANY, "PARTIALLY_REFUNDED"].includes(inv.status) && !!inv.paid_at
    && new Date(inv.paid_at).getFullYear() === new Date().getFullYear();
}

// Comisión de PayPal por factura en EE. UU. (✅ paypal.com/us/business/paypal-business-fees, 1-nov-2026): 3.49% + $0.49.
// Es una ESTIMACIÓN para el dueño (la real la trae PayPal y entra al libro aparte, en Merchant fees).
const FEE_RATE = 0.0349, FEE_FIXED = 0.49;

/** La revisión antes de enviar, con forma de FACTURA: quién emite, a quién, el renglón, el total y el vencimiento. */
function InvoicePreview({ business, customer, concept, amount, due, issued, profile, number, reference }: {
  business: string; customer?: Customer; concept: string; amount: string; due: string; issued: string;
  profile: InvoiceProfile | null; number: string; reference: string;
}) {
  const { t } = useTranslation();
  const total = Number(amount);
  const fee = Math.round((total * FEE_RATE + FEE_FIXED) * 100) / 100;
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 bg-slate-50 border-b border-slate-200">
        <div>
          {profile?.logo_path && <img src={logoSrc(profile.logo_path)} alt="" className="mb-2 h-auto max-h-[45px] w-auto max-w-[125px] object-contain" />}
          <div className="text-[11px] uppercase tracking-wide text-slate-500">{t("owner.invoices.from")}</div>
          <div className="text-base font-semibold text-slate-900">{business}</div>
          {profile && (
            <div className="text-xs text-slate-600 leading-snug">
              {profile.address_line && <div>{profile.address_line}</div>}
              {(profile.city || profile.state || profile.postal_code) && (
                <div>{[profile.city, [profile.state, profile.postal_code].filter(Boolean).join(" ")].filter(Boolean).join(", ")}</div>
              )}
              {profile.phone && <div>{formatPhone(profile.phone)}</div>}
              {profile.website && <div>{profile.website}</div>}
              {profile.tax_id && <div>{t("owner.profile.f.tax_id")}: {profile.tax_id}</div>}
            </div>
          )}
        </div>
        <div className="text-end">
          <div className="text-xl font-bold tracking-tight text-slate-900">{t("owner.invoices.docTitle")}</div>
          {number && <div className="text-xs font-medium text-slate-700">#{number}</div>}
          <div className="text-xs text-slate-500">{t("owner.invoices.issued", { date: issued })}</div>
          {reference && <div className="text-xs text-slate-500">{t("owner.invoices.referenceShort")}: {reference}</div>}
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
      {profile && (profile.note || profile.terms) && (
        <div className="px-5 py-3 border-t border-slate-100 grid gap-2 text-xs text-slate-600">
          {profile.note && <div><div className="text-[11px] uppercase tracking-wide text-slate-500">{t("owner.profile.f.note")}</div>{profile.note}</div>}
          {profile.terms && <div><div className="text-[11px] uppercase tracking-wide text-slate-500">{t("owner.profile.f.terms")}</div>{profile.terms}</div>}
        </div>
      )}
    </div>
  );
}
