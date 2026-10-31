import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getMonths, setMonthClosed, type BookMonth } from "../../api";
import { BTN_SECONDARY, BTN_SMALL_PRIMARY } from "../PageKit";

// CIERRE DE MES, en la consola (FASE 16 Paso 2, área PayPal / portal del dueño · 31-oct-2026).
// Decisiones de Jose (31-oct): el bookkeeper cierra cada mes que ya terminó; al cerrarlo, sus totales por categoría
// suben al portal del dueño y el mes queda congelado (ni reglas, ni edición, ni banco lo cambian). Se puede cerrar
// con Pendientes (se pide confirmar y el dueño los ve declarados) y se puede reabrir (queda registrado).
// Lo que llega tarde a un mes cerrado entra al libro pero no a la foto: aquí se avisa, y se reabre para tomarlo.
// Un mes se cierra solo cuando pasaron los días de espera del libro (Jose, 31-oct): antes sale "En días de espera".

export function monthLabel(lang: string, ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  const s = new Date(y, m - 1, 1).toLocaleDateString(lang, { month: "long", year: "numeric" });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export default function MonthCloseSection({ canClose }: { canClose: boolean }) {
  const { t, i18n } = useTranslation();
  const [months, setMonths] = useState<BookMonth[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);   // mes con Pendientes esperando confirmación
  const [err, setErr] = useState(false);

  const load = () => getMonths().then(setMonths).catch(() => setMonths([]));
  useEffect(() => { load(); }, []);

  async function act(m: BookMonth, close: boolean) {
    if (close && m.pending > 0 && confirm !== m.month) { setConfirm(m.month); return; }
    setBusy(m.month); setErr(false); setConfirm(null);
    try { await setMonthClosed(m.month, close); await load(); }
    catch { setErr(true); }
    finally { setBusy(null); }
  }

  const lang = i18n.language || "en";
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-3 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{t("owner.close.title")}</h3>
        <p className="text-xs text-slate-500 mt-1">{t("owner.close.help")}</p>
      </div>

      {months && months.length === 0 && <p className="text-sm text-slate-400">{t("owner.close.none")}</p>}
      <ul className="divide-y divide-slate-100">
        {(months ?? []).map((m) => (
          <li key={m.month} className="py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className={`w-2 h-2 rounded-full ${m.status === "closed" ? "bg-emerald-500" : m.status === "current" || m.status === "waiting" ? "bg-slate-300" : "bg-amber-400"}`} />
            <span className="font-medium text-slate-800 w-36">{monthLabel(lang, m.month)}</span>
            <span className="text-xs text-slate-500">
              {t("owner.close.count", { count: m.count })}
              {m.pending > 0 && <span className="ms-2 text-amber-700">{t("owner.close.pending", { count: m.pending })}</span>}
            </span>
            <span className="text-xs text-slate-500">
              {m.status === "closed" && m.closed_at && t("owner.close.closedOn", {
                date: new Date(m.closed_at).toLocaleDateString(lang, { day: "numeric", month: "short" }),
                who: m.closed_by ?? "—",
              })}
              {m.status === "current" && t("owner.close.current")}
              {m.status === "waiting" && t("owner.close.waiting", {
                date: new Date(`${m.closable_on}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" }),
              })}
            </span>
            {m.status === "closed" && m.late > 0 && (
              <span className="text-xs text-rose-700 bg-rose-50 rounded px-1.5 py-0.5">{t("owner.close.late", { count: m.late })}</span>
            )}
            <span className="ms-auto flex items-center gap-2">
              {confirm === m.month && <span className="text-xs text-amber-700">{t("owner.close.confirmPending", { count: m.pending })}</span>}
              {canClose && m.status === "open" && (
                <button onClick={() => act(m, true)} disabled={busy === m.month} className={BTN_SMALL_PRIMARY}>
                  {confirm === m.month ? t("owner.close.closeAnyway") : t("owner.close.close")}
                </button>
              )}
              {canClose && m.status === "closed" && (
                <button onClick={() => act(m, false)} disabled={busy === m.month} className={BTN_SECONDARY}>
                  {t("owner.close.reopen")}
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
      {err && <p className="text-sm text-rose-600">{t("owner.close.error")}</p>}
    </div>
  );
}
