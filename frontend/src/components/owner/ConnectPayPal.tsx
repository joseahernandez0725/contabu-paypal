import { useState } from "react";
import { useTranslation } from "react-i18next";
import { authorizePayPal } from "../../api";

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
      {err && <span className="text-xs text-rose-700">{err}</span>}
    </span>
  );
}
