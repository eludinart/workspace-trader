"use client";

import { useEffect } from "react";

/**
 * Clears stale service workers (common cause of removeChild / hydration DOM fights),
 * then registers a lightweight PWA worker in production only.
 */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;

    const reset = async () => {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
      if ("caches" in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
      if (process.env.NODE_ENV === "production") {
        await navigator.serviceWorker.register("/sw.js");
      }
    };

    void reset();
  }, []);

  return null;
}
