import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cancelSeries, editSeries, money, type InvoiceSeries, type PaymentTerm } from "../../api";
import { BTN_SECONDARY, BTN_SMALL_PRIMARY, Badge } from "../PageKit";

// FACTURAS RECURRENTES, en Cobros (FASE 16, 4-nov; `backend/invoice_series.py`). Cada serie: a quién, qué, cuánto, cada
// cuánto, cuántas van y la próxima fecha. PayPal genera y envía cada factura sola; aparecen en la lista de facturas con la
// marca "Recurrente". Detenerla (solo el dueño, con confirmación) no toca las facturas ya enviadas.

const TERMS: PaymentTerm[] = ["DUE_ON_RECEIPT", "NET_10", "NET_15", "NET_30"];

const STATUS_CLS: Record<string, string> = {
  ACTIVE: "bg-emerald-50 text-emerald-700 ring-emerald-200", DRAFT: "bg-slate-50 text-slate-600 ring-slate-200",
  CANCELLED: "bg-slate-50 text-slate-500 ring-slate-200", COMPLETED: "bg-sky-50 text-sky-700 ring-sky-200",
};

export function everyText(t: (k: string, o?: Record<string, unknown>) => string, unit: string, count: number): string {
  return t(`owner.series.every.${unit}`, { count });
}

export default function SeriesList({ series, preview, onChange }: {
  series: InvoiceSeries[]; preview: boolean; onChange: (s: InvoiceSeries[]) => void;
}) {
  const { t, i18n } = useTranslation();
  const [confirm, setConfirm] = useState<number | null>(null);
  // Editar las PRÓXIMAS facturas (5-nov): concepto, monto y plazo. PayPal no tiene "pausar".
  const [editing, setEditing] = useState<{ id: number; concept: string; amount: string; term: PaymentTerm } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const day = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(i18n.language || "en", { day: "numeric", month: "short", year: "numeric" });
  if (series.length === 0) return null;
  return (
    <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="px-3 py-2 border-b border-slate-100 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.series.title")}</div>
      <ul className="divide-y divide-slate-100">
        {series.map((x) => (
          <li key={x.id} className="px-3 py-2.5 text-sm flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="min-w-0 flex-1">
              <span className="block font-semibold text-slate-900 truncate">{x.customer?.name ?? "—"} · {x.concept}</span>
              <span className="block text-xs text-slate-500">
                {everyText(t, x.interval_unit, x.interval_count)} · {x.total_cycles ? t("owner.series.progress", { done: x.completed_cycles, total: x.total_cycles })
                  : t("owner.series.progressOpen", { done: x.completed_cycles })}
                {x.next_date && ` · ${t("owner.series.next", { date: day(x.next_date) })}`}
              </span>
            </span>
            <span className="text-end">
              <span className="block tabular-nums font-semibold text-slate-900">{money(Number(x.amount))}</span>
              <Badge cls={STATUS_CLS[x.status] ?? STATUS_CLS.DRAFT}>{t(`owner.series.status.${x.status}`, { defaultValue: x.status })}</Badge>
            </span>
            {editing?.id === x.id && (
              <span className="basis-full grid gap-2 sm:grid-cols-[1fr_auto_auto_auto_auto] items-center text-sm">
                <input value={editing.concept} maxLength={200} onChange={(e) => setEditing({ ...editing, concept: e.target.value })}
                       aria-label={t("owner.invoices.concept")} className="min-w-0 rounded-md border border-slate-300 px-2 py-1" />
                <input inputMode="decimal" value={editing.amount} onChange={(e) => setEditing({ ...editing, amount: e.target.value })}
                       aria-label={t("owner.invoices.amount")} className="w-28 rounded-md border border-slate-300 px-2 py-1 tabular-nums" />
                <select value={editing.term} onChange={(e) => setEditing({ ...editing, term: e.target.value as PaymentTerm })}
                        aria-label={t("owner.series.term")} className="rounded-md border border-slate-300 px-2 py-1">
                  {TERMS.map((k) => <option key={k} value={k}>{t(`owner.series.terms.${k}`)}</option>)}
                </select>
                <button disabled={busy || !editing.concept.trim() || !/^\d+(\.\d{1,2})?$/.test(editing.amount.trim())} className={BTN_SMALL_PRIMARY}
                        onClick={async () => {
                          setBusy(true); setErr(null);
                          try { onChange((await editSeries(x.id, editing.concept, editing.amount.trim(), editing.term)).series); setEditing(null); }
                          catch { setErr(t("owner.collect.err.other")); }
                          finally { setBusy(false); }
                        }}>{t("owner.contractors.save")}</button>
                <button disabled={busy} className={BTN_SECONDARY} onClick={() => setEditing(null)}>{t("owner.invoices.cancel")}</button>
                <span className="sm:col-span-5 text-[11px] text-slate-500">{t("owner.series.editHint")}</span>
              </span>
            )}
            {!preview && editing?.id !== x.id && (x.status === "ACTIVE" || x.status === "DRAFT") && confirm !== x.id && (
              <button onClick={() => { setEditing({ id: x.id, concept: x.concept, amount: x.amount, term: x.payment_term }); setConfirm(null); }}
                      className="text-[11px] text-slate-500 hover:text-slate-800 hover:underline">{t("owner.contractors.edit")}</button>
            )}
            {!preview && editing?.id !== x.id && (x.status === "ACTIVE" || x.status === "DRAFT") && (confirm === x.id ? (
              <span className="basis-full flex flex-wrap items-center justify-end gap-2 text-xs">
                <span className="text-rose-800">{t("owner.series.stopConfirm")}</span>
                <button disabled={busy} className={BTN_SMALL_PRIMARY} onClick={async () => {
                  setBusy(true); setErr(null);
                  try { onChange((await cancelSeries(x.id)).series); setConfirm(null); }
                  catch { setErr(t("owner.collect.err.other")); }
                  finally { setBusy(false); }
                }}>{t("owner.series.stop")}</button>
                <button disabled={busy} className={BTN_SECONDARY} onClick={() => setConfirm(null)}>{t("owner.invoices.keep")}</button>
              </span>
            ) : (
              <button onClick={() => setConfirm(x.id)} className="text-[11px] text-slate-500 hover:text-rose-700 hover:underline">{t("owner.series.stop")}</button>
            ))}
          </li>
        ))}
      </ul>
      {err && <p className="px-3 pb-2 text-xs text-rose-700">{err}</p>}
    </section>
  );
}
