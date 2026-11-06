"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Web Push foundations (point 19). Permission must be explicit.
 * Public VAPID fetched at runtime from /api/engine/push/vapid-public.
 * Never private keys in repo or client bundle.
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

      const res = await fetch("/api/engine/push/vapid-public", { cache: "no-store" });
      if (!res.ok) {
        setDetail("Impossible de lire la clé VAPID publique (API).");
        return;
      }
      const json = (await res.json()) as {
        enabled?: boolean;
        public_key?: string | null;
        private_key_configured?: boolean;
      };
      if (!json.enabled) {
        setDetail("Web Push désactivé côté serveur (WEB_PUSH_ENABLED).");
        return;
      }
      const vapid = String(json.public_key || "").trim();
      if (!vapid) {
        setDetail(
          "Permission OK — clé publique VAPID absente. Pose VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY en Coolify.",
        );
        return;
      }
      if (!json.private_key_configured) {
        setDetail("Clé publique OK mais clé privée absente côté serveur — abonnement incomplet.");
      }
      const reg = await navigator.serviceWorker.ready;
      await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapid),
      });
      setDetail(
        json.private_key_configured
          ? "Abonnement push enregistré (VAPID)."
          : "Abonnement enregistré — complète VAPID_PRIVATE_KEY pour l’envoi serveur.",
      );
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
        Pas de Telegram. HTTPS requis. VAPID via env Coolify only.
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
