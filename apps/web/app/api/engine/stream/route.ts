export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function engineBase() {
  return process.env.ENGINE_URL || "http://127.0.0.1:8100";
}

export async function GET() {
  const upstream = await fetch(`${engineBase()}/stream`, {
    headers: { Accept: "text/event-stream" },
    cache: "no-store",
  });

  if (!upstream.ok || !upstream.body) {
    return new Response(JSON.stringify({ detail: "stream unavailable" }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
