"""Admin endpoint for rotating tenant portal tokens."""

import logging
from starlette.requests import Request
from starlette.responses import Response, HTMLResponse
from sqladmin import BaseView, expose

from app.admin.auth import get_admin_role
from app.api.tenant_portal import create_portal_token

logger = logging.getLogger(__name__)


class TokenRotateView(BaseView):
    """View for rotating tenant portal tokens."""
    
    name = "Rotate Token"
    icon = "fa-solid fa-rotate"
    
    @expose("/rotate-token", methods=["GET", "POST"])
    async def rotate_token(self, request: Request) -> Response:
        """Rotate portal token for a tenant."""
        role = get_admin_role(request)
        
        # Platform admin only
        if role != "platform_admin":
            return HTMLResponse(
                "<h3>Access Denied</h3><p>Only platform_admin can rotate tokens.</p>",
                status_code=403,
            )
        
        tenant_id = request.query_params.get("tenant_id", "")
        plain_token = None
        error = None
        
        if request.method == "POST":
            form = await request.form()
            tenant_id = form.get("tenant_id", "")
            
            if not tenant_id:
                error = "tenant_id is required"
            else:
                try:
                    plain_token, token_id = create_portal_token(tenant_id)
                    logger.info(f"Rotated portal token for tenant {tenant_id}")
                except Exception as e:
                    logger.exception(f"Failed to rotate token for {tenant_id}")
                    error = str(e)[:100]
        
        # Render simple HTML form
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Rotate Tenant Portal Token</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head>
        <body class="bg-light">
            <div class="container mt-5">
                <h3>Rotate Tenant Portal Token</h3>
                
                {f'<div class="alert alert-danger">{error}</div>' if error else ''}
                
                {f'''
                <div class="alert alert-success">
                    <h5>Token Created Successfully!</h5>
                    <p><strong>Tenant ID:</strong> {tenant_id}</p>
                    <p><strong>Token (copy now - will not be shown again):</strong></p>
                    <pre class="bg-white p-3 border">{plain_token}</pre>
                    <p class="text-muted">Store this token securely. It cannot be recovered.</p>
                </div>
                ''' if plain_token else ''}
                
                <form method="post" class="mt-4">
                    <div class="mb-3">
                        <label class="form-label">Tenant ID</label>
                        <input type="text" name="tenant_id" class="form-control" 
                               value="{tenant_id}" required 
                               placeholder="e.g., tenant-123">
                    </div>
                    <button type="submit" class="btn btn-primary">
                        {'Rotate Another' if plain_token else 'Rotate Token'}
                    </button>
                    <a href="/admin/" class="btn btn-secondary ms-2">Back to Admin</a>
                </form>
                
                <div class="mt-4 text-muted">
                    <small>
                        Note: Rotating creates a new token and revokes any existing active token for this tenant.
                    </small>
                </div>
            </div>
        </body>
        </html>
        """
        
        return HTMLResponse(html)
