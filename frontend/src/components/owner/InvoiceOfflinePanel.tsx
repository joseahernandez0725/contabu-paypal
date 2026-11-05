import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { confirmOffline, getOffline, money, rejectOffline, rematchOffline, type OfflineView } from "../../api";
import { BTN_GHOST, BTN_SECONDARY, BTN_SMALL_PRIMARY } from "../PageKit";

// FACTURAS COBRADAS (O DEVUELTAS) FUERA DE PAYPAL, para el bookkeeper (FASE 16, 3-nov; `backend/invoice_offline.py`).
// El dueño las registra en su portal; ContaBu no crea venta: empareja la fila del banco. Si había una sola, ya quedó; si
// hay varias, se elige aquí (queda como decisión del bookkeeper). Lo que no llegó al banco se declara.

export default function InvoiceOfflinePanel({ refreshKey }: { refreshKey: number }) {
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<OfflineView | null>(null);
  const [pick, setPick] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const lang = i18n.language || "en";
  const day = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });

  useEffect(() => { getOffline().then(setD).catch(() => setD(null)); }, [refreshKey]);

  async function run(fn: () => Promise<OfflineView>) {
    setBusy(true); setErr(null);
    try { setD(await fn()); } catch { setErr(t("owner.offline.console.error")); } finally { setBusy(false); }
  }

  if (!d || d.proposals.length + d.matched.length + d.waiting.length === 0) return null;
  const what = (h: OfflineView["waiting"][number]) => t(`owner.offline.console.${h.kind}`, {
    number: h.invoice.number ?? "—", customer: h.invoice.customer ?? "—", amount: money(Number(h.amount)),
    method: t(`owner.offline.methods.${h.method}`).toLowerCase(), date: day(h.day),
  });
  return (
    <section className="border-t border-slate-100 pt-2 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.offline.console.title")}</h4>
        <button onClick={() => run(rematchOffline)} disabled={busy} className={`${BTN_GHOST} ms-auto`}>{t("owner.transfers.rematch")}</button>
      </div>
      <p className="text-xs text-slate-500">{t("owner.offline.console.help", { before: d.window.before, after: d.window.after })}</p>
      {d.proposals.map((p) => (
        <div key={p.id} className="rounded-lg border border-amber-200 bg-amber-50/50 p-2 space-y-1.5">
          <div className="text-xs text-slate-700"><span className="font-semibold">{t("owner.transfers.toConfirm")}</span> {what(p)}</div>
          {p.candidates.map((c) => (
            <label key={c.id} className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
              <input type="radio" name={`o${p.id}`} checked={pick[p.id] === c.id} onChange={() => setPick((x) => ({ ...x, [p.id]: c.id }))} />
              <span className="tabular-nums">{day(c.date)}</span>
              <span className="truncate">{c.description}</span>
              <span className="text-slate-400">{c.bank_account ?? ""}</span>
            </label>
          ))}
          <div className="flex gap-2">
            <button disabled={busy || !pick[p.id]} className={BTN_SMALL_PRIMARY} onClick={() => run(() => confirmOffline(p.id, pick[p.id]))}>
              {t("owner.transfers.confirm")}
            </button>
            <button disabled={busy} className={BTN_SECONDARY} onClick={() => run(() => rejectOffline(p.id))}>{t("owner.transfers.none")}</button>
          </div>
        </div>
      ))}
      {d.matched.length > 0 && (
        <ul className="text-xs text-slate-600 space-y-0.5">
          {d.matched.map((m) => (
            <li key={m.id} className="flex flex-wrap gap-x-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 self-center" />
              <span>{what(m)}</span><span className="text-slate-400">→</span>
              <span className="truncate">{m.bank ? `${day(m.bank.date)} · ${m.bank.description}` : "—"}</span>
              <span className="text-slate-400">{m.status === "auto" ? t("owner.transfers.auto") : t("owner.transfers.confirmed")}</span>
            </li>
          ))}
        </ul>
      )}
      {d.waiting.length > 0 && (
        <p className="text-xs text-slate-500">{t("owner.offline.console.waiting", { count: d.waiting.length })} {d.waiting.map(what).join(" · ")}</p>
      )}
      {err && <p className="text-xs text-rose-700">{err}</p>}
    </section>
  );
}
