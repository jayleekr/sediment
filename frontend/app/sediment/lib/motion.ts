"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// Motion that CSS cannot express, kept out of the page components.
//
// The Editorial-Archive motion tokens (duration/easing/keyframes) live in
// globals.css. What is here is the part that needs to observe state over
// time: pacing a token stream, and keeping a growing transcript pinned to
// the bottom of the window. Both honour `prefers-reduced-motion` by
// degrading to the instant behaviour rather than by being switched off —
// a reduced-motion user still needs to see the newest text.

/** Live value of the `prefers-reduced-motion` media query. */
export function usePrefersReducedMotion(): boolean {
  // Server render and first paint assume "no preference": the alternative
  // (assume reduce, then correct) makes every animation flash on for users
  // who asked for none, which is the exact wrong direction to be wrong in.
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

// SSE deltas do not arrive evenly. The backend emits whatever the provider
// flushed — a 4-character token, then 200 characters, then nothing for a
// beat — and rendering each delta the instant it lands is what makes the
// answer look like it is being stamped in blocks rather than written. This
// decouples arrival from display: the raw buffer is the target, and the
// revealed prefix walks toward it once per frame.
//
// Rate is proportional to the backlog rather than fixed — an exponential
// drain, so it is self-correcting at both ends. A burst is spread over a few
// hundred ms instead of painting at once, while a genuinely fast provider
// just raises the backlog and is served proportionally faster, which bounds
// the lag instead of letting it accumulate. It never runs ahead of what was
// actually received.
//
// The divisors below are the whole design. DRAIN_FRAMES is the time constant
// while the stream is live: ~10 frames (≈170ms) of smoothing, enough to turn
// a 200-character buffered flush into something that reads as writing, small
// enough that the display never trails far behind the real answer.
// CLOSING_FRAMES takes over once the stream has ended — there is no longer
// anything to stay close to, only a tail to finish, so it finishes it about
// three times faster rather than making the reader watch a drained buffer
// play out.
// DRAIN_CEIL caps the leading edge. The exponential alone still opens a large
// flush with a ~70-character first step, which is two lines appearing at once
// — smoother than the unpaced version, but the same defect in miniature. The
// cap is set well above any sustained rate a provider actually streams at
// (~720 chars/s at 60fps), so in normal operation it only ever shaves the
// spike. LAG_LIMIT is the safety valve: if the backlog ever exceeds it the cap
// is lifted, so a pathologically fast stream can fall at most that far behind
// instead of accumulating lag without bound.
const DRAIN_FRAMES = 10;
const DRAIN_FLOOR = 2;
const DRAIN_CEIL = 12;
const LAG_LIMIT = 240;
const CLOSING_FRAMES = 3;
const CLOSING_FLOOR = 8;

export function useSmoothStream(target: string, active: boolean): string {
  const reduced = usePrefersReducedMotion();
  const [len, setLen] = useState(target.length);
  const targetRef = useRef(target);
  const lenRef = useRef(len);
  const activeRef = useRef(active);
  const rafRef = useRef<number | null>(null);

  targetRef.current = target;
  lenRef.current = len;
  activeRef.current = active;

  const stop = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  useEffect(() => {
    // A shorter target means the buffer was replaced, not appended to: a new
    // turn started, or `onDone` cleared the live bubble because the persisted
    // row now renders the answer. There is no continuity to preserve, so land
    // on it directly — easing here would animate a *deletion*.
    if (reduced || target.length < lenRef.current) {
      stop();
      setLen(target.length);
      return;
    }
    if (rafRef.current !== null) return; // already draining

    const tick = () => {
      const backlog = targetRef.current.length - lenRef.current;
      if (backlog <= 0) {
        rafRef.current = null;
        return;
      }
      // Read `active` from the ref, not the closure: the stream ending must
      // switch this loop to its closing rate mid-flight, and a captured value
      // would keep it at the live rate until the effect happened to re-run.
      const closing = !activeRef.current;
      const frames = closing ? CLOSING_FRAMES : DRAIN_FRAMES;
      const floor = closing ? CLOSING_FLOOR : DRAIN_FLOOR;
      let step = Math.max(floor, Math.ceil(backlog / frames));
      // The tail is uncapped — once the stream has ended there is nothing left
      // to stay close to, and finishing promptly beats finishing prettily.
      if (!closing && backlog <= LAG_LIMIT) step = Math.min(step, DRAIN_CEIL);
      setLen((l) => Math.min(targetRef.current.length, l + step));
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [target, active, reduced, stop]);

  useEffect(() => stop, [stop]);

  return target.slice(0, len);
}

/** Distance in px from the bottom of the document within which we consider
 *  the reader to be "following along" rather than reading back. Roughly one
 *  short paragraph of slack, so a token that pushes the page down by a line
 *  does not count as the reader having scrolled away. */
const STICK_THRESHOLD_PX = 140;

function distanceFromBottom(): number {
  return (
    document.documentElement.scrollHeight - window.innerHeight - window.scrollY
  );
}

// Keeps the window pinned to the bottom while `watch` changes — but only
// while the reader has not scrolled away. Scrolling up during a stream is a
// deliberate act (checking a citation, re-reading a paragraph); yanking the
// viewport back is the single most hostile thing a chat UI can do.
//
// Returns `stuck` so the caller can offer a way back down once the reader
// has left the bottom.
export function useStickToBottom(
  watch: unknown,
  active: boolean,
): { stuck: boolean; scrollToBottom: () => void } {
  const [stuck, setStuck] = useState(true);
  const stuckRef = useRef(true);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const onScroll = () => {
      const next = distanceFromBottom() < STICK_THRESHOLD_PX;
      if (next !== stuckRef.current) {
        stuckRef.current = next;
        setStuck(next);
      }
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  const pin = useCallback(() => {
    if (!stuckRef.current) return;
    // `behavior: "auto"` on purpose, and why `html { scroll-behavior: smooth }`
    // is overridden here: a smooth scroll re-issued every frame restarts its
    // own animation every frame and converges on nothing. Because the text
    // now arrives a few characters at a time, an instant scroll per frame IS
    // the smooth one — the viewport creeps with the text instead of chasing it.
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "auto" });
  }, []);

  useEffect(() => {
    if (!active) return;
    pin();
  }, [watch, active, pin]);

  // Reacting to the streamed text alone under-counts: the transcript also
  // grows in commits the text does not drive — a citation landing in the
  // sidebar, the feedback row appearing under a finished answer, a markdown
  // table reflowing, a font swapping in. Each of those left the viewport a
  // little short of the bottom, and the shortfall accumulated. Watching the
  // rendered height instead catches every one of them.
  useEffect(() => {
    if (!active || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => pin());
    ro.observe(document.body);
    return () => ro.disconnect();
  }, [active, pin]);

  const scrollToBottom = useCallback(() => {
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: reduced ? "auto" : "smooth",
    });
  }, [reduced]);

  return { stuck, scrollToBottom };
}
