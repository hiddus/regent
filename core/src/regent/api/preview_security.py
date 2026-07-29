"""Security headers for /preview/ static surfaces."""

# Preview sandbox: allow inline <style> so generated static HTML is not blank/unstyled.
# Scripts stay 'self' only (regent-preview.js / app.js).
PREVIEW_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'"
)
