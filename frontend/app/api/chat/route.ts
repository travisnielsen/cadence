/**
 * API Route - Chat
 *
 * This route is kept for backward compatibility but is no longer used.
 * The frontend now calls the FastAPI backend directly via lib/chatApi.ts
 *
 * If you need to add a proxy layer (e.g., for authentication), you can
 * implement it here to forward requests to the FastAPI backend.
 */

export async function POST(req: Request) {
  // The frontend now calls the FastAPI backend directly
  // This route is deprecated and returns a helpful message
  return new Response(
    JSON.stringify({
      error: "This endpoint is deprecated",
      message:
        "The frontend now communicates directly with the FastAPI backend. " +
        "Configure NEXT_PUBLIC_API_URL environment variable to point to your backend.",
    }),
    {
      status: 410, // Gone
      headers: { "Content-Type": "application/json" },
    }
  );
}

export async function GET() {
  return new Response(
    JSON.stringify({
      status: "deprecated",
      message: "Use FastAPI backend directly via NEXT_PUBLIC_API_URL",
    }),
    {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }
  );
}
