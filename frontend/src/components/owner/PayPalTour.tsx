import { useState } from "react";
import { useTranslation } from "react-i18next";
import { goLive, inTour } from "../../demo";
import type { View } from "../Sidebar";
import type { OwnerView } from "./OwnerSidebar";

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
  const touring = inTour();

  function hide(v: boolean) {
    setHidden(v);
    try { localStorage.setItem(HIDDEN_KEY, v ? "1" : "0"); } catch { /* sin almacenamiento: solo esta vez */ }
  }

  if (hidden) {
    return (
      <button onClick={() => hide(false)} className="text-xs font-semibold text-[#0070BA] hover:underline">
        {t("owner.tour.show")}
      </button>
    );
  }
  return (
    <section className="rounded-xl border border-[#009CDE]/30 bg-white p-3 sm:p-4 space-y-2">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-900">{t("owner.tour.title")}</h2>
          <p className="text-xs text-slate-500">{t(touring ? "owner.tour.introTour" : "owner.tour.introLive")}</p>
        </div>
        <button onClick={() => hide(true)} aria-label={t("owner.tour.hide")} className="text-slate-400 hover:text-slate-700">✕</button>
      </div>
      <ol className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
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
      </ol>
    </section>
  );
}
