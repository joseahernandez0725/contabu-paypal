import { useTranslation } from "react-i18next";
import { money, type AiCall } from "../../api";

// "WHAT THE AI DID" (FASE 16, 5-nov). Los pasos de UNA acción de la IA, en orden, tal como pasaron: lo que leyó, la
// herramienta del PayPal Agent Toolkit que quedó preparada, lo que validó el código y lo que espera al dueño. El modelo,
// el tiempo y el costo son los de ESA llamada (`nemotron.Usage`, los mismos que guarda `agent_calls`). Nada se inventa:
// cada paso sale de la respuesta del backend.

export type TraceStep = { label: string; detail?: string; tone?: "ok" | "wait" | "warn" };

export default function AiTrace({ ai, steps }: { ai: AiCall | undefined; steps: TraceStep[] }) {
  const { t } = useTranslation();
  if (!ai) return null;
  const secs = (ai.latency_ms / 1000).toFixed(1);
  const cost = ai.cost_usd == null ? "—" : ai.cost_usd < 0.01 ? `$${ai.cost_usd.toFixed(5)}` : money(ai.cost_usd);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-2.5 text-xs">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-semibold text-slate-900">{t("owner.ai.title")}</span>
        <span className="text-slate-500">{t("owner.ai.meta", { model: ai.model.split("/").pop(), secs, cost, tokens: ai.tokens })}</span>
      </div>
      <ol className="mt-1.5 space-y-1">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white ${s.tone === "wait" ? "bg-amber-500" : s.tone === "warn" ? "bg-rose-500" : "bg-emerald-600"}`}>{i + 1}</span>
            <span className="min-w-0">
              <span className="text-slate-800">{s.label}</span>
              {s.detail && <span className="block text-slate-500 break-words">{s.detail}</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
