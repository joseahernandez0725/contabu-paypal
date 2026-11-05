import { useTranslation } from "react-i18next";
import AutoReminders from "./AutoReminders";
import InvoiceProfileCard from "./InvoiceProfileCard";

// CONFIGURACIÓN, en el portal del dueño (FASE 16, 4-nov; pedido de Jose: "es más de la configuración"). Lo que se ajusta
// una vez y no se toca cada día: los datos y el logo de sus facturas, y las reglas de recordatorios automáticos de PayPal.
// Cobros queda para cobrar. En la vista previa y en la visita se ven, pero no se cambian.

export default function OwnerSettings({ preview, businessName, paypalConnected }: {
  preview: boolean; businessName: string; paypalConnected: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">{t("owner.settings.intro")}</p>
      <InvoiceProfileCard preview={preview} businessName={businessName} />
      {paypalConnected ? <AutoReminders preview={preview} />
        : <p className="text-xs text-slate-500">{t("owner.settings.needsPaypal")}</p>}
    </div>
  );
}
