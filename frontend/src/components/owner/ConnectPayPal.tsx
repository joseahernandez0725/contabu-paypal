import { useState } from "react";
import { useTranslation } from "react-i18next";
import { authorizePayPal, connectPayPalKeys } from "../../api";

// EL BOTÓN "CONECTAR CON PAYPAL" (FASE 16, 1-nov; Jose: "un punto clave del video y del demo"). Lleva al dueño (o al
// bookkeeper) a iniciar sesión DE VERDAD en PayPal; a la vuelta (`/paypal-return`) App.tsx termina la conexión.
// Se recuerda de dónde salió (portal o consola) para volver a la misma pantalla. Los colores son los del botón de
// PayPal (amarillo #FFC439, azules #003087 / #009CDE).

export const PAYPAL_FROM_KEY = "contabu:paypal-from";

export default function ConnectPayPal({ from, disabled, size = "lg", title }: {
  from: "owner" | "console"; disabled?: boolean; size?: "lg" | "sm"; title?: string;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showKeys, setShowKeys] = useState(false);
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");

  // Con SU cuenta (Jose, 2-nov): las llaves de su app de sandbox. Sin recarga no se ven lo importado ni las cuentas nuevas.
  async function connectKeys(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      await connectPayPalKeys(clientId, secret);
      setSecret("");
      window.location.reload();
    } catch (ex) {
      const code = ex instanceof Error ? ex.message : "";
      const known = ["paypal_auth_failed", "paypal_app_permissions", "paypal_in_use", "paypal_network", "paypal_not_configured"];
      setErr(t(`owner.paypal.err.${known.includes(code) ? code : "other"}`));
      setBusy(false);
    }
  }

  async function go() {
    setBusy(true); setErr(null);
    try {
      try { sessionStorage.setItem(PAYPAL_FROM_KEY, from); } catch { /* sin almacenamiento: se vuelve al inicio */ }
      window.location.assign(await authorizePayPal());
    } catch (e) {
      setErr(t(`owner.paypal.err.${e instanceof Error && e.message === "paypal_not_configured" ? "paypal_not_configured" : "other"}`));
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col gap-1">
      <button onClick={go} disabled={disabled || busy} title={title}
              className={`inline-flex items-center justify-center gap-1.5 rounded-full font-semibold text-[#003087] bg-[#FFC439] hover:bg-[#f2ba36] disabled:opacity-50 shadow-sm ${size === "lg" ? "px-6 py-2.5 text-base" : "px-4 py-1.5 text-sm"}`}>
        {busy ? t("owner.paypal.working") : (
          <>
            <span>{t("owner.paypal.connectWith")}</span>
            <span className="italic font-extrabold tracking-tight"><span className="text-[#003087]">Pay</span><span className="text-[#009CDE]">Pal</span></span>
          </>
        )}
      </button>
      <button type="button" onClick={() => setShowKeys((v) => !v)} disabled={disabled}
              className="self-center text-xs text-slate-500 underline disabled:opacity-50">
        {t("owner.paypal.keys.toggle")}
      </button>
      {showKeys && (
        <form onSubmit={connectKeys} className="flex flex-col gap-1.5 max-w-sm rounded-lg border border-slate-200 bg-white p-2.5 text-start">
          <p className="text-xs text-slate-500">{t("owner.paypal.keys.help")}</p>
          <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder={t("owner.paypal.keys.clientId")}
                 aria-label={t("owner.paypal.keys.clientId")} autoComplete="off" spellCheck={false}
                 className="min-w-0 rounded border border-slate-300 px-2 py-1 text-sm" />
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={t("owner.paypal.keys.secret")}
                 aria-label={t("owner.paypal.keys.secret")} autoComplete="new-password"
                 className="min-w-0 rounded border border-slate-300 px-2 py-1 text-sm" />
          <button type="submit" disabled={busy || !clientId.trim() || !secret.trim()}
                  className="rounded-full bg-[#003087] px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
            {busy ? t("owner.paypal.working") : t("owner.paypal.keys.submit")}
          </button>
        </form>
      )}
      {err && <span className="text-xs text-rose-700">{err}</span>}
    </span>
  );
}
