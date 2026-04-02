import type { NextRequest } from "next/server";

import { proxyRequest } from "@/components/lib/http/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function proxy(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const { path } = await ctx.params;
  return proxyRequest(request, { path });
}

export function GET(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, ctx);
}

export function HEAD(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, ctx);
}

export function OPTIONS(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, ctx);
}

export function POST(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, ctx);
}

export function PUT(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, ctx);
}

export function PATCH(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, ctx);
}

export function DELETE(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, ctx);
}
