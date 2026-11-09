"""Security headers for /preview/ static surfaces."""

# Preview sandbox: allow inline <style> so generated static HTML is not blank/unstyled.
# Scripts stay 'self' only (regent-preview.js / app.js).
#
# base-uri must allow 'self': runtime Preview injects
# ``<base href="/preview/runtime/{id}/">`` so relative nav/assets resolve under the
# path prefix. ``base-uri 'none'`` disables that tag and nested pages break
# (e.g. href="countries" from /crosswalks/US-SG → /crosswalks/countries).
PREVIEW_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'"
)
