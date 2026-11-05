import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { DISPUTE_EVIDENCE, acceptDispute, evidenceDispute, getDisputes, messageDispute, money, offerDispute, refreshDisputes,
         simulateDispute, summarizeDispute, type Dispute, type DisputesView } from "../../api";
import { BTN_GHOST, BTN_SECONDARY, BTN_SMALL_PRIMARY, Badge } from "../PageKit";

// DISPUTAS DE PAYPAL, en Cobros del portal del dueño (FASE 16, 3-nov; `backend/paypal_disputes.py`). Lo que está en
// disputa se declara arriba (lo suma SQL). La IA RESUME el caso (no decide ni recomienda; no ve nombres ni montos) y el
// resumen queda guardado. El dueño decide: aceptar la reclamación (con confirmación) o escribirle al cliente. Las
// pruebas con archivos se suben en PayPal. En sandbox, unos botones simulan lo que haría PayPal (para probar y el video).
// En la vista previa y en la visita todo se ve, pero nada se envía ni se llama a la IA.

const STATUS_CLS: Record<string, string> = {
  OPEN: "bg-amber-50 text-amber-700 ring-amber-200",
  WAITING_FOR_SELLER_RESPONSE: "bg-rose-50 text-rose-700 ring-rose-200",
  WAITING_FOR_BUYER_RESPONSE: "bg-sky-50 text-sky-700 ring-sky-200",
  UNDER_REVIEW: "bg-violet-50 text-violet-700 ring-violet-200",
  RESOLVED: "bg-slate-50 text-slate-600 ring-slate-200",
  OTHER: "bg-slate-50 text-slate-600 ring-slate-200",
};

export default function OwnerDisputes({ preview }: { preview: boolean }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language?.startsWith("es") ? "es" : "en";
  const [v, setV] = useState<DisputesView | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  useEffect(() => {
    getDisputes(lang).then(setV).catch(() => {});
    if (!preview) refreshDisputes(lang).then(setV).catch(() => {});   // en local, en lugar del webhook
  }, [preview, lang]);

  async function run(fn: () => Promise<DisputesView>) {
    setBusy(true); setErr(null);
    try { setV(await fn()); }
    catch (e) {
      const c = e instanceof Error ? e.message : "";
      setErr(t(`owner.disputes.err.${["ai_cap_reached", "ai_failed", "ai_unavailable", "dispute_not_actionable", "bad_message", "paypal_not_connected", "bad_amount", "bad_evidence_type", "bad_evidence_file", "evidence_empty"].includes(c) ? c : "other"}`));
    } finally { setBusy(false); }
  }

  if (!v) return null;
  const n = v.summary.open;
  return (
    <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="px-3 py-2 border-b border-slate-100 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.disputes.title")}</span>
        {n > 0 && (
          <span className="text-xs font-semibold text-rose-700">
            {t("owner.disputes.inDispute", { count: n, amount: money(Number(v.summary.in_dispute)) })}
          </span>
        )}
        <span className="flex-1" />
        {!preview && <button onClick={() => void run(() => refreshDisputes(lang))} disabled={busy} className={BTN_GHOST}>{t("owner.disputes.refresh")}</button>}
      </div>
      {err && <p className="px-3 pt-2 text-xs text-rose-700">{err}</p>}
      {v.disputes.length === 0 ? (
        <p className="px-3 py-4 text-xs text-slate-500">{t("owner.disputes.none")}</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {v.disputes.map((d) => (
            <Row key={d.id} d={d} lang={lang} preview={preview} busy={busy} open={openId === d.id}
                 onToggle={() => setOpenId(openId === d.id ? null : d.id)} run={run} />
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({ d, lang, preview, busy, open, onToggle, run }: {
  d: Dispute; lang: string; preview: boolean; busy: boolean; open: boolean; onToggle: () => void;
  run: (fn: () => Promise<DisputesView>) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [confirm, setConfirm] = useState(false);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const [offer, setOffer] = useState<{ amount: string; note: string; confirm: boolean } | null>(null);        // 5-nov
  const [ev, setEv] = useState<{ type: string; notes: string; file: File | null } | null>(null);
  const when = (iso: string) => new Date(iso).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
  const closed = d.status === "RESOLVED";
  const who = d.invoice?.customer ?? t("owner.disputes.unknownCustomer");
  return (
    <li className={`px-3 py-3 text-sm border-s-4 ${closed ? "border-s-slate-200" : "border-s-rose-500"}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="min-w-0 flex-1">
          <span className="block font-semibold text-slate-900 truncate">{who}</span>
          <span className="block text-xs text-slate-500 truncate">
            {d.invoice?.number ? `#${d.invoice.number} · ` : ""}{t(`owner.disputes.reason.${d.reason}`, { defaultValue: d.reason })}
            {d.opened_at ? ` · ${t("owner.disputes.opened", { date: when(d.opened_at) })}` : ""}
          </span>
        </span>
        <span className="text-end">
          <span className="block tabular-nums text-base font-semibold text-slate-900">{money(Number(d.amount))}</span>
          <span className="flex items-center justify-end gap-1.5">
            <Badge cls={STATUS_CLS[d.status] ?? STATUS_CLS.OTHER}>{t(`owner.disputes.status.${d.status}`, { defaultValue: d.status })}</Badge>
            {d.stage && <span className="text-[11px] text-slate-500">{t(`owner.disputes.stage.${d.stage}`, { defaultValue: d.stage })}</span>}
          </span>
        </span>
        <button onClick={onToggle} aria-expanded={open} className={open ? BTN_SECONDARY : BTN_SMALL_PRIMARY}>
          {open ? t("owner.collect.close") : t("owner.disputes.open")}
        </button>
      </div>
      {!closed && d.seller_response_due && (
        <p className="mt-1 text-xs text-rose-700">{t("owner.disputes.dueBy", { date: when(d.seller_response_due) })}</p>
      )}
      {closed && d.outcome && (
        <p className="mt-1 text-xs text-slate-600">{t(`owner.disputes.outcome.${d.outcome}`, { defaultValue: d.outcome })}</p>
      )}

      {open && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50/60 p-3 grid gap-3 md:grid-cols-2 text-sm">
          {/* El resumen de la IA: explica, no decide. Guardado; la visita lo lee sin gastar créditos. */}
          <section className="min-w-0 rounded-md bg-white border border-slate-200 p-3 md:col-span-2">
            <h4 className="font-semibold text-slate-900">{t("owner.disputes.aiTitle")}</h4>
            <p className="text-xs text-slate-500 mt-0.5">{t("owner.disputes.aiHint")}</p>
            {d.summary ? (
              <dl className="mt-2 grid gap-2 text-sm">
                <div><dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.disputes.aiSummary")}</dt><dd className="text-slate-800">{d.summary.summary}</dd></div>
                <div><dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.disputes.aiCustomer")}</dt><dd className="text-slate-800">{d.summary.customer_says}</dd></div>
                <div><dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.disputes.aiOptions")}</dt><dd className="text-slate-800">{d.summary.options}</dd></div>
              </dl>
            ) : preview ? (
              <p className="mt-2 text-xs text-slate-500">{t("owner.disputes.aiNone")}</p>
            ) : (
              <button disabled={busy} className={`${BTN_SECONDARY} mt-2`} onClick={() => void run(() => summarizeDispute(d.id, lang))}>
                {busy ? t("owner.disputes.aiWorking") : t("owner.disputes.aiRun")}
              </button>
            )}
          </section>

          {/* Lo que se escribieron el cliente y el negocio en PayPal. */}
          <section className="min-w-0 rounded-md bg-white border border-slate-200 p-3">
            <h4 className="font-semibold text-slate-900">{t("owner.disputes.messages")}</h4>
            {d.messages.length === 0 ? <p className="mt-1 text-xs text-slate-500">{t("owner.disputes.noMessages")}</p> : (
              <ul className="mt-2 space-y-2">
                {d.messages.map((m, i) => (
                  <li key={i} className="text-xs">
                    <span className="font-semibold text-slate-700">{t(`owner.disputes.by.${m.by ?? "OTHER"}`, { defaultValue: m.by ?? "" })}</span>
                    {m.at && <span className="text-slate-400"> · {when(m.at)}</span>}
                    <p className="text-slate-700 whitespace-pre-wrap break-words">{m.text}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Lo que decide el dueño. */}
          <section className="min-w-0 rounded-md bg-white border border-slate-200 p-3 space-y-3">
            <h4 className="font-semibold text-slate-900">{t("owner.disputes.decide")}</h4>
            {preview ? <p className="text-xs text-slate-500">{t("owner.preview.disabled")}</p> : closed ? (
              <p className="text-xs text-slate-500">{t("owner.disputes.closedNote")}</p>
            ) : (
              <>
                {d.can_message && (
                  <div className="grid gap-1.5">
                    <textarea value={msg} rows={3} onChange={(e) => setMsg(e.target.value)} placeholder={t("owner.disputes.messagePh")}
                              className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
                    <button disabled={busy || !msg.trim()} className={`${BTN_SECONDARY} justify-self-start`}
                            onClick={() => void run(() => messageDispute(d.id, msg, lang)).then(() => setMsg(""))}>
                      {t("owner.disputes.send")}
                    </button>
                  </div>
                )}
                {d.can_accept && (confirm ? (
                  <div className="grid gap-1.5 rounded-md bg-rose-50 p-2">
                    <p className="text-xs text-rose-800">{t("owner.disputes.acceptConfirm", { amount: money(Number(d.amount)) })}</p>
                    <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("owner.disputes.notePh")}
                           className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
                    <div className="flex gap-2">
                      <button disabled={busy} className={BTN_SMALL_PRIMARY} onClick={() => void run(() => acceptDispute(d.id, note, lang)).then(() => setConfirm(false))}>
                        {t("owner.collect.confirm")}
                      </button>
                      <button disabled={busy} className={BTN_SECONDARY} onClick={() => setConfirm(false)}>{t("owner.invoices.cancel")}</button>
                    </div>
                  </div>
                ) : (
                  <button disabled={busy} className={BTN_SECONDARY} onClick={() => setConfirm(true)}>{t("owner.disputes.accept")}</button>
                ))}
                {d.can_offer && (offer ? (
                  <div className="grid gap-1.5 rounded-md bg-slate-50 p-2">
                    <span className="text-xs font-semibold text-slate-700">{t("owner.disputes.offerTitle")}</span>
                    <div className="flex flex-wrap gap-2">
                      <input inputMode="decimal" value={offer.amount} disabled={offer.confirm} placeholder="0.00" onChange={(e) => setOffer({ ...offer, amount: e.target.value })}
                             aria-label={t("owner.disputes.offerAmount")} className="w-28 rounded-md border border-slate-300 px-2 py-1 text-sm tabular-nums" />
                      <input value={offer.note} disabled={offer.confirm} maxLength={2000} placeholder={t("owner.disputes.notePh")}
                             onChange={(e) => setOffer({ ...offer, note: e.target.value })} className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm" />
                    </div>
                    {offer.confirm ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="basis-full text-xs text-slate-700">{t("owner.disputes.offerConfirm", { amount: money(Number(offer.amount)) })}</span>
                        <button disabled={busy} className={BTN_SMALL_PRIMARY} onClick={() => void run(() => offerDispute(d.id, offer.amount.trim(), offer.note, lang)).then(() => setOffer(null))}>{t("owner.disputes.offerSend")}</button>
                        <button disabled={busy} className={BTN_SECONDARY} onClick={() => setOffer({ ...offer, confirm: false })}>{t("owner.invoices.edit")}</button>
                      </div>
                    ) : (
                      <div className="flex gap-2">
                        <button disabled={!/^\d+(\.\d{1,2})?$/.test(offer.amount.trim()) || Number(offer.amount) <= 0 || Number(offer.amount) > Number(d.amount)}
                                className={BTN_SMALL_PRIMARY} onClick={() => setOffer({ ...offer, confirm: true })}>{t("owner.invoices.review")}</button>
                        <button className={BTN_SECONDARY} onClick={() => setOffer(null)}>{t("owner.invoices.cancel")}</button>
                      </div>
                    )}
                  </div>
                ) : <button disabled={busy} className={BTN_SECONDARY} onClick={() => setOffer({ amount: "", note: "", confirm: false })}>{t("owner.disputes.offer")}</button>)}
                {d.can_evidence && (ev ? (
                  <div className="grid gap-1.5 rounded-md bg-slate-50 p-2">
                    <span className="text-xs font-semibold text-slate-700">{t("owner.disputes.evTitle")}</span>
                    <select value={ev.type} onChange={(e) => setEv({ ...ev, type: e.target.value })} aria-label={t("owner.disputes.evType")}
                            className="rounded-md border border-slate-300 px-2 py-1 text-sm">
                      {DISPUTE_EVIDENCE.map((k) => <option key={k} value={k}>{t(`owner.disputes.evTypes.${k}`)}</option>)}
                    </select>
                    <textarea value={ev.notes} rows={2} maxLength={2000} placeholder={t("owner.disputes.evNotes")} onChange={(e) => setEv({ ...ev, notes: e.target.value })}
                              className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
                    <input type="file" accept="application/pdf,image/png,image/jpeg,image/gif" onChange={(e) => setEv({ ...ev, file: e.target.files?.[0] ?? null })}
                           className="text-xs" aria-label={t("owner.disputes.evFile")} />
                    <div className="flex gap-2">
                      <button disabled={busy || (!ev.notes.trim() && !ev.file)} className={BTN_SMALL_PRIMARY}
                              onClick={() => void run(() => evidenceDispute(d.id, ev.type, ev.notes, ev.file, lang)).then(() => setEv(null))}>{t("owner.disputes.evSend")}</button>
                      <button disabled={busy} className={BTN_SECONDARY} onClick={() => setEv(null)}>{t("owner.invoices.cancel")}</button>
                    </div>
                  </div>
                ) : <button disabled={busy} className={BTN_SECONDARY} onClick={() => setEv({ type: DISPUTE_EVIDENCE[0], notes: "", file: null })}>{t("owner.disputes.evOpen")}</button>)}
                <a href={d.resolution_center} target="_blank" rel="noreferrer" className="block text-xs font-medium text-brand-green hover:underline">
                  {t("owner.disputes.evidence")}
                </a>
              </>
            )}
            {!preview && d.sandbox_actions.length > 0 && (
              <div className="border-t border-dashed border-slate-200 pt-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700">{t("owner.disputes.sandboxTitle")}</p>
                <p className="text-[11px] text-slate-500">{t("owner.disputes.sandboxHint")}</p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {d.sandbox_actions.map((a) => (
                    <button key={a} disabled={busy} className={BTN_GHOST + " ring-1 ring-amber-200"} onClick={() => void run(() => simulateDispute(d.id, a, lang))}>
                      {t(`owner.disputes.sandbox.${a}`)}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </li>
  );
}
