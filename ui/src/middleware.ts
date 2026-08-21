import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

import { getServerBackendUrl } from '@/lib/apiClient';

const OSS_TOKEN_COOKIE = 'dograh_auth_token';

// Paths that don't require authentication in OSS mode
const PUBLIC_PATHS = ['/auth/login', '/auth/signup'];

let cachedAuthProvider: string | null = null;
// Timestamp of last 'unknown' result. We retry after UNKNOWN_TTL_MS so a
// cold-starting Render backend is re-queried periodically rather than on
// every single page request (which would each spend 1s waiting for timeout).
let unknownCachedAt: number = 0;
const UNKNOWN_TTL_MS = 30_000; // retry backend after 30 s of being unreachable
// Hard cap on the health-check round-trip. Vercel middleware has a 1.5 s limit;
// keeping this at 1 s leaves headroom for JS execution overhead.
const HEALTH_FETCH_TIMEOUT_MS = 1000;

async function fetchAuthProvider(): Promise<string> {
  if (cachedAuthProvider) {
    return cachedAuthProvider;
  }

  // Serve the cached 'unknown' result while the backend is still cold-starting
  // (avoids burning 1 s per request during a cold-start window).
  if (unknownCachedAt > 0 && Date.now() - unknownCachedAt < UNKNOWN_TTL_MS) {
    return 'unknown';
  }

  try {
    const backendUrl = getServerBackendUrl();
    const res = await fetch(`${backendUrl}/api/v1/health`, {
      // Abort after 1 s — Vercel middleware limit is 1.5 s; this keeps us safe.
      signal: AbortSignal.timeout(HEALTH_FETCH_TIMEOUT_MS),
      // No caching: always read the live backend response.
      cache: 'no-store',
    });
    if (res.ok) {
      const data = await res.json();
      // Only cache a DEFINITIVE answer from the backend. Never cache a failure:
      // this is a module-scoped cache with no TTL, so a single early request
      // during container startup (before the api service is reachable) would
      // otherwise poison it to 'local' for the life of the worker — redirecting
      // every Stack user to the local /auth/login form even though the backend
      // reports `stack`.
      cachedAuthProvider = (data.auth_provider as string) || 'local';
      return cachedAuthProvider;
    }
  } catch {
    // Backend not reachable or timed out — record when this happened so we can
    // serve the 'unknown' sentinel cheaply for the next UNKNOWN_TTL_MS ms.
    unknownCachedAt = Date.now();
  }

  // Provider unknown (backend unreachable). Return a non-'local' sentinel so the
  // middleware does NOT guard/redirect: assuming 'local' here would bounce Stack
  // users to /auth/login. Deliberately not cached — the next request retries
  // after UNKNOWN_TTL_MS.
  return 'unknown';
}

export async function middleware(request: NextRequest) {
  const authProvider = await fetchAuthProvider();

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.next();
  }

  const token = request.cookies.get(OSS_TOKEN_COOKIE)?.value;
  const { pathname } = request.nextUrl;

  // Allow public paths without auth
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // If no token, redirect to login
  if (!token) {
    const loginUrl = new URL('/auth/login', request.url);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

// Configure which routes the middleware runs on
export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - api routes
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public static assets (anything with a file extension, e.g. /dograh-logo.png)
     */
    '/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|otf)).*)',
  ],
};
