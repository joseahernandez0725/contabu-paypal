import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getOwnerHome, type OwnerHome } from "../../api";
import OwnerYearView from "./OwnerYear";
import type { Company } from "../../types";
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

export default function OwnerApp({ company, companies, onSwitch, onExitPreview }: {
  company: Company;
  companies: Company[];
  onSwitch: (c: Company) => void;
  onExitPreview?: () => void;   // solo en la vista previa del bookkeeper: volver a la consola
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<OwnerView>("summary");
  const [menuOpen, setMenuOpen] = useState(false); // el cajón de la barra lateral (solo celular)
  const [home, setHome] = useState<OwnerHome | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setHome(null); setError(false);
    getOwnerHome().then(setHome).catch(() => setError(true));
  }, [company.id]);

  const managedBy = home?.managed_by ?? null;

  // La misma cáscara que la consola (App.tsx): barra lateral desplegable + barra de arriba de h-14 con el título.
  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-900">
      <OwnerSidebar view={view} onNavigate={setView} company={company} companies={companies} onSwitch={onSwitch}
                    preview={!!onExitPreview} open={menuOpen} onClose={() => setMenuOpen(false)} />

      <div className="flex-1 min-w-0 flex flex-col">
        {onExitPreview && (
          <div className="bg-amber-50 border-b border-amber-200 px-3 sm:px-4 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-amber-900">
            <span className="font-semibold">{t("owner.preview.title")}</span>
            <span className="text-amber-800">{t("owner.preview.body")}</span>
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
        </header>

        <main className="flex-1 w-full px-3 sm:px-4 py-3 space-y-3">
          {error ? (
            <p className="text-sm text-rose-600">{t("owner.error")}</p>
          ) : !home ? (
            <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>
          ) : view === "summary" ? (
            <Summary home={home} />
          ) : view === "accounts" ? (
            <Placeholder kicker={t("owner.nav.accounts")} title={t("owner.accounts.title")} body={t("owner.accounts.body")} />
          ) : (
            <Placeholder kicker={t("owner.nav.invoices")} title={t("owner.invoices.title")} body={t("owner.invoices.body")} />
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

function Placeholder({ kicker, title, body }: { kicker: string; title: string; body: string }) {
  return (
    <DetailCard>
      <DetailHeader kicker={kicker} title={title} />
      <div className="mt-2"><DetailEmpty text={body} /></div>
    </DetailCard>
  );
}
