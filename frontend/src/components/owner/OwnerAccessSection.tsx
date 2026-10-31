import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getOwnerAccess, inviteOwner, removeOwnerAccess, type OwnerAccess } from "../../api";
import { BTN_SECONDARY } from "../PageKit";
import { openOwnerPreview } from "./OwnerApp";

// EL DUEÑO DEL NEGOCIO, visto desde la consola (FASE 16, área PayPal / portal del dueño · 31-oct-2026).
// Vive en Ajustes, SEPARADO del equipo a propósito (FASE 13.I Paso 8): si el dueño estuviera en la misma
// lista que el equipo, el día del apuro se le invita como MEMBER y ve el libro entero.
// Invitar y quitar: solo el OWNER del libro (como el equipo). "Ver como el dueño": cualquiera de la consola.
// Hoy invitar no manda correo (FASE 13.I Paso 11): el bookkeeper le avisa al dueño.

export default function OwnerAccessSection({ canInvite }: { canInvite: boolean }) {
  const { t } = useTranslation();
  const [owners, setOwners] = useState<OwnerAccess[] | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "error" | "warn"; text: string } | null>(null);

  const load = () => getOwnerAccess().then(setOwners).catch(() => setOwners([]));
  useEffect(() => { load(); }, []);

  async function invite() {
    if (!email.trim()) return;
    setBusy(true); setMsg(null);
    try {
      const r = await inviteOwner(email.trim());
      setEmail("");
      if (r.warning === "team_elsewhere") setMsg({ tone: "warn", text: t("owner.access.teamElsewhere") });
      await load();
    } catch (e) {
      const code = e instanceof Error ? e.message : "";
      setMsg({ tone: "error", text: code === "already_on_team" ? t("owner.access.alreadyOnTeam") : t("owner.access.inviteError") });
    } finally { setBusy(false); }
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-3 space-y-4">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-900">{t("owner.access.title")}</h3>
          <p className="text-xs text-slate-500 mt-1">{t("owner.access.help")}</p>
        </div>
        <button onClick={openOwnerPreview} className={BTN_SECONDARY}>{t("owner.access.preview")}</button>
      </div>

      <div className="space-y-1">
        {owners && owners.length === 0 && <p className="text-sm text-slate-400">{t("owner.access.none")}</p>}
        {(owners ?? []).map((o) => (
          <div key={o.id} className="flex items-center justify-between gap-2 py-1.5 text-sm">
            <span className="min-w-0 flex-1 truncate text-slate-700">
              {o.email}
              {o.pending && <span className="ms-1 text-[10px] uppercase text-amber-600 bg-amber-50 rounded px-1">{t("settings.pending", { defaultValue: "pending" })}</span>}
            </span>
            {canInvite && (
              <button onClick={async () => { await removeOwnerAccess(o.id); load(); }}
                      className="text-slate-400 hover:text-rose-600" aria-label={t("owner.access.remove")} title={t("owner.access.remove")}>
                <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                </svg>
              </button>
            )}
          </div>
        ))}
      </div>

      {msg && <div className={`text-sm ${msg.tone === "error" ? "text-rose-600" : "text-amber-700"}`}>{msg.text}</div>}

      {canInvite && (
        <div className="flex items-center gap-2 border-t border-slate-100 pt-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") invite(); }}
            placeholder={t("owner.access.placeholder")}
            className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40"
          />
          <button onClick={invite} disabled={busy || !email.trim()}
            className="px-3 py-2 rounded-lg text-sm font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-50">
            {t("owner.access.invite")}
          </button>
        </div>
      )}
    </div>
  );
}
