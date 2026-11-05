"use client";

import { splitSignalExplanation } from "@/lib/signal-fr";

export function SignalText({
  text,
  className = "",
}: {
  text: string | null | undefined;
  className?: string;
}) {
  const { lead, note } = splitSignalExplanation(text);
  if (!lead && !note) return null;
  return (
    <div className={className}>
      {lead ? <p>{lead}</p> : null}
      {note ? <p className="mt-1 text-muted">{note}</p> : null}
    </div>
  );
}
