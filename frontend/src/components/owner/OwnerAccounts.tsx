import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { BankApiError, checkBankConnection, createBankConnection, createBankLinkToken, getOwnerAccounts, money,
         type BankLinkMeta, type OwnerAccounts as Data } from "../../api";
import { loadPlaid } from "../BankConnections";
import { BTN_PRIMARY, BTN_SECONDARY, DOT, DetailCard, DetailHeader } from "../PageKit";
import ConnectPayPal from "./ConnectPayPal";
import OwnerStatementUpload from "./OwnerStatementUpload";

// CUENTAS, en el portal del dueño (FASE 16; 🔄 1-nov, Jose: más útil). Arriba, QUÉ está conectado a ContaBu (la misma
// contabilidad que lleva su bookkeeper). PayPal con su SALDO y "Pasar a mi banco" (en PayPal: la transferencia estándar
// no cuesta, la instantánea 1.5% — ✅ paypal.com/us/business/paypal-business-fees). Bancos: el dueño VINCULA el suyo con
// Plaid (sus credenciales); reparar, sincronizar y quitar siguen siendo del bookkeeper. La ruta no trae tokens ni
// transacciones. En la vista previa, los botones se ven pero quedan apagados.

export default function OwnerAccounts({ preview, managedBy }: { preview: boolean; managedBy: string | null }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [data, setData] = useState<Data | null>(null);
  const [linking, setLinking] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "warn" | "error"; text: string } | null>(null);

  const load = () => getOwnerAccounts().then(setData).catch(() => setData({ banks: [], paypal: { connected: false, available: false, environment: "" } }));
  useEffect(() => { load(); }, []);

  async function linkBank() {
    setLinking(true); setMsg(null);
    try {
      const [{ link_token }] = await Promise.all([createBankLinkToken(lang), loadPlaid()]);
      const handler = window.Plaid!.create({
        token: link_token,
        onSuccess: async (publicToken, metadata) => {
          try {
            const meta: BankLinkMeta = {
              institution_id: metadata?.institution?.institution_id ?? null,
              institution_name: metadata?.institution?.name ?? null,
              account_masks: (metadata?.accounts ?? []).map((a) => a.mask ?? "").filter(Boolean),
            };
            const { duplicate } = await checkBankConnection(meta);   // ya vinculado → no se gasta otra conexión de Plaid
            if (duplicate) { setMsg({ tone: "warn", text: t("owner.accounts.bankDuplicate", { bank: meta.institution_name ?? "—" }) }); return; }
            await createBankConnection(publicToken, meta);
            setMsg({ tone: "ok", text: t("owner.accounts.bankLinked", { bank: meta.institution_name ?? "—" }) });
          } catch (e) {
            setMsg({ tone: "error", text: t(`bank.err.${e instanceof BankApiError ? e.code : "bank_error"}`, { defaultValue: t("owner.accounts.bankError") }) });
          } finally { handler.destroy(); setLinking(false); load(); }
        },
        onExit: () => { handler.destroy(); setLinking(false); },
      });
      handler.open();
    } catch {
      setMsg({ tone: "error", text: t("owner.accounts.bankError") }); setLinking(false);
    }
  }

  if (!data) return <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>;
  const when = (iso: string | null | undefined) =>
    iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString(lang, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : null;
  const pp = data.paypal;
  const connected = [
    ...(pp.connected ? [{ key: "pp", label: `PayPal${pp.environment === "sandbox" ? ` (${t("owner.paypal.sandbox")})` : ""}`, ok: pp.status !== "error" }] : []),
    ...data.banks.flatMap((b, k) => (b.accounts.length ? b.accounts : [{ name: null, mask: null }]).map((a, j) => ({
      key: `b${k}-${j}`, label: `${b.institution ?? "—"}${a.name ? ` · ${a.name}` : ""}${a.mask ? ` ••${a.mask}` : ""}`, ok: b.status !== "disconnected",
    }))),
  ];

  return (
    <div className="space-y-3">
      {/* 1) Qué está conectado a ContaBu — la misma contabilidad que lleva el bookkeeper. */}
      <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 px-4 py-3">
        <div className="text-sm font-semibold text-slate-900">{t("owner.accounts.connectedTitle", { count: connected.length })}</div>
        <p className="text-xs text-slate-600 mt-0.5">{t("owner.accounts.connectedBody", { name: managedBy ?? t("owner.yourBookkeeper") })}</p>
        {connected.length > 0 ? (
          <ul className="mt-2 flex flex-wrap gap-2">
            {connected.map((c) => (
              <li key={c.key} className="inline-flex items-center gap-1.5 rounded-full bg-white border border-slate-200 px-2.5 py-1 text-xs text-slate-700">
                <span className={`w-1.5 h-1.5 rounded-full ${c.ok ? DOT.ok : DOT.problem}`} />{c.label}
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-xs text-slate-500">{t("owner.accounts.noneConnected")}</p>}
      </div>

      {msg && <p className={`text-sm rounded-lg px-3 py-2 ${msg.tone === "ok" ? "bg-emerald-50 text-emerald-900" : msg.tone === "warn" ? "bg-amber-50 text-amber-900" : "bg-rose-50 text-rose-900"}`}>{msg.text}</p>}

      <div className="grid gap-3 lg:grid-cols-2">
        {/* 2) PayPal: estado, saldo y cómo pasarlo al banco. */}
        <DetailCard>
          <DetailHeader dot={!pp.connected ? DOT.off : pp.status === "error" ? DOT.problem : DOT.ok}
                        kicker={pp.environment === "sandbox" ? `PayPal · ${t("owner.paypal.sandbox")}` : "PayPal"}
                        title={pp.connected ? t("owner.paypal.connected") : t("owner.paypal.notConnected")}
                        sub={pp.connected ? (when(pp.last_synced_at) ? t("owner.paypal.lastSync", { date: when(pp.last_synced_at) }) : t("owner.paypal.neverSynced"))
                                          : t("owner.paypal.ownerHelp")} />
          {pp.connected && pp.balance?.available != null && (
            <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="text-xs text-slate-500">{t("owner.accounts.ppAvailable")}</div>
                <div className="text-2xl font-semibold text-slate-900">{money(Number(pp.balance.available))}</div>
                {pp.balance.total != null && pp.balance.total !== pp.balance.available && (
                  <div className="text-xs text-slate-500">{t("owner.accounts.ppTotal", { amount: money(Number(pp.balance.total)) })}</div>
                )}
              </div>
              {pp.wallet_url && (
                <a href={pp.wallet_url} target="_blank" rel="noreferrer" className={BTN_SECONDARY}>{t("owner.accounts.ppWithdraw")}</a>
              )}
            </div>
          )}
          {pp.connected && <p className="mt-2 text-[11px] text-slate-500">{t("owner.accounts.ppWithdrawNote")}</p>}
          {!pp.connected && (
            <div className="mt-3">
              <ConnectPayPal from="owner" disabled={preview || !pp.available} title={preview ? t("owner.preview.disabled") : undefined} />
              {!pp.available && <p className="mt-1 text-xs text-slate-500">{t("owner.paypal.err.paypal_not_configured")}</p>}
            </div>
          )}
        </DetailCard>

        {/* 3) Bancos: los vinculados y el botón para vincular el suyo (Plaid). */}
        <DetailCard>
          <DetailHeader kicker={t("owner.accounts.banks")} title={data.banks.length ? t("owner.accounts.banksTitle") : t("owner.accounts.noBanks")}
                        sub={data.banks.length ? undefined : t("owner.accounts.noBanksHelp")} />
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
                <div className="ps-4 text-xs text-slate-500">{b.accounts.map((a) => `${a.name ?? ""}${a.mask ? ` ••${a.mask}` : ""}`).join(" · ")}</div>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button onClick={() => void linkBank()} disabled={preview || linking} className={BTN_PRIMARY}
                    title={preview ? t("owner.preview.disabled") : undefined}>
              {linking ? t("owner.paypal.working") : `+ ${t("owner.accounts.linkBank")}`}
            </button>
            <span className="text-[11px] text-slate-500">{t("owner.accounts.linkBankNote")}</span>
          </div>
        </DetailCard>
      </div>

      {/* 4) Subir un estado de cuenta (para el banco que no se vincula, o meses anteriores). */}
      <OwnerStatementUpload preview={preview} onDone={load} />
    </div>
  );
}
