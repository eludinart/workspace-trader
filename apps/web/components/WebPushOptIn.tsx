"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Web Push foundations (point 19). Permission must be explicit.
 * VAPID public key from NEXT_PUBLIC_VAPID_PUBLIC_KEY only — never private keys in repo.
 * No Telegram.
 */
export function WebPushOptIn() {
  const [status, setStatus] = useState<"idle" | "unsupported" | "denied" | "granted" | "error">(
    "idle",
  );
  const [detail, setDetail] = useState("Notifications push désactivées (opt-in).");

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("Notification" in window) || !("serviceWorker" in navigator)) {
      setStatus("unsupported");
      setDetail("Web Push non supporté sur cet appareil.");
      return;
    }
    const perm = Notification.permission;
    if (perm === "granted") {
      setStatus("granted");
      setDetail("Permission accordée. Abonnement actif seulement si VAPID est configuré.");
    } else if (perm === "denied") {
      setStatus("denied");
      setDetail("Permission refusée dans le navigateur.");
    }
  }, []);

  const request = useCallback(async () => {
    try {
      if (!("Notification" in window)) {
        setStatus("unsupported");
        return;
      }
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        setStatus(perm === "denied" ? "denied" : "idle");
        setDetail("Permission non accordée.");
        return;
      }
      setStatus("granted");
      const vapid = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY || "";
      if (!vapid) {
        setDetail(
          "Permission OK — configure NEXT_PUBLIC_VAPID_PUBLIC_KEY (HTTPS + domaine public). Clé privée côté serveur uniquement.",
        );
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapid),
      });
      setDetail("Abonnement push enregistré (VAPID public).");
    } catch (err) {
      setStatus("error");
      setDetail(err instanceof Error ? err.message : "Erreur push");
    }
  }, []);

  return (
    <div className="rounded-md border border-line/60 bg-ink/40 p-3 text-sm">
      <p className="font-medium text-slate-200">Alertes Web Push (PWA / Samsung)</p>
      <p className="mt-1 text-xs text-muted">{detail}</p>
      <p className="mt-1 text-[10px] text-muted">
        Pas de Telegram. HTTPS requis. VAPID via env only.
      </p>
      <button
        type="button"
        className="btn-secondary mt-2 text-xs"
        disabled={status === "unsupported" || status === "denied" || status === "granted"}
        onClick={() => void request()}
      >
        Autoriser les notifications
      </button>
    </div>
  );
}

function urlBase64ToUint8Array(base64String: string): BufferSource {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}
