import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { confirmPayPalTransfer, getPayPalTransfers, matchPayPalTransfers, money, rejectPayPalTransfer,
         type PayPalTransfers as Data } from "../../api";
import { BTN_GHOST, BTN_SECONDARY, BTN_SMALL_PRIMARY } from "../PageKit";

// TRANSFERENCIAS DE PAYPAL A LA CUENTA BANCARIA (FASE 16 Paso 4), dentro de la tarjeta de PayPal en Bancos.
// El código empareja el retiro de PayPal con el depósito del banco (mismo monto, 0 a 7 días después) cuando no hay duda;
// lo demás lo confirma el bookkeeper aquí. Así la venta no se cuenta dos veces (`backend/paypal.py`).

export default function PayPalTransfers({ refreshKey }: { refreshKey: number }) {
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<Data | null>(null);
  const [pick, setPick] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const lang = i18n.language || "en";
  const day = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });

  useEffect(() => { getPayPalTransfers().then(setD).catch(() => setD(null)); }, [refreshKey]);

  async function run(fn: () => Promise<Data>) {
    setBusy(true); setErr(null);
    try { setD(await fn()); }
    catch (e) { setErr((e instanceof Error && e.message.startsWith("month_closed")) ? t("owner.transfers.monthClosed") : t("owner.transfers.error")); }
    finally { setBusy(false); }
  }

  if (!d || (d.matched.length + d.proposals.length + d.unmatched.length === 0)) return null;
  return (
    <section className="border-t border-slate-100 pt-2 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.transfers.title")}</h4>
        <span className="text-xs text-slate-500">
          {t("owner.transfers.summary", { matched: d.matched.length, proposals: d.proposals.length, unmatched: d.unmatched.length })}
        </span>
        <button onClick={() => run(matchPayPalTransfers)} disabled={busy} className={`${BTN_GHOST} ms-auto`}>{t("owner.transfers.rematch")}</button>
      </div>
      <p className="text-xs text-slate-500">{t("owner.transfers.help", { days: d.window_days })}</p>

      {d.proposals.map((p) => (
        <div key={p.withdrawal.id} className="rounded-lg border border-amber-200 bg-amber-50/50 p-2 space-y-1.5">
          <div className="text-xs text-slate-700">
            <span className="font-semibold">{t("owner.transfers.toConfirm")}</span>{" "}
            {t("owner.transfers.withdrawal", { amount: money(Number(p.withdrawal.amount)), date: day(p.withdrawal.date) })}
          </div>
          {p.candidates.map((c) => (
            <label key={c.id} className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
              <input type="radio" name={`w${p.withdrawal.id}`} checked={pick[p.withdrawal.id] === c.id}
                     onChange={() => setPick((x) => ({ ...x, [p.withdrawal.id]: c.id }))} />
              <span className="tabular-nums">{day(c.date)}</span>
              <span className="truncate">{c.description}</span>
              <span className="text-slate-400">{c.bank_account ?? ""}</span>
              {c.category && <span className="text-slate-400">· {c.category}</span>}
            </label>
          ))}
          <div className="flex gap-2">
            <button disabled={busy || !pick[p.withdrawal.id]} className={BTN_SMALL_PRIMARY}
                    onClick={() => run(() => confirmPayPalTransfer(p.withdrawal.id, pick[p.withdrawal.id]))}>
              {t("owner.transfers.confirm")}
            </button>
            <button disabled={busy} className={BTN_SECONDARY} onClick={() => run(() => rejectPayPalTransfer(p.withdrawal.id))}>
              {t("owner.transfers.none")}
            </button>
          </div>
        </div>
      ))}

      {d.matched.length > 0 && (
        <ul className="text-xs text-slate-600 space-y-0.5">
          {d.matched.map((m) => (
            <li key={m.withdrawal.id} className="flex flex-wrap gap-x-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 self-center" />
              <span>{t("owner.transfers.withdrawal", { amount: money(Number(m.withdrawal.amount)), date: day(m.withdrawal.date) })}</span>
              <span className="text-slate-400">→</span>
              <span className="truncate">{m.deposit ? `${day(m.deposit.date)} · ${m.deposit.description}` : "—"}</span>
              <span className="text-slate-400">{m.status === "auto" ? t("owner.transfers.auto") : t("owner.transfers.confirmed")}</span>
            </li>
          ))}
        </ul>
      )}
      {d.unmatched.length > 0 && (
        <p className="text-xs text-slate-500">
          {t("owner.transfers.unmatched", { count: d.unmatched.length })}{" "}
          {d.unmatched.map((u) => `${money(Number(u.withdrawal.amount))} (${day(u.withdrawal.date)})`).join(" · ")}
        </p>
      )}
      {err && <p className="text-xs text-rose-700">{err}</p>}
    </section>
  );
}
