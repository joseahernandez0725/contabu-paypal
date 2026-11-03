import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getOwnerHome, type OwnerHome } from "../../api";
import OwnerYearView from "./OwnerYear";
import OwnerAccounts from "./OwnerAccounts";
import OwnerInvoices from "./OwnerInvoices";
import ConnectPayPal from "./ConnectPayPal";
import { monthLabel } from "./MonthCloseSection";
import { getInvoices, money } from "../../api";
import type { Company } from "../../types";
import { PAYPAL_DEMO } from "../../demo";
import type { View } from "../Sidebar";
import PayPalTour from "./PayPalTour";
import OwnerSidebar, { type OwnerView } from "./OwnerSidebar";
import { DetailCard, DetailEmpty, DetailHeader, PageHeader, Stat } from "../PageKit";

// EL PORTAL DEL DUEÑO (FASE 16, área PayPal / portal del dueño · 31-oct-2026).
// Misma app, CÁSCARA DISTINTA, elegida por el rol del libro abierto (App.tsx): el dueño (`CLIENT`) nunca ve
// la consola. Tres ventanas —Resumen · Cuentas · Cobros—, sin engranaje de Ajustes y sin selector de libro
// salvo que tenga más de uno (FASE 13.I Paso 9). Mismo diseño que la consola: la barra lateral desplegable
// (`OwnerSidebar`, pedido de Jose 31-oct), la barra de arriba y las piezas de `PageKit`.
//
// La VISTA PREVIA ("Ver como el dueño"): el bookkeeper abre ESTA misma cáscara. Pide las mismas rutas
// (`/api/owner/...`), así que ve exactamente lo que ve el dueño; el backend le dice `preview: true` y las
// acciones del dueño (facturar, conectar PayPal: Pasos 3 y 5) se ven pero quedan apagadas.
//
// Decisiones de Jose (31-oct): el dueño nunca ve el P&L ni la lista de transacciones; su Resumen son los
// totales por categoría de cada mes CERRADO por el bookkeeper, y el mes en curso no aparece (Paso 2).

export const OWNER_PREVIEW_EVENT = "contabu:owner-preview";

/** La consola pide la vista previa del portal (Ajustes → Dueño del negocio). La escucha App.tsx. */
export function openOwnerPreview() {
  window.dispatchEvent(new Event(OWNER_PREVIEW_EVENT));
}

export default function OwnerApp({ company, companies, onSwitch, onExitPreview, onOpenConsole, notice, initialView = "summary" }: {
  company: Company;
  companies: Company[];
  onSwitch: (c: Company) => void;
  onExitPreview?: () => void;   // solo en la vista previa del bookkeeper: volver a la consola
  onOpenConsole?: (v: View) => void;   // `paypal.contabu.com`: la guía lleva a una pantalla de la consola
  notice?: { tone: "ok" | "warn" | "error"; text: string; onClose: () => void } | null;   // la vuelta de PayPal
  initialView?: OwnerView;
}) {
  const { t, i18n } = useTranslation();
  const [view, setView] = useState<OwnerView>(initialView);
  const [newInvoice, setNewInvoice] = useState(0);   // "+ Nueva factura" desde el Resumen: abre Cobros con el formulario
  useEffect(() => { setView(initialView); }, [initialView]);
  const [menuOpen, setMenuOpen] = useState(false); // el cajón de la barra lateral (solo celular)
  const [home, setHome] = useState<OwnerHome | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setHome(null); setError(false);
    getOwnerHome().then(setHome).catch(() => setError(true));
  }, [company.id, notice?.text]);   // al volver de PayPal (aviso nuevo) se vuelve a pedir: la tarjeta de conectar se va

  const managedBy = home?.managed_by ?? null;

  // La misma cáscara que la consola (App.tsx): barra lateral desplegable + barra de arriba de h-14 con el título.
  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-900">
      <OwnerSidebar view={view} onNavigate={setView} company={company} companies={companies} onSwitch={onSwitch}
                    preview={!!onExitPreview} open={menuOpen} onClose={() => setMenuOpen(false)} />

      <div className="flex-1 min-w-0 flex flex-col">
        {onExitPreview && (
          <div className="bg-amber-50 border-b border-amber-200 px-3 sm:px-4 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-amber-900">
            <span className="font-semibold">{t(home && !home.preview ? "owner.preview.actingTitle" : "owner.preview.title")}</span>
            <span className="text-amber-800">{t(home && !home.preview ? "owner.preview.actingBody" : "owner.preview.body")}</span>
            <button onClick={onExitPreview}
                    className="ms-auto px-2.5 py-1 rounded-lg text-xs font-semibold border border-amber-300 bg-white hover:bg-amber-100">
              {t("owner.preview.exit")}
            </button>
          </div>
        )}

        <header className="sticky top-0 z-10 h-14 shrink-0 bg-white/80 backdrop-blur-md border-b border-slate-200 flex items-center gap-3 px-3 sm:px-4">
          <button onClick={() => setMenuOpen(true)} aria-label={t("shell.menu")}
                  className="p-1.5 -ms-1.5 rounded-lg text-slate-500 hover:bg-slate-100 md:hidden">
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
          <h1 className="font-semibold text-slate-900 truncate">{t(`owner.nav.${view}`)}</h1>
          {managedBy && <span className="text-sm text-slate-400 truncate">· {t("owner.managedBy", { name: managedBy })}</span>}
          {home && home.closed_months[0] && (
            <span className="hidden sm:inline text-sm text-slate-400 truncate">· {t("owner.lastClosed", { month: monthLabel(i18n.language || "en", home.closed_months[0].month) })}</span>
          )}
        </header>

        <main className="flex-1 w-full px-3 sm:px-4 py-3 space-y-3">
          {PAYPAL_DEMO && home && <PayPalTour view={view} onView={setView} onConsole={onOpenConsole} />}
          {notice && (
            <div className={`flex items-start gap-3 rounded-xl px-3 py-2.5 text-sm ${notice.tone === "ok" ? "bg-emerald-50 text-emerald-900" : notice.tone === "warn" ? "bg-amber-50 text-amber-900" : "bg-rose-50 text-rose-900"}`}>
              <span className="flex-1">{notice.text}</span>
              <button onClick={notice.onClose} aria-label="dismiss" className="opacity-60 hover:opacity-100">✕</button>
            </div>
          )}
          {error ? (
            <p className="text-sm text-rose-600">{t("owner.error")}</p>
          ) : !home ? (
            <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>
          ) : view === "summary" ? (
            <>
              {!home.paypal.connected && <PayPalHero preview={home.preview} />}
              <QuickActions preview={home.preview} paypal={home.paypal.connected}
                            onNewInvoice={() => { setView("invoices"); setNewInvoice((n) => n + 1); }}
                            onOpenInvoices={() => setView("invoices")} />
              <Summary home={home} />
            </>
          ) : view === "accounts" ? (
            <OwnerAccounts preview={home.preview} managedBy={home.managed_by} />
          ) : (
            <OwnerInvoices preview={home.preview} businessName={home.company.name} autoOpen={newInvoice} />
          )}
        </main>
      </div>
    </div>
  );
}

/** Resumen: el AÑO acumulado de los meses que el bookkeeper CERRÓ (Jose, 31-oct: una colección del año, no un
 *  reporte mes a mes), con gráficos. Sin utilidad: no es un P&L. Lo Pendiente se declara (regla 4). */
function Summary({ home }: { home: OwnerHome }) {
  const { t } = useTranslation();
  if (home.years.length === 0) {
    return (
      <>
        <PageHeader meta={t("owner.summary.meta")}>
          <Stat value={0} label={t("owner.summary.closedMonths", { count: 0 })} />
        </PageHeader>
        <DetailCard>
          <DetailHeader kicker={t("owner.nav.summary")} title={t("owner.summary.emptyTitle")} />
          <div className="mt-2">
            <DetailEmpty text={t("owner.summary.emptyBody", { name: home.managed_by ?? t("owner.yourBookkeeper") })} />
          </div>
        </DetailCard>
      </>
    );
  }
  return <OwnerYearView years={home.years} closed={home.closed_months} />;
}

/** La invitación GRANDE a conectar PayPal, arriba del Resumen, mientras no esté conectado (Jose, 1-nov). */
function PayPalHero({ preview }: { preview: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-xl border border-[#009CDE]/30 bg-gradient-to-r from-[#009CDE]/10 to-white p-4 sm:p-5 flex flex-wrap items-center gap-4">
      <div className="min-w-0 flex-1">
        <h3 className="text-base font-semibold text-slate-900">{t("owner.paypal.heroTitle")}</h3>
        <p className="text-sm text-slate-600 mt-1">{t("owner.paypal.heroBody")}</p>
        {/* Jose, 1-nov: claro y corto — se necesita cuenta de NEGOCIOS, abrirla no cuesta, se cobra con tarjeta, y una
            personal se convierte. Fuentes oficiales: paypal.com/us/business/paypal-business-fees y help318. */}
        <ul className="mt-3 space-y-1 text-sm text-slate-700">
          {(["needBusiness", "noCost", "cards", "convert"] as const).map((k) => (
            <li key={k} className="flex gap-2"><span className="text-[#0070BA]">✓</span><span>{t(`owner.paypal.hero.${k}`)}</span></li>
          ))}
        </ul>
        <p className="mt-2 text-xs flex flex-wrap gap-x-4 gap-y-1">
          <a href="https://www.paypal.com/us/business/open-business-account" target="_blank" rel="noreferrer" className="font-medium text-[#0070BA] hover:underline">
            {t("owner.paypal.openAccount")}
          </a>
          <a href="https://www.paypal.com/us/cshelp/article/what-is-the-difference-between-personal-and-business-accounts%E2%80%AF-help318" target="_blank" rel="noreferrer" className="font-medium text-[#0070BA] hover:underline">
            {t("owner.paypal.convertLink")}
          </a>
        </p>
      </div>
      <ConnectPayPal from="owner" disabled={preview} title={preview ? t("owner.preview.disabled") : undefined} />
    </div>
  );
}

/** El acceso rápido del Resumen (Jose, 1-nov): lo diario del dueño es COBRAR. "Por cobrar" sale de SQL (Cobros). */
function QuickActions({ preview, paypal, onNewInvoice, onOpenInvoices }: {
  preview: boolean; paypal: boolean; onNewInvoice: () => void; onOpenInvoices: () => void;
}) {
  const { t } = useTranslation();
  const [outstanding, setOutstanding] = useState<{ amount: string; overdue: number } | null>(null);
  useEffect(() => {
    getInvoices().then((v) => setOutstanding({ amount: v.summary.outstanding, overdue: v.summary.overdue })).catch(() => {});
  }, []);
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <button onClick={onNewInvoice} disabled={preview || !paypal} className="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-50"
              title={preview ? t("owner.preview.disabled") : !paypal ? t("owner.invoices.connectFirst") : undefined}>
        + {t("owner.invoices.new")}
      </button>
      {outstanding && (
        <button onClick={onOpenInvoices} className="text-start">
          <span className="block text-xs text-slate-500">{t("owner.invoices.outstandingTitle")}</span>
          <span className="text-lg font-semibold text-slate-900">{money(Number(outstanding.amount))}</span>
          {outstanding.overdue > 0 && <span className="ms-2 text-xs font-medium text-rose-700">{t("owner.quick.overdue", { count: outstanding.overdue })}</span>}
        </button>
      )}
      <span className="ms-auto text-xs text-slate-500">{t("owner.quick.hint")}</span>
    </div>
  );
}
