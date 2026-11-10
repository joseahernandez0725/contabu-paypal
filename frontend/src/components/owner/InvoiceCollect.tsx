import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { invoiceQR, recordOfflinePayment, remindInvoice, reminderDraft, stopAutoReminders, type InvoicesView, type OfflineMethod,
         type OwnerInvoice, type ReminderDraft } from "../../api";
import { BTN_SECONDARY, BTN_SMALL_PRIMARY } from "../PageKit";
import AiTrace from "./AiTrace";

// COBRAR UNA FACTURA ABIERTA, en el portal del dueño (FASE 16, 1-nov; `backend/invoice_reminders.py`). Tres caminos:
//  1. El recordatorio OFICIAL de PayPal: PayPal manda su correo estándar (no acepta texto propio). Uno cada 24 h.
//  2. Un MENSAJE PERSONAL: Nemotron redacta un borrador con marcadores y ContaBu pone el cliente, el monto y el enlace
//     (la IA no los ve). El dueño lo edita y lo envía desde SU correo: ContaBu no envía nada por él.
//  3. El QR de pago, para cobrar en persona.
//  4. Ya me pagaron FUERA de PayPal (3-nov; `backend/invoice_offline.py`): PayPal la marca pagada y ContaBu NO crea venta:
//     la toma de la fila del banco cuando llegue (el efectivo queda declarado hasta que se deposite).

const REMIND_EVERY_MS = 24 * 3600 * 1000;
export const OFFLINE_METHODS: OfflineMethod[] = ["CASH", "CHECK", "BANK_TRANSFER", "WIRE_TRANSFER", "OTHER"];
export const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

/** `readOnly` (10-nov, Jose): en la visita guiada el panel se ve entero (el juez ve con qué se cobra) pero nada avanza:
 *  ni se envía, ni corre la IA, ni se pide el QR. Los botones quedan apagados y una línea dice dónde se prueba. */
export default function InvoiceCollect({ inv, busy, onView, autoQr = false, readOnly = false }: {
  inv: OwnerInvoice; busy: boolean; onView: (v: InvoicesView) => void; autoQr?: boolean; readOnly?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language?.startsWith("es") ? "es" : "en";
  const [working, setWorking] = useState<"" | "remind" | "draft" | "qr" | "offline" | "stop">("");
  const [stopConfirm, setStopConfirm] = useState(false);   // detener los automáticos de ESTA factura (5-nov)
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  // El tono lo SUGIERE el código con el historial de pago del cliente (5-nov); el dueño lo puede cambiar.
  const [tone, setTone] = useState<"friendly" | "firm">(inv.payer?.tone ?? (inv.overdue ? "firm" : "friendly"));
  const [msg, setMsg] = useState<{ subject: string; body: string } | null>(null);
  const [draftAi, setDraftAi] = useState<ReminderDraft | null>(null);   // para "What the AI did"
  const [copied, setCopied] = useState(false);
  const [qr, setQr] = useState<string | null>(null);
  const [paidOff, setPaidOff] = useState<{ method: OfflineMethod; day: string; note: string; amount: string; confirm: boolean }>(
    { method: "CHECK", day: localToday(), note: "", amount: "", confirm: false });

  const when = (iso: string) => new Date(iso).toLocaleString(lang, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  const nextAt = inv.last_reminder_at ? new Date(new Date(inv.last_reminder_at).getTime() + REMIND_EVERY_MS) : null;
  const waiting = nextAt !== null && nextAt.getTime() > Date.now();
  const off = busy || working !== "" || readOnly;

  async function go<T>(what: "remind" | "draft" | "qr" | "offline" | "stop", fn: () => Promise<T>, after: (x: T) => void) {
    setWorking(what); setErr(null);
    try { after(await fn()); }
    catch (e) {
      const c = e instanceof Error ? e.message : "";
      setErr(t(`owner.collect.err.${["reminded_recently", "ai_cap_reached", "ai_failed", "ai_unavailable", "invoice_not_open", "bad_day", "no_auto_reminders", "auto_reminders_already_stopped", "bad_amount"].includes(c) ? c : "other"}`));
    } finally { setWorking(""); }
  }

  // Cobro con tarjeta en persona (3-nov): la factura recién enviada abre directo con su QR en pantalla.
  useEffect(() => {
    if (autoQr) invoiceQR(inv.id).then((r) => setQr(r.png_base64)).catch(() => setErr(t("owner.collect.err.other")));
  }, [autoQr, inv.id, t]);

  const mailto = msg && inv.customer
    ? `mailto:${inv.customer.email}?subject=${encodeURIComponent(msg.subject)}&body=${encodeURIComponent(msg.body)}` : null;

  return (
    <div className="basis-full mt-1 rounded-lg border border-slate-200 bg-slate-50/60 p-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4 text-sm">
      {readOnly && (
        <p role="status" className="md:col-span-2 xl:col-span-4 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">{t("owner.collect.tourOnly")}</p>
      )}
      {/* 1. Recordatorio oficial de PayPal */}
      <section className="min-w-0 rounded-md bg-white border border-slate-200 p-3">
        <h4 className="font-semibold text-slate-900">{t("owner.collect.paypalTitle")}</h4>
        <p className="text-xs text-slate-500 mt-1">{t("owner.collect.paypalHint")}</p>
        {inv.reminder_count > 0 && inv.last_reminder_at && (
          <p className="text-xs text-slate-600 mt-2">{t("owner.collect.sentCount", { count: inv.reminder_count, when: when(inv.last_reminder_at) })}</p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {waiting && nextAt ? (
            <span className="text-xs text-slate-500">{t("owner.collect.nextAt", { when: when(nextAt.toISOString()) })}</span>
          ) : confirm ? (
            <>
              <span className="text-xs text-slate-600 basis-full">{t("owner.collect.paypalConfirm", { email: inv.customer?.email ?? "—" })}</span>
              <button disabled={off} className={BTN_SMALL_PRIMARY}
                      onClick={() => void go("remind", () => remindInvoice(inv.id), (v) => { onView(v); setConfirm(false); })}>
                {working === "remind" ? "…" : t("owner.collect.confirm")}
              </button>
              <button disabled={off} className={BTN_SECONDARY} onClick={() => setConfirm(false)}>{t("owner.invoices.cancel")}</button>
            </>
          ) : (
            <button disabled={off} className={BTN_SMALL_PRIMARY} onClick={() => setConfirm(true)}>{t("owner.collect.paypalSend")}</button>
          )}
        </div>
        {/* Los AUTOMÁTICOS de esta factura (reglas en Configuración): detenerlos no se puede deshacer en PayPal. */}
        <div className="mt-3 border-t border-dashed border-slate-200 pt-2 text-[11px]">
          {inv.auto_reminders_stopped ? (
            <span className="text-slate-500">{t("owner.collect.autoStopped")}</span>
          ) : stopConfirm ? (
            <span className="flex flex-wrap items-center gap-2">
              <span className="basis-full text-rose-800">{t("owner.collect.autoStopConfirm")}</span>
              <button disabled={off} className={BTN_SMALL_PRIMARY}
                      onClick={() => void go("stop", () => stopAutoReminders(inv.id), (v) => { onView(v); setStopConfirm(false); })}>
                {working === "stop" ? "…" : t("owner.collect.autoStop")}
              </button>
              <button disabled={off} className={BTN_SECONDARY} onClick={() => setStopConfirm(false)}>{t("owner.invoices.keep")}</button>
            </span>
          ) : (
            <button disabled={off} onClick={() => setStopConfirm(true)} className="text-slate-500 hover:text-rose-700 hover:underline">{t("owner.collect.autoStop")}</button>
          )}
        </div>
      </section>

      {/* 2. Mensaje personal: la IA redacta, ContaBu completa, el dueño revisa y envía desde su correo */}
      <section className="min-w-0 rounded-md bg-white border border-slate-200 p-3">
        <h4 className="font-semibold text-slate-900">{t("owner.collect.personalTitle")}</h4>
        <p className="text-xs text-slate-500 mt-1">{t("owner.collect.personalHint")}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-md ring-1 ring-slate-200 overflow-hidden text-xs" role="group">
            {(["friendly", "firm"] as const).map((k) => (
              <button key={k} onClick={() => setTone(k)} aria-pressed={tone === k}
                      className={`px-2.5 py-1 ${tone === k ? "bg-slate-800 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}>
                {t(`owner.collect.${k}`)}
              </button>
            ))}
          </div>
          <button disabled={off} className={BTN_SECONDARY}
                  onClick={() => void go("draft", () => reminderDraft(inv.id, lang, tone), (r) => { setMsg({ subject: r.subject, body: r.body }); setDraftAi(r); setCopied(false); })}>
            {working === "draft" ? t("owner.collect.drafting") : t(msg ? "owner.collect.redraft" : "owner.collect.draft")}
          </button>
        </div>
        {inv.payer && inv.payer.kind !== "new" && (
          <p className="mt-1.5 text-[11px] text-slate-500">{t(`owner.collect.payer.${inv.payer.kind}`, { late: inv.payer.late, paid: inv.payer.paid })}</p>
        )}
        {msg && (
          <div className="mt-3 grid gap-2">
            <label className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.collect.subject")}
              <input value={msg.subject} onChange={(e) => setMsg({ ...msg, subject: e.target.value })}
                     className="mt-1 w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm font-normal normal-case tracking-normal text-slate-900" />
            </label>
            <label className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.collect.body")}
              <textarea value={msg.body} rows={7} onChange={(e) => setMsg({ ...msg, body: e.target.value })}
                        className="mt-1 w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm font-normal normal-case tracking-normal text-slate-900" />
            </label>
            <div className="flex flex-wrap gap-2">
              {mailto && <a href={mailto} className={BTN_SMALL_PRIMARY}>{t("owner.collect.openMail")}</a>}
              <button className={BTN_SECONDARY} onClick={() => {
                void navigator.clipboard?.writeText(`${msg.subject}\n\n${msg.body}`).then(() => setCopied(true)).catch(() => {});
              }}>{copied ? t("owner.collect.copied") : t("owner.collect.copy")}</button>
            </div>
            <AiTrace ai={draftAi?.ai} steps={[
              { label: t("owner.ai.reminder.tone", { tone: t(`owner.collect.${draftAi?.tone ?? tone}`) }),
                detail: draftAi?.payer ? t(`owner.collect.payer.${draftAi.payer.kind}`, { late: draftAi.payer.late, paid: draftAi.payer.paid }) : undefined },
              { label: t("owner.ai.reminder.read"), detail: draftAi?.sent_to_ai },
              { label: t("owner.ai.reminder.filled") },
              { label: t("owner.ai.voice.waits", { tool: draftAi?.ai?.waits_for_owner ?? "send_invoice_reminder" }), tone: "wait" },
            ]} />
          </div>
        )}
      </section>

      {/* 3. QR de pago (cobro en persona) */}
      <section className="min-w-0 rounded-md bg-white border border-slate-200 p-3">
        <h4 className="font-semibold text-slate-900">{t("owner.collect.qrTitle")}</h4>
        <p className="text-xs text-slate-500 mt-1">{t("owner.collect.qrHint")}</p>
        {qr ? (
          <div className="mt-3 flex flex-col items-center gap-2">
            <img src={`data:image/png;base64,${qr}`} alt={t("owner.collect.qrAlt", { number: inv.number ?? inv.id })}
                 className="size-40 rounded border border-slate-200" />
            <a href={`data:image/png;base64,${qr}`} download={`invoice-${inv.number ?? inv.id}-qr.png`}
               className="text-xs font-medium text-brand-green hover:underline">{t("owner.collect.qrDownload")}</a>
          </div>
        ) : (
          <button disabled={off} className={`${BTN_SECONDARY} mt-3`}
                  onClick={() => void go("qr", () => invoiceQR(inv.id), (r) => setQr(r.png_base64))}>
            {working === "qr" ? "…" : t("owner.collect.qrShow")}
          </button>
        )}
      </section>

      {/* 4. Ya me pagaron fuera de PayPal: PayPal la marca pagada; la venta sale del depósito del banco (no se duplica). */}
      <section className="min-w-0 rounded-md bg-white border border-slate-200 p-3">
        <h4 className="font-semibold text-slate-900">{t("owner.offline.title")}</h4>
        <p className="text-xs text-slate-500 mt-1">{t("owner.offline.hint")}</p>
        <div className="mt-3 grid gap-2">
          <select value={paidOff.method} onChange={(e) => setPaidOff({ ...paidOff, method: e.target.value as OfflineMethod, confirm: false })}
                  className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" aria-label={t("owner.offline.method")}>
            {OFFLINE_METHODS.map((m) => <option key={m} value={m}>{t(`owner.offline.methods.${m}`)}</option>)}
          </select>
          <input type="date" value={paidOff.day} max={localToday()} aria-label={t("owner.offline.day")}
                 onChange={(e) => setPaidOff({ ...paidOff, day: e.target.value, confirm: false })}
                 className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
          <input inputMode="decimal" value={paidOff.amount} placeholder={t("owner.offline.amountPh")} aria-label={t("owner.offline.amount")}
                 onChange={(e) => setPaidOff({ ...paidOff, amount: e.target.value, confirm: false })}
                 className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm tabular-nums" />
          <input value={paidOff.note} maxLength={500} placeholder={t("owner.offline.notePh")}
                 onChange={(e) => setPaidOff({ ...paidOff, note: e.target.value })}
                 className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
          {paidOff.confirm ? (
            <>
              <span className="text-xs text-slate-600">{t(paidOff.amount.trim() ? "owner.offline.confirmPart" : "owner.offline.confirmPay", { method: t(`owner.offline.methods.${paidOff.method}`).toLowerCase(), amount: paidOff.amount.trim() })}</span>
              <div className="flex gap-2">
                <button disabled={off || !paidOff.day} className={BTN_SMALL_PRIMARY}
                        onClick={() => void go("offline", () => recordOfflinePayment(inv.id, paidOff.method, paidOff.day, paidOff.note, paidOff.amount.trim()), onView)}>
                  {working === "offline" ? "…" : t("owner.collect.confirm")}
                </button>
                <button disabled={off} className={BTN_SECONDARY} onClick={() => setPaidOff({ ...paidOff, confirm: false })}>{t("owner.invoices.cancel")}</button>
              </div>
            </>
          ) : (
            <button disabled={off || !paidOff.day} className={`${BTN_SECONDARY} justify-self-start`}
                    onClick={() => setPaidOff({ ...paidOff, confirm: true })}>{t("owner.offline.markPaid")}</button>
          )}
        </div>
      </section>

      {err && <p className="md:col-span-2 xl:col-span-4 text-xs text-rose-700">{err}</p>}
    </div>
  );
}
