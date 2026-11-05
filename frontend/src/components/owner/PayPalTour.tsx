import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { goLive, inTour } from "../../demo";
import type { View } from "../Sidebar";
import type { OwnerView } from "./OwnerSidebar";
import { isPayPalGuideOpen, openPayPalGuide, PAYPAL_GUIDE_STATE } from "./PayPalGuide";

// LA GUÍA DE `paypal.contabu.com` (FASE 16 Paso 7, Jose 2-nov: "enfocada en mostrar PayPal", sin gastar créditos de
// ninguna plataforma). Una tarjeta con las paradas en orden: cada una lleva a su pantalla, del portal o de la consola. Todo
// lo que muestra sale de los datos sembrados (`backend/demo_paypal.py`) y del saldo de sandbox; no llama a ningún modelo.
// Reemplaza aquí a la guía del concurso de Nebius (`DemoGuide.tsx`), que recorre los agentes.

type Stop = { key: string; owner?: OwnerView; console?: View };
const STOPS: Stop[] = [
  { key: "summary", owner: "summary" },
  { key: "accounts", owner: "accounts" },
  { key: "invoices", owner: "invoices" },
  { key: "bank", console: "upload" },
  { key: "close", console: "settings" },
];
const HIDDEN_KEY = "contabu.paypalTour.hidden.v1";

// En la visita guiada no se recuerda: cada juez empieza con la pantalla completa (Jose, 3-nov).
function readHidden(): boolean {
  if (inTour()) return false;
  try { return localStorage.getItem(HIDDEN_KEY) === "1"; } catch { return false; }
}

// Va arriba en el portal Y en la consola (Jose, 2-nov: "conservar la tabla de arriba para desplazarnos"): la parada de la
// pantalla abierta queda marcada, y cada una lleva a la suya desde cualquiera de las dos cáscaras.
export default function PayPalTour({ ownerView, consoleView, onOwner, onConsole }: {
  ownerView?: OwnerView;
  consoleView?: View;
  onOwner: (v: OwnerView) => void;
  onConsole?: (v: View) => void;
}) {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState(readHidden);
  // En el celular empieza PLEGADA en una línea con la parada actual (revisión 1, 3-nov: las tarjetas ocupaban una
  // pantalla entera antes de la primera cifra del negocio). En pantallas anchas, abierta como siempre.
  const [open, setOpen] = useState(() => typeof window === "undefined" || window.matchMedia("(min-width: 640px)").matches);
  const touring = inTour();
  const current = STOPS.findIndex((s) => (s.owner ? s.owner === ownerView : s.console === consoleView));
  // Mientras corre la guía con foco (`PayPalGuide`, 5-nov) la tarjeta se oculta: la guía ya lleva de pantalla en pantalla.
  const [guiding, setGuiding] = useState(isPayPalGuideOpen);
  useEffect(() => {
    const on = (e: Event) => setGuiding(Boolean((e as CustomEvent<boolean>).detail));
    window.addEventListener(PAYPAL_GUIDE_STATE, on);
    return () => window.removeEventListener(PAYPAL_GUIDE_STATE, on);
  }, []);
  if (guiding) return null;

  function hide(v: boolean) {
    setHidden(v);
    try { localStorage.setItem(HIDDEN_KEY, v ? "1" : "0"); } catch { /* sin almacenamiento: solo esta vez */ }
  }

  const guideButton = (
    <button onClick={openPayPalGuide} className="shrink-0 rounded-full bg-[#FFC439] px-3 py-1 text-xs font-semibold text-[#003087] hover:bg-[#f2ba36]">
      {t("owner.guide.open")}
    </button>
  );
  if (hidden) {
    return (
      <div className="flex items-center gap-3">
        {guideButton}
        <button onClick={() => hide(false)} className="text-xs font-semibold text-[#0070BA] hover:underline">
          {t("owner.tour.show")}
        </button>
      </div>
    );
  }
  return (
    <section className="rounded-xl border border-[#009CDE]/30 bg-white p-3 sm:p-4 space-y-2">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-900">{t("owner.tour.title")}</h2>
          <p className="text-xs text-slate-500">{t(touring ? "owner.tour.introTour" : "owner.tour.introLive")}</p>
        </div>
        {guideButton}
        <button onClick={() => hide(true)} aria-label={t("owner.tour.hide")} className="text-slate-400 hover:text-slate-700">✕</button>
      </div>
      {!open && (
        <button onClick={() => setOpen(true)} className="w-full flex items-center justify-between gap-2 rounded-lg border border-[#0070BA] bg-[#009CDE]/10 px-2.5 py-2 text-start">
          <span className="text-xs font-semibold text-slate-900 truncate">
            {current >= 0
              ? t("owner.tour.step", { n: current + 1, total: STOPS.length, title: t(`owner.tour.stops.${STOPS[current].key}.title`) })
              : t("owner.tour.title")}
          </span>
          <span className="shrink-0 text-xs font-semibold text-[#0070BA]">{t("owner.tour.allStops")}</span>
        </button>
      )}
      {open && <ol className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {STOPS.map((s, i) => {
          const here = s.owner ? s.owner === ownerView : s.console === consoleView;
          const go = s.owner ? () => onOwner(s.owner!) : onConsole && s.console ? () => onConsole(s.console!) : undefined;
          return (
            <li key={s.key}>
              <button onClick={go} disabled={!go}
                      className={`w-full min-w-0 text-start rounded-lg border px-2.5 py-2 ${here ? "border-[#0070BA] bg-[#009CDE]/10" : "border-slate-200 hover:bg-slate-50"} disabled:opacity-60`}>
                <span className="block text-xs font-semibold text-slate-900">
                  {i + 1}. {t(`owner.tour.stops.${s.key}.title`)}
                  {s.console && <span className="ms-1.5 font-normal text-slate-400">· {t("owner.tour.bookkeeper")}</span>}
                </span>
                <span className="block text-xs text-slate-600">{t(`owner.tour.stops.${s.key}.body`)}</span>
              </button>
            </li>
          );
        })}
        {touring && (
          <li>
            <button onClick={() => void goLive()} className="w-full text-start rounded-lg border border-[#FFC439] bg-[#FFC439]/15 px-2.5 py-2 hover:bg-[#FFC439]/30">
              <span className="block text-xs font-semibold text-[#003087]">6. {t("owner.tour.stops.try.title")}</span>
              <span className="block text-xs text-slate-700">{t("owner.tour.stops.try.body")}</span>
            </button>
          </li>
        )}
      </ol>}
      {open && (
        <button onClick={() => setOpen(false)} className="sm:hidden text-xs font-semibold text-[#0070BA] hover:underline">
          {t("owner.tour.fewerStops")}
        </button>
      )}
    </section>
  );
}
