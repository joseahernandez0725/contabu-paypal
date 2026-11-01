import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { disconnectPayPal, getPayPal, syncPayPal, type PayPalStatus } from "../../api";
import ConnectPayPal from "./ConnectPayPal";
import { BTN_GHOST, BTN_SMALL_PRIMARY, DOT } from "../PageKit";
import PayPalTransfers from "./PayPalTransfers";

// PAYPAL EN LA PÁGINA BANCOS de la consola (FASE 16 Paso 3, área PayPal / portal del dueño · 31-oct-2026).
// PayPal es una cuenta más del libro: el bookkeeper lo conecta, lo actualiza y lo desconecta aquí, junto a los bancos de
// Plaid (Jose, 31-oct). El dueño también puede conectarlo desde su portal (Cuentas). Lo que se importa pasa primero por
// las reglas; lo que ninguna cubre lo clasifica el código de PayPal o queda Pendiente (`backend/paypal.py`).

export function payPalError(t: (k: string) => string, code: string): string {
  const known = ["paypal_not_configured", "paypal_auth_failed", "paypal_network", "paypal_not_connected"];
  return t(`owner.paypal.err.${known.includes(code) ? code : "other"}`);
}

export function ImportedLine({ s }: { s: PayPalStatus }) {
  const { t } = useTranslation();
  if (!s.imported) return null;
  const i = s.imported;
  return (
    <p className="text-xs text-slate-600 bg-slate-50 rounded-lg px-2.5 py-1.5">
      {i.new === 0 ? t("owner.paypal.upToDate")
        : t("owner.paypal.imported", { count: i.new, rule: i.by_rule, paypal: i.by_paypal, pending: i.pending })}
    </p>
  );
}

export default function PayPalCard({ canEdit }: { canEdit: boolean }) {
  const { t, i18n } = useTranslation();
  const [s, setS] = useState<PayPalStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);   // tras sincronizar, la sección de transferencias se recarga

  useEffect(() => { getPayPal().then(setS).catch(() => setS(null)); }, []);

  async function run(fn: () => Promise<PayPalStatus>) {
    setBusy(true); setErr(null);
    try { setS(await fn()); setTick((n) => n + 1); }
    catch (e) { setErr(payPalError(t, e instanceof Error ? e.message : "")); getPayPal().then(setS).catch(() => {}); }
    finally { setBusy(false); }
  }

  if (!s) return null;
  const when = s.last_synced_at
    ? new Date(s.last_synced_at + (s.last_synced_at.endsWith("Z") ? "" : "Z")).toLocaleString(i18n.language || "en")
    : null;
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${!s.connected ? DOT.off : s.status === "error" ? DOT.problem : DOT.ok}`} />
        <h3 className="text-sm font-semibold text-slate-900">PayPal</h3>
        {s.environment === "sandbox" && <span className="text-[10px] uppercase tracking-wide text-slate-500 bg-slate-100 rounded px-1.5 py-0.5">{t("owner.paypal.sandbox")}</span>}
        <span className="text-xs text-slate-500">
          {s.connected ? (when ? t("owner.paypal.lastSync", { date: when }) : t("owner.paypal.neverSynced")) : t("owner.paypal.notConnected")}
        </span>
        {canEdit && (
          <span className="ms-auto flex gap-2">
            {!s.connected ? (
              <ConnectPayPal from="console" size="sm" disabled={!s.available} />
            ) : (
              <>
                <button onClick={() => run(syncPayPal)} disabled={busy} className={BTN_SMALL_PRIMARY}>
                  {busy ? t("owner.paypal.working") : t("owner.paypal.sync")}
                </button>
                <button onClick={() => run(disconnectPayPal)} disabled={busy} className={`${BTN_GHOST} text-rose-700`}>
                  {t("owner.paypal.disconnect")}
                </button>
              </>
            )}
          </span>
        )}
      </div>
      <p className="text-xs text-slate-500">{t("owner.paypal.help")}</p>
      {!s.available && !s.connected && <p className="text-xs text-slate-500">{t("owner.paypal.err.paypal_not_configured")}</p>}
      {s.status === "error" && s.last_error && <p className="text-xs text-rose-700">{payPalError(t, "paypal_" + s.last_error)}</p>}
      <ImportedLine s={s} />
      {err && <p className="text-xs text-rose-700">{err}</p>}
      {s.connected && <PayPalTransfers refreshKey={tick} />}
    </div>
  );
}
