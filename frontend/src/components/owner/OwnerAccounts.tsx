import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getOwnerAccounts, type OwnerAccounts as Data } from "../../api";
import { DOT, DetailCard, DetailHeader, PageHeader, Stat } from "../PageKit";
import ConnectPayPal from "./ConnectPayPal";

// CUENTAS, en el portal del dueño (FASE 16 Paso 3). Sus bancos (Plaid) y su PayPal, con la última sincronización: solo
// nombres, últimos 4 dígitos y estado (la ruta no trae tokens ni transacciones). El dueño puede CONECTAR su PayPal (es
// su cuenta, Jose 31-oct); actualizarlo es del bookkeeper. En la vista previa, el botón se ve pero queda apagado.

export default function OwnerAccounts({ preview }: { preview: boolean }) {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<Data | null>(null);

  const load = () => getOwnerAccounts().then(setData).catch(() => setData({ banks: [], paypal: { connected: false, available: false, environment: "" } }));
  useEffect(() => { load(); }, []);


  if (!data) return <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>;
  const lang = i18n.language || "en";
  const when = (iso: string | null | undefined) =>
    iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString(lang, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : null;
  const pp = data.paypal;
  const nAccounts = data.banks.reduce((n, b) => n + b.accounts.length, 0) + (pp.connected ? 1 : 0);

  return (
    <div className="space-y-3">
      <PageHeader meta={t("owner.accounts.meta")}>
        <Stat value={nAccounts} label={t("owner.accounts.count", { count: nAccounts })} />
      </PageHeader>

      <DetailCard>
        <DetailHeader dot={!pp.connected ? DOT.off : pp.status === "error" ? DOT.problem : DOT.ok}
                      kicker={pp.environment === "sandbox" ? `PayPal · ${t("owner.paypal.sandbox")}` : "PayPal"}
                      title={pp.connected ? t("owner.paypal.connected") : t("owner.paypal.notConnected")}
                      sub={pp.connected ? (when(pp.last_synced_at) ? t("owner.paypal.lastSync", { date: when(pp.last_synced_at) }) : t("owner.paypal.neverSynced"))
                                        : t("owner.paypal.ownerHelp")} />
        {!pp.connected && (
          <div className="mt-3">
            <ConnectPayPal from="owner" disabled={preview || !pp.available} title={preview ? t("owner.preview.disabled") : undefined} />
            {!pp.available && <p className="mt-1 text-xs text-slate-500">{t("owner.paypal.err.paypal_not_configured")}</p>}
          </div>
        )}
      </DetailCard>

      <DetailCard>
        <DetailHeader kicker={t("owner.accounts.banks")} title={data.banks.length ? t("owner.accounts.banksTitle") : t("owner.accounts.noBanks")} />
        <ul className="mt-2 divide-y divide-slate-100">
          {data.banks.map((b, k) => (
            <li key={k} className="py-2 text-sm">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${b.status === "disconnected" ? DOT.problem : DOT.ok}`} />
                <span className="font-medium text-slate-800">{b.institution ?? "—"}</span>
                <span className="ms-auto text-xs text-slate-500">
                  {b.status === "disconnected" ? t("owner.accounts.disconnected") : when(b.last_synced_at) ? t("owner.paypal.lastSync", { date: when(b.last_synced_at) }) : ""}
                </span>
              </div>
              <div className="ps-4 text-xs text-slate-500">
                {b.accounts.map((a) => `${a.name ?? ""}${a.mask ? ` ••${a.mask}` : ""}`).join(" · ")}
              </div>
            </li>
          ))}
        </ul>
      </DetailCard>
    </div>
  );
}
