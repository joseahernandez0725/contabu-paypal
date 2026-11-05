import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAutoReminders, setAutoReminders, type AutoRule } from "../../api";
import { BTN_GHOST, BTN_SECONDARY, BTN_SMALL_PRIMARY } from "../PageKit";

// RECORDATORIOS AUTOMÁTICOS DE PAYPAL, en Cobros (FASE 16, 3-nov; `backend/invoice_reminders.py` §4). Dos reglas de la
// CUENTA de PayPal (no por factura): antes del vencimiento y después. PayPal las cumple solo; ContaBu solo las lee y las
// cambia. Límites de PayPal (✅ sandbox): de 1 a 7 días; después del vencimiento, de 1 a 7 veces; antes, 1 vez.
// En la vista previa y en la visita se ven, pero no se cambian.

const RANGE = [1, 2, 3, 4, 5, 6, 7];
const DEFAULT_BEFORE: AutoRule = { active: false, days: 2, times: 1 };
const DEFAULT_AFTER: AutoRule = { active: false, days: 2, times: 2 };

export default function AutoReminders({ preview }: { preview: boolean }) {
  const { t } = useTranslation();
  const [before, setBefore] = useState<AutoRule | null>(null);
  const [after, setAfter] = useState<AutoRule | null>(null);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function load(v: { before: AutoRule | null; after: AutoRule | null }) {
    setBefore(v.before ?? DEFAULT_BEFORE); setAfter(v.after ?? DEFAULT_AFTER);
  }
  useEffect(() => { getAutoReminders().then(load).catch(() => setErr(t("owner.auto.errLoad"))); }, [t]);

  if (!before || !after) return err ? <p className="text-xs text-rose-700">{err}</p> : null;
  const summary = [
    before.active ? t("owner.auto.beforeOn", { count: before.days }) : t("owner.auto.beforeOff"),
    after.active ? t("owner.auto.afterOn", { count: after.days, times: t("owner.auto.timesN", { count: after.times }) }) : t("owner.auto.afterOff"),
  ].join(" · ");

  async function save() {
    setBusy(true); setErr(null);
    try { load(await setAutoReminders(before!, after!)); setEdit(false); }
    catch { setErr(t("owner.auto.errSave")); }
    finally { setBusy(false); }
  }

  const sel = "rounded-md border border-slate-300 px-1.5 py-0.5 text-sm";
  return (
    <section className="bg-white rounded-xl border border-slate-200 px-3 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.auto.title")}</span>
        {!edit && <span className="text-xs text-slate-600">{summary}</span>}
        <span className="flex-1" />
        {!preview && !edit && <button onClick={() => setEdit(true)} className={BTN_GHOST}>{t("owner.auto.edit")}</button>}
      </div>
      {edit && (
        <div className="mt-2 grid gap-2">
          <p className="text-xs text-slate-500">{t("owner.auto.hint")}</p>
          <label className="flex flex-wrap items-center gap-2">
            <input type="checkbox" checked={before.active} onChange={(e) => setBefore({ ...before, active: e.target.checked })} />
            <span>{t("owner.auto.beforeLabel")}</span>
            <select value={before.days} onChange={(e) => setBefore({ ...before, days: Number(e.target.value) })} className={sel}
                    aria-label={t("owner.auto.days")}>
              {RANGE.map((n) => <option key={n} value={n}>{t("owner.auto.daysBefore", { count: n })}</option>)}
            </select>
          </label>
          <label className="flex flex-wrap items-center gap-2">
            <input type="checkbox" checked={after.active} onChange={(e) => setAfter({ ...after, active: e.target.checked })} />
            <span>{t("owner.auto.afterLabel")}</span>
            <select value={after.days} onChange={(e) => setAfter({ ...after, days: Number(e.target.value) })} className={sel}
                    aria-label={t("owner.auto.days")}>
              {RANGE.map((n) => <option key={n} value={n}>{t("owner.auto.everyDays", { count: n })}</option>)}
            </select>
            <select value={after.times} onChange={(e) => setAfter({ ...after, times: Number(e.target.value) })} className={sel}
                    aria-label={t("owner.auto.times")}>
              {RANGE.map((n) => <option key={n} value={n}>{t("owner.auto.timesN", { count: n })}</option>)}
            </select>
          </label>
          <div className="flex gap-2">
            <button disabled={busy} onClick={() => void save()} className={BTN_SMALL_PRIMARY}>{busy ? "…" : t("owner.auto.save")}</button>
            <button disabled={busy} onClick={() => { setEdit(false); getAutoReminders().then(load).catch(() => {}); }} className={BTN_SECONDARY}>
              {t("owner.invoices.cancel")}
            </button>
          </div>
        </div>
      )}
      {err && <p className="mt-1 text-xs text-rose-700">{err}</p>}
    </section>
  );
}
