import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { signOut } from "firebase/auth";
import { auth } from "../../firebase";
import LanguageSwitcher from "../LanguageSwitcher";
import type { Company } from "../../types";

// LA BARRA LATERAL DEL DUEÑO (FASE 16, 31-oct, pedido de Jose: "el mismo diseño del panel desplegable").
// Copia el DISEÑO de `Sidebar.tsx` (Firm): riel de 48 px que se expande a 240 px al acercar el mouse, encima del
// contenido; cajón en celular; marca arriba, el negocio abierto, las páginas en círculos, idioma y salir abajo.
// Separada A PROPÓSITO del de la consola (Jose, 31-oct): no comparten funciones (Auditoría, contadores, "Agregar
// libro", el recorrido del demo son del bookkeeper) y van a divergir: el portal se usa más en el celular y llevará
// la marca de la firma. Unificar solo si aparece una tercera cáscara (DEUDA TECNICA.md, ⚫ #32).

export type OwnerView = "summary" | "accounts" | "invoices";
export const OWNER_ORDER: OwnerView[] = ["summary", "accounts", "invoices"];

const ic = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" className="size-[18px] shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {d}
  </svg>
);
const ICONS: Record<OwnerView, ReactNode> = {
  summary: ic(<><path d="M4 19V5M4 19h16" /><path d="M8 16v-5M12 16V8M16 16v-3" /></>),
  accounts: ic(<><path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18" /></>),
  invoices: ic(<><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" /><path d="M9 8h6M9 12h6M9 16h3" /></>),
};
const LOGOUT_ICON = ic(<><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></>);

const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 220;

function initials(name: string | undefined): string {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "·";
}

export default function OwnerSidebar({ view, onNavigate, company, companies, onSwitch, preview, open, onClose }: {
  view: OwnerView;
  onNavigate: (v: OwnerView) => void;
  company: Company;
  companies: Company[];       // el selector sale solo si el DUEÑO tiene más de un negocio
  onSwitch: (c: Company) => void;
  preview: boolean;           // vista previa del bookkeeper: ni selector ni "Salir" (sale por la franja de arriba)
  open: boolean;              // en celular, el cajón
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState(false);
  const [picking, setPicking] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  const later = (value: boolean, ms: number) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHover(value), ms);
  };
  const canSwitch = !preview && companies.length > 1;

  const body = (expanded: boolean) => (
    <div className="h-full flex flex-col bg-white">
      <div className="h-14 shrink-0 flex items-center gap-2.5 px-4 border-b border-slate-200 overflow-hidden">
        <img src="/icon.svg" alt="ContaBu" className="size-8 shrink-0 rounded-lg shadow-sm" />
        {expanded && (
          <div className="font-bold tracking-tight whitespace-nowrap">
            <span className="text-brand-amber">Conta</span>
            <span className="text-brand-green">Bu</span>
          </div>
        )}
        {expanded && (
          <button onClick={onClose} aria-label={t("shell.close")} className="ms-auto md:hidden p-1 text-slate-400 hover:text-slate-600">✕</button>
        )}
      </div>

      {/* El negocio abierto, fijo arriba (como el libro en la consola). */}
      <div className="relative shrink-0 h-[60px] px-4 flex items-center border-b border-slate-200">
        {expanded ? (
          <div className="min-w-0 w-full">
            <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5 whitespace-nowrap">{t("owner.shell.business")}</div>
            {canSwitch ? (
              <button onClick={() => setPicking((p) => !p)} className="flex items-center gap-1 max-w-full text-start">
                <span className="text-sm font-medium text-slate-800 truncate">{company.name}</span>
                <svg viewBox="0 0 24 24" className="size-3.5 text-slate-400 shrink-0" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
              </button>
            ) : (
              <div className="text-sm font-medium text-slate-800 truncate">{company.name}</div>
            )}
          </div>
        ) : (
          <div title={company.name} className="size-8 shrink-0 rounded-full bg-brand-green/10 text-brand-green text-[11px] font-bold flex items-center justify-center">
            {initials(company.name)}
          </div>
        )}
        {expanded && picking && (
          <>
            <div className="fixed inset-0 z-20" onClick={() => setPicking(false)} />
            <div className="absolute z-30 top-full start-3 mt-1 w-56 rounded-xl border border-slate-200 bg-white shadow-lg overflow-hidden">
              {companies.map((c) => (
                <button key={c.id} onClick={() => { setPicking(false); onSwitch(c); onClose(); }}
                        className={`w-full px-3 py-2 text-sm text-start truncate hover:bg-slate-50 ${c.id === company.id ? "text-brand-green font-semibold" : "text-slate-700"}`}>
                  {c.name}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-3">
        <div className="h-5 mb-1 flex items-center px-2.5">
          {expanded ? (
            <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400 whitespace-nowrap">{t("owner.shell.portal")}</span>
          ) : (
            <span className="block w-full h-px bg-slate-200" />
          )}
        </div>
        <div className="space-y-0.5">
          {OWNER_ORDER.map((v) => {
            const active = view === v;
            return (
              <button key={v} onClick={() => { onNavigate(v); onClose(); }} aria-label={t(`owner.nav.${v}`)}
                      className={`w-full h-9 flex items-center gap-2.5 rounded-lg text-sm text-start transition-colors ${
                        expanded ? "ps-[7px] pe-2.5" : "justify-center"
                      } ${expanded && active ? "text-brand-green font-semibold" : expanded ? "text-slate-600 hover:bg-slate-50" : ""}`}>
                <span className={`relative size-8 shrink-0 rounded-full flex items-center justify-center transition-colors ${
                  active ? "bg-brand-green/10 text-brand-green" : "text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                }`}>
                  {ICONS[v]}
                </span>
                {expanded && <span className="flex-1 truncate whitespace-nowrap">{t(`owner.nav.${v}`)}</span>}
              </button>
            );
          })}
        </div>
      </nav>

      <div className="shrink-0 h-12 border-t border-slate-200 px-3 flex items-center gap-2 overflow-hidden">
        {expanded && <LanguageSwitcher />}
        {!preview && (
          <button onClick={() => signOut(auth)} aria-label={t("login.logout")} title={t("login.logout")}
                  className={`flex items-center gap-1.5 text-sm text-slate-500 hover:text-rose-500 rounded-lg whitespace-nowrap ${expanded ? "ms-auto px-2 py-1" : ""}`}>
            <span className="relative size-8 shrink-0 rounded-full flex items-center justify-center hover:bg-slate-100 hover:text-rose-500">
              {LOGOUT_ICON}
            </span>
            {expanded && t("login.logout")}
          </button>
        )}
      </div>
    </div>
  );

  return (
    <>
      <div className="hidden md:block w-12 shrink-0">
        <aside
          onMouseEnter={() => later(true, OPEN_DELAY_MS)}
          onMouseLeave={() => { later(false, CLOSE_DELAY_MS); setPicking(false); }}
          onFocus={() => later(true, 0)}
          onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) later(false, 0); }}
          className={`fixed inset-y-0 start-0 z-30 border-e border-slate-200 bg-white transition-[width,box-shadow] duration-200 ease-out ${
            hover ? "w-60 shadow-xl" : "w-12"
          }`}
        >
          {body(hover)}
        </aside>
      </div>
      {open && (
        <div className="md:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
          <aside className="absolute inset-y-0 start-0 w-64 shadow-xl">{body(true)}</aside>
        </div>
      )}
    </>
  );
}
