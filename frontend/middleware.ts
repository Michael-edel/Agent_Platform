import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  // Only check tenant ID for app routes (not settings)
  if (request.nextUrl.pathname.startsWith('/app/') && 
      request.nextUrl.pathname !== '/app/settings') {
    // Check tenant ID from cookie or header (we'll use localStorage on client side)
    // For now, we'll let the client-side handle the redirect
    return NextResponse.next();
  }

  return NextResponse.next();
}

export const config = {
  matcher: '/app/:path*',
};
