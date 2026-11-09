import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgAiChatMessage, AgAiChatSession, AgAiHarness, AgAiRunStatus } from "ag-studio";

// EL CHAT DEL ASISTENTE, PROPIO (8-nov, Jose: "al cerrar el chat los gráficos se encogen y vuelven").
// El panel de chat de AG Studio solo existe en su modo de edición, y al volver al modo vista AG Studio reacomodaba los
// widgets un instante. Este chat habla con el MISMO harness del Agent Framework (`createAiHarness`, nuestro adaptador y
// nuestras herramientas), pero lo dibuja ContaBu: el tablero se queda siempre en modo vista y no se mueve.

export interface ChatStarter { label: string; prompt: string }

export default function OwnerChat({ open, harness, starters, toolName, onClose }: {
  open: boolean; harness: AgAiHarness | null; starters: ChatStarter[]; toolName: (n: string) => string; onClose: () => void;
}) {
  const { t } = useTranslation();
  const [session, setSession] = useState<AgAiChatSession | null>(null);
  const [messages, setMessages] = useState<ReadonlyArray<AgAiChatMessage>>([]);
  const [status, setStatus] = useState<AgAiRunStatus>("idle");
  const [text, setText] = useState("");
  const [model, setModel] = useState<string | undefined>(harness?.models?.[0]?.id);
  const end = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  useEffect(() => {
    if (!session) return;
    const sync = () => { setMessages(session.messages); setStatus(session.status); };
    sync();
    session.addEventListener("changed", sync);
    return () => session.removeEventListener("changed", sync);
  }, [session]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [messages, status]);

  const running = status === "running";
  const send = async (msg: string) => {
    const q = msg.trim();
    if (!q || !harness || running) return;
    setText("");
    let s = session;
    if (!s) {
      s = await harness.createThread({ agentId: harness.agents[0]?.id ?? "collections", model: model ? { id: model } : undefined });
      setSession(s);
    }
    s.sendMessage(q, model ? { model: { id: model } } : undefined);
  };
  const fresh = () => { session?.close(); setSession(null); setMessages([]); setStatus("idle"); input.current?.focus(); };

  return (
    <div role="dialog" aria-label={t("owner.dashboard.ai.panel")} hidden={!open}
         className={`contabu-chat fixed z-[60] ${open ? "flex" : "hidden"} flex-col overflow-hidden rounded-2xl bg-white font-sans
                    shadow-[0_16px_48px_rgba(15,23,42,.22)] ring-1 ring-slate-900/10`}>
      <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2.5">
        <span className="grid size-7 place-items-center rounded-full bg-[#FFC439] text-[#003087]">
          <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /></svg>
        </span>
        <span className="text-sm font-semibold text-slate-900">{t("owner.dashboard.ai.panel")}</span>
        <button onClick={fresh} className="ms-auto rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100">{t("owner.dashboard.ai.new")}</button>
        <button onClick={onClose} aria-label={t("owner.dashboard.ai.close")} className="grid size-7 place-items-center rounded-md text-slate-500 hover:bg-slate-100">
          <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M6 12h12" /></svg>
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-auto px-3 py-3 text-sm">
        {messages.length === 0 && (
          <>
            <div className="whitespace-pre-line rounded-xl bg-slate-100 px-3.5 py-3 leading-relaxed text-slate-700">{t("owner.dashboard.ai.welcome")}</div>
            <div className="flex flex-wrap gap-1.5">
              {starters.map((s) => (
                <button key={s.label} onClick={() => void send(s.prompt)} disabled={!harness}
                        className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 hover:border-[#0070BA] hover:text-[#0070BA]">
                  {s.label}
                </button>
              ))}
            </div>
          </>
        )}
        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "flex justify-end" : ""}>
            {m.role === "user" ? (
              <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[#0070BA] px-3.5 py-2 text-white">
                {m.parts.map((p) => (p.type === "text" ? p.text : "")).join("")}
              </div>
            ) : (
              <div className="space-y-1.5">
                {m.parts.map((p, i) => p.type === "text" ? (
                  <p key={i} className="whitespace-pre-wrap leading-relaxed text-slate-800">{p.text}</p>
                ) : p.type === "tool_call" ? (
                  <div key={i} className="flex items-center gap-1.5 text-xs text-slate-500">
                    <span className={`size-1.5 rounded-full ${p.toolCall.status === "error" ? "bg-rose-500" : p.toolCall.status === "complete" ? "bg-emerald-500" : "bg-amber-400 animate-pulse"}`} />
                    {toolName(p.toolCall.name)}
                  </div>
                ) : p.type === "error" ? (
                  <p key={i} className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{p.message}</p>
                ) : null)}
              </div>
            )}
          </div>
        ))}
        {running && <div className="text-xs text-slate-500 animate-pulse">{t("owner.dashboard.ai.thinking")}</div>}
        <div ref={end} />
      </div>

      <div className="border-t border-slate-100 p-2.5">
        <div className="rounded-xl border border-slate-300 focus-within:border-[#0070BA] focus-within:ring-2 focus-within:ring-sky-100">
          <textarea ref={input} rows={2} value={text} onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }}
                    placeholder={t("owner.dashboard.ai.placeholder")} aria-label={t("owner.dashboard.ai.placeholder")}
                    className="block w-full resize-none rounded-xl bg-transparent px-3 pt-2 text-sm text-slate-800 outline-none" />
          <div className="flex items-center gap-2 px-2 pb-1.5">
            {(harness?.models?.length ?? 0) > 1 && (
              <select value={model} onChange={(e) => setModel(e.target.value)} aria-label={t("owner.dashboard.ai.model")}
                      className="max-w-[60%] rounded-md bg-transparent py-0.5 text-xs text-slate-500 outline-none hover:text-slate-700">
                {harness!.models!.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            )}
            {running ? (
              <button onClick={() => session?.cancel()} className="ms-auto rounded-md px-2.5 py-1 text-xs font-semibold text-rose-700 hover:bg-rose-50">{t("owner.dashboard.ai.stop")}</button>
            ) : (
              <button onClick={() => void send(text)} disabled={!text.trim() || !harness}
                      className="ms-auto rounded-md bg-[#0070BA] px-3 py-1 text-xs font-semibold text-white disabled:opacity-40">{t("owner.dashboard.ai.send")}</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
