import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { addContractor, deleteContractor, editContractor, getContractors, money, payContractor, refreshContractorPayments, type Contractor,
         type ContractorsView } from "../../api";
import { BTN_GHOST, BTN_PRIMARY, BTN_SECONDARY, BTN_SMALL_PRIMARY, Badge } from "../PageKit";

// CONTRATISTAS, en el portal del dueño (FASE 16, 4-nov; `backend/contractor_payouts.py`, detrás de `PAYPAL_PAYOUTS`).
// El dueño le paga a un ayudante o subcontratista desde su saldo de PayPal (Payouts), con confirmación. Al confirmarse,
// entra solo al libro como costo (COGS · Subcontractors) y la comisión aparte. Lo pagado por PayPal no va en el 1099-NEC
// del dueño (lo reporta PayPal en el 1099-K): la pantalla lo dice. Lo pagado en el año lo suma el código.

const STATUS_CLS: Record<string, string> = {
  SUCCESS: "bg-emerald-50 text-emerald-700 ring-emerald-200", PENDING: "bg-sky-50 text-sky-700 ring-sky-200",
  UNCLAIMED: "bg-amber-50 text-amber-700 ring-amber-200", ONHOLD: "bg-amber-50 text-amber-700 ring-amber-200",
};
const BAD = "bg-rose-50 text-rose-700 ring-rose-200";

export default function OwnerContractors({ preview }: { preview: boolean }) {
  const { t, i18n } = useTranslation();
  const [v, setV] = useState<ContractorsView | null>(null);
  const [adding, setAdding] = useState<{ name: string; email: string } | null>(null);
  const [editing, setEditing] = useState<{ id: number; name: string; email: string } | null>(null);   // 5-nov
  const [removing, setRemoving] = useState<number | null>(null);
  const [paying, setPaying] = useState<{ c: Contractor; amount: string; memo: string; confirm: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const day = (iso: string) => new Date(iso).toLocaleDateString(i18n.language || "en", { day: "numeric", month: "short", year: "numeric" });

  useEffect(() => {
    getContractors().then((x) => { setV(x); if (x.enabled && !preview) refreshContractorPayments().then(setV).catch(() => {}); }).catch(() => {});
  }, [preview]);

  async function run(fn: () => Promise<ContractorsView>, after?: () => void) {
    setBusy(true); setErr(null);
    try { setV(await fn()); after?.(); }
    catch (e) {
      const c = e instanceof Error ? e.message : "";
      setErr(t(`owner.contractors.err.${["insufficient_balance", "bad_amount", "name_and_email_required", "has_payments"].includes(c) ? c : "other"}`));
    } finally { setBusy(false); }
  }

  if (!v || !v.enabled) return null;
  const validAmount = paying && /^\d+(\.\d{1,2})?$/.test(paying.amount.trim()) && Number(paying.amount) > 0;
  return (
    <div data-tour="pp-contractors" className="space-y-3">
      <p className="text-sm text-slate-600">{t("owner.contractors.intro")}</p>
      {/* El botón principal, a la vista como "+ Nueva factura" en Cobros (Jose, 4-nov: "que se vea más claro"). */}
      {!preview && !adding && (
        <button onClick={() => setAdding({ name: "", email: "" })} className={BTN_PRIMARY}>+ {t("owner.contractors.add")}</button>
      )}

      <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-3 py-2 border-b border-slate-100 flex items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.contractors.listTitle")}</span>
        </div>
        {adding && (
          <div className="px-3 py-2 flex flex-wrap items-center gap-2 border-b border-slate-100 text-sm">
            <input value={adding.name} placeholder={t("owner.contractors.name")} onChange={(e) => setAdding({ ...adding, name: e.target.value })}
                   className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1" />
            <input value={adding.email} placeholder={t("owner.contractors.email")} type="email" onChange={(e) => setAdding({ ...adding, email: e.target.value })}
                   className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1" />
            <button disabled={busy || !adding.name.trim() || !adding.email.trim()} className={BTN_SMALL_PRIMARY}
                    onClick={() => void run(() => addContractor(adding.name, adding.email), () => setAdding(null))}>{t("owner.contractors.save")}</button>
            <button disabled={busy} className={BTN_SECONDARY} onClick={() => setAdding(null)}>{t("owner.invoices.cancel")}</button>
            <span className="basis-full text-[11px] text-slate-500">{t("owner.contractors.emailHint")}</span>
          </div>
        )}
        {v.contractors.length === 0 ? <p className="px-3 py-4 text-xs text-slate-500">{t("owner.contractors.none")}</p> : (
          <ul className="divide-y divide-slate-100">
            {v.contractors.map((c) => (
              <li key={c.id} className="px-3 py-2.5 text-sm">
                {editing?.id === c.id ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} aria-label={t("owner.contractors.name")}
                           className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1" />
                    <input value={editing.email} type="email" onChange={(e) => setEditing({ ...editing, email: e.target.value })} aria-label={t("owner.contractors.email")}
                           className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1" />
                    <button disabled={busy || !editing.name.trim() || !editing.email.trim()} className={BTN_SMALL_PRIMARY}
                            onClick={() => void run(() => editContractor(c.id, editing.name, editing.email), () => setEditing(null))}>{t("owner.contractors.save")}</button>
                    <button disabled={busy} className={BTN_SECONDARY} onClick={() => setEditing(null)}>{t("owner.invoices.cancel")}</button>
                    {c.has_payments && <span className="basis-full text-[11px] text-slate-500">{t("owner.contractors.editHint")}</span>}
                  </div>
                ) : (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="min-w-0 flex-1">
                    <span className="block font-semibold text-slate-900 truncate">{c.name}</span>
                    <span className="block text-xs text-slate-500 truncate">{c.email}</span>
                    {!preview && (
                      <span className="flex gap-3 text-[11px]">
                        <button onClick={() => { setEditing({ id: c.id, name: c.name, email: c.email }); setRemoving(null); }} className="text-slate-500 hover:text-slate-800 hover:underline">{t("owner.contractors.edit")}</button>
                        {!c.has_payments && (removing === c.id ? (
                          <span className="flex gap-2 items-center text-rose-800">{t("owner.contractors.removeConfirm")}
                            <button disabled={busy} onClick={() => void run(() => deleteContractor(c.id), () => setRemoving(null))} className="font-semibold hover:underline">{t("owner.contractors.remove")}</button>
                            <button onClick={() => setRemoving(null)} className="text-slate-500 hover:underline">{t("owner.invoices.keep")}</button>
                          </span>
                        ) : (
                          <button onClick={() => setRemoving(c.id)} className="text-slate-500 hover:text-rose-700 hover:underline">{t("owner.contractors.remove")}</button>
                        ))}
                      </span>
                    )}
                  </span>
                  <span className="text-end text-xs text-slate-500">
                    {t("owner.contractors.paidYear")}<br /><span className="tabular-nums text-sm font-semibold text-slate-900">{money(Number(c.paid_this_year))}</span>
                  </span>
                  {!preview && paying?.c.id !== c.id && (
                    <button onClick={() => setPaying({ c, amount: "", memo: "", confirm: false })} className={BTN_SMALL_PRIMARY}>{t("owner.contractors.pay")}</button>
                  )}
                </div>
                )}
                {paying?.c.id === c.id && (
                  <div className="mt-2 rounded-lg bg-slate-50 p-2 flex flex-wrap items-center gap-2">
                    <input inputMode="decimal" placeholder="0.00" value={paying.amount} disabled={paying.confirm}
                           onChange={(e) => setPaying({ ...paying, amount: e.target.value })}
                           className="w-28 rounded-md border border-slate-300 px-2 py-1 tabular-nums" />
                    <input value={paying.memo} maxLength={200} placeholder={t("owner.contractors.memo")} disabled={paying.confirm}
                           onChange={(e) => setPaying({ ...paying, memo: e.target.value })}
                           className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1" />
                    {paying.confirm ? (
                      <>
                        <span className="basis-full text-xs text-slate-700">{t("owner.contractors.confirm", { amount: money(Number(paying.amount)), name: c.name, email: c.email })}</span>
                        <button disabled={busy} className={BTN_PRIMARY}
                                onClick={() => void run(() => payContractor(c.id, paying.amount.trim(), paying.memo), () => {
                                  setPaying(null);
                                  setTimeout(() => { refreshContractorPayments().then(setV).catch(() => {}); }, 20000);   // PayPal confirma en ~15 s
                                })}>{busy ? "…" : t("owner.contractors.payNow")}</button>
                        <button disabled={busy} className={BTN_SECONDARY} onClick={() => setPaying({ ...paying, confirm: false })}>{t("owner.invoices.edit")}</button>
                      </>
                    ) : (
                      <>
                        <button disabled={!validAmount} className={BTN_SMALL_PRIMARY} onClick={() => setPaying({ ...paying, confirm: true })}>{t("owner.invoices.review")}</button>
                        <button className={BTN_SECONDARY} onClick={() => setPaying(null)}>{t("owner.invoices.cancel")}</button>
                      </>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {err && <p className="px-3 pb-2 text-xs text-rose-700">{err}</p>}
      </section>

      {v.payments.length > 0 && (
        <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-3 py-2 border-b border-slate-100 flex items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.contractors.paymentsTitle")}</span>
            <span className="flex-1" />
            {!preview && <button disabled={busy} onClick={() => void run(refreshContractorPayments)} className={BTN_GHOST}>{t("owner.disputes.refresh")}</button>}
          </div>
          <ul className="divide-y divide-slate-100">
            {v.payments.map((p) => (
              <li key={p.id} className="px-3 py-2 text-sm flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-slate-900 truncate">{p.contractor ?? "—"}{p.memo ? ` · ${p.memo}` : ""}</span>
                  <span className="block text-xs text-slate-500">{day(p.date)}{p.fee ? ` · ${t("owner.contractors.fee", { fee: money(Number(p.fee)) })}` : ""}</span>
                </span>
                <span className="text-end">
                  <span className="block tabular-nums font-semibold text-slate-900">{money(Number(p.amount))}</span>
                  <span className="flex items-center justify-end gap-1.5">
                    <Badge cls={STATUS_CLS[p.status] ?? BAD}>{t(`owner.contractors.status.${p.status}`, { defaultValue: p.status })}</Badge>
                    {p.in_books && <span className="text-[11px] text-emerald-700">✓ {t("owner.invoices.inBooksShort")}</span>}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="text-xs text-slate-500">{t("owner.contractors.taxNote")}</p>
    </div>
  );
}
