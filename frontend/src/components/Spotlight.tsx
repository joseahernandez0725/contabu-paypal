import { useEffect, useReducer, useState, type CSSProperties } from "react";

// EL FOCO DE LAS GUÍAS (5-nov; separado de `DemoGuide.tsx`, la guía de `demo.contabu.com`, para que la de
// `paypal.contabu.com` use el mismo). La pantalla se oscurece, un recorte claro y redondeado sobre lo que se explica, y
// fuera del recorte nada recibe clics. Las guías dicen QUÉ iluminar (un selector `data-tour="…"`); esto lo sigue cuadro a
// cuadro (scroll, cambio de tamaño) y dice dónde va la burbuja sin taparlo. En el celular, la burbuja va abajo (o arriba).

export const PAD = 6;          // el aire alrededor de lo iluminado
export const GAP = 12;         // entre lo iluminado y la burbuja
export const BUBBLE_W = 340;

export type Box = { top: number; left: number; width: number; height: number };

/** El primer elemento VISIBLE que cumple el selector (la barra lateral tiene una copia oculta en el celular). */
export function visible(sel: string): HTMLElement | null {
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}

export async function waitFor(sel: string, ms = 4000): Promise<HTMLElement | null> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const el = visible(sel);
    if (el) return el;
    await new Promise((r) => window.setTimeout(r, 100));
  }
  return null;
}

export async function clickWhenReady(sel: string, ms?: number): Promise<boolean> {
  const el = await waitFor(sel, ms);
  if (el) el.click();
  return !!el;
}

/** El rectángulo de `el` recortado por los contenedores con scroll que lo cortan (un panel angosto, la grilla…):
 *  sin esto, el recorte iluminaría también lo que ese contenedor esconde. null si no se ve nada. */
export function clipped(el: HTMLElement): { top: number; left: number; right: number; bottom: number } | null {
  const r = el.getBoundingClientRect();
  let { top, left, right, bottom } = r;
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const o = getComputedStyle(p);
    if ([o.overflowX, o.overflowY].every((v) => v === "visible")) continue;
    const c = p.getBoundingClientRect();
    top = Math.max(top, c.top); left = Math.max(left, c.left); right = Math.min(right, c.right); bottom = Math.min(bottom, c.bottom);
  }
  return right > left && bottom > top ? { top, left, right, bottom } : null;
}

/** El recorte sobre `target`, seguido cuadro a cuadro (scroll, cambio de tamaño, la barra que se ensancha). */
export function useHole(target: string | null): Box | null {
  const [hole, setHole] = useState<Box | null>(null);
  useEffect(() => {
    if (!target) { setHole(null); return; }
    let raf = 0;
    const tick = () => {
      const el = visible(target);
      const r = el ? clipped(el) : null;
      const w = window.innerWidth, h = window.innerHeight;
      setHole((prev) => {
        if (!r) return null;
        const top = Math.max(r.top - PAD, 4), left = Math.max(r.left - PAD, 4);
        const box = { top, left, width: Math.min(r.right + PAD, w - 4) - left, height: Math.min(r.bottom + PAD, h - 4) - top };
        return prev && Math.abs(prev.top - box.top) < 0.5 && Math.abs(prev.left - box.left) < 0.5
          && Math.abs(prev.width - box.width) < 0.5 && Math.abs(prev.height - box.height) < 0.5 ? prev : box;
      });
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return hole;
}

/** El tamaño de la ventana, leído AL PINTAR (guardarlo al montar falla si el panel aún no tenía tamaño). */
export function useViewport(): { w: number; h: number } {
  const [, repaint] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    window.addEventListener("resize", repaint);
    return () => window.removeEventListener("resize", repaint);
  }, []);
  return { w: window.innerWidth, h: window.innerHeight };
}

/** Dónde va la burbuja: al lado de lo iluminado sin taparlo; en el celular, abajo (o arriba si lo iluminado está abajo). */
export function bubblePosition(hole: Box | null, centered: boolean, vp: { w: number; h: number }, bubbleH: number): CSSProperties {
  if (centered || !hole) {
    return { top: Math.max(16, (vp.h - bubbleH) / 2), left: Math.max(16, (vp.w - Math.min(440, vp.w - 32)) / 2), width: Math.min(440, vp.w - 32) };
  }
  if (vp.w < 640) {
    const holeLow = hole.top + hole.height / 2 > vp.h / 2;
    return holeLow ? { top: 8, left: 8, right: 8 } : { bottom: 8, left: 8, right: 8 };
  }
  const clampTop = (y: number) => Math.min(Math.max(8, y), vp.h - bubbleH - 8);
  const clampLeft = (x: number) => Math.min(Math.max(8, x), vp.w - BUBBLE_W - 8);
  if (hole.left + hole.width + GAP + BUBBLE_W <= vp.w - 8) return { top: clampTop(hole.top), left: hole.left + hole.width + GAP, width: BUBBLE_W };
  if (hole.left - GAP - BUBBLE_W >= 8) return { top: clampTop(hole.top), left: hole.left - GAP - BUBBLE_W, width: BUBBLE_W };
  if (hole.top + hole.height + GAP + bubbleH <= vp.h - 8) return { top: hole.top + hole.height + GAP, left: clampLeft(hole.left), width: BUBBLE_W };
  if (hole.top - GAP - bubbleH >= 8) return { top: hole.top - GAP - bubbleH, left: clampLeft(hole.left), width: BUBBLE_W };
  return { bottom: 8, right: 8, width: BUBBLE_W };   // lo iluminado ocupa casi todo: a la esquina
}

/** Lo oscuro, con el recorte. Fuera del recorte nada recibe clics; dentro, sí, salvo con `shielded` (un tapón: lo
 *  iluminado se ve pero no se toca, la visita es de solo lectura también por dentro). */
export function SpotlightBackdrop({ hole, centered, shielded }: { hole: Box | null; centered: boolean; shielded: boolean }) {
  if (!hole || centered) return <div className="fixed inset-0 bg-slate-900/50 pointer-events-auto" />;
  return (
    <>
      <div className="pointer-events-none fixed rounded-xl ring-2 ring-white/90 transition-all duration-150"
           style={{ ...hole, boxShadow: "0 0 0 9999px rgba(15, 23, 42, 0.5)" }} />
      <div className="fixed pointer-events-auto" style={{ top: 0, left: 0, right: 0, height: hole.top }} />
      <div className="fixed pointer-events-auto" style={{ top: hole.top + hole.height, left: 0, right: 0, bottom: 0 }} />
      <div className="fixed pointer-events-auto" style={{ top: hole.top, left: 0, width: hole.left, height: hole.height }} />
      <div className="fixed pointer-events-auto" style={{ top: hole.top, left: hole.left + hole.width, right: 0, height: hole.height }} />
      {shielded && <div className="fixed pointer-events-auto" style={hole} />}
    </>
  );
}
