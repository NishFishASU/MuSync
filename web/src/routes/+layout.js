// SPA mode: static adapter + client-side routing; FastAPI owns /api/*.
// trailingSlash always → outputs/index.html so StaticFiles(html=True) deep-links work.
export const prerender = true;
export const ssr = false;
export const trailingSlash = 'always';
