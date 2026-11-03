import { useState } from "react";
import { useTranslation } from "react-i18next";
import { inTour } from "../../demo";

// LA BIENVENIDA DE `paypal.contabu.com` (FASE 16 Paso 7, Jose 2-nov): la herramienta que ContaBu le da al DUEÑO para
// cobrar con tarjeta. Se lee en tres pasos —factura, pago con tarjeta, en los libros— y lleva a Cobros. Se puede ocultar.

const HIDDEN_KEY = "contabu.cardWelcome.hidden.v1";
const STEPS = ["invoice", "card", "books"] as const;

// En la visita guiada no se recuerda: cada juez empieza con la pantalla completa (Jose, 3-nov).
function readHidden(): boolean {
  if (inTour()) return false;
  try { return localStorage.getItem(HIDDEN_KEY) === "1"; } catch { return false; }
}

export default function CardPaymentsWelcome({ onInvoices }: { onInvoices: () => void }) {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState(readHidden);
  if (hidden) return null;

  function hide() {
    setHidden(true);
    try { localStorage.setItem(HIDDEN_KEY, "1"); } catch { /* sin almacenamiento: solo esta vez */ }
  }

  return (
    <section className="rounded-xl bg-gradient-to-br from-[#003087] to-[#0070BA] text-white p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-[#FFC439]">{t("owner.welcome.kicker")}</p>
          <h2 className="mt-1 text-lg sm:text-xl font-semibold">{t("owner.welcome.title")}</h2>
          <p className="mt-1 text-sm text-white/85 max-w-3xl">{t("owner.welcome.body")}</p>
        </div>
        <button onClick={hide} aria-label={t("owner.welcome.hide")} className="text-white/70 hover:text-white">✕</button>
      </div>
      <ol className="mt-4 grid gap-2 sm:grid-cols-3">
        {STEPS.map((k, i) => (
          <li key={k} className="min-w-0 rounded-lg bg-white/10 px-3 py-2.5">
            <span className="flex items-center gap-2 text-sm font-semibold">
              <span className="grid size-6 shrink-0 place-items-center rounded-full bg-[#FFC439] text-xs font-bold text-[#003087]">{i + 1}</span>
              {t(`owner.welcome.steps.${k}.title`)}
            </span>
            <span className="mt-1 block text-xs text-white/80">{t(`owner.welcome.steps.${k}.body`)}</span>
          </li>
        ))}
      </ol>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button onClick={onInvoices} className="rounded-full bg-[#FFC439] px-4 py-2 text-sm font-semibold text-[#003087] hover:bg-[#f2ba36]">
          {t("owner.welcome.cta")}
        </button>
        <span className="text-xs text-white/75">{t("owner.welcome.fee")}</span>
      </div>
    </section>
  );
}
