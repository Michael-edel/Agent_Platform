"""SQLAdmin authentication backend."""

import os
import logging
from typing import Optional
from starlette.requests import Request
from starlette.responses import Response
from sqladmin.authentication import AuthenticationBackend

logger = logging.getLogger(__name__)


class AdminAuthBackend(AuthenticationBackend):
    """
    Authentication backend for SQLAdmin.
    
    Supports two roles:
    - platform_admin: can see all tenants
    - tenant_admin: can only see own tenant_id
    """
    
    async def login(self, request: Request) -> bool:
        """Handle login form submission."""
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")
        
        expected_username = os.getenv("ADMIN_USERNAME", "admin")
        expected_password = os.getenv("ADMIN_PASSWORD", "")
        
        if not expected_password:
            logger.warning("ADMIN_PASSWORD not set, denying login")
            return False
        
        if username == expected_username and password == expected_password:
            # Get role and tenant_id from env
            role = os.getenv("ADMIN_ROLE", "platform_admin")
            tenant_id = os.getenv("ADMIN_TENANT_ID", "")
            
            # Store in session
            request.session.update({
                "admin_logged_in": True,
                "admin_role": role,
                "admin_tenant_id": tenant_id if role == "tenant_admin" else "",
            })
            
            logger.info(f"Admin login successful: role={role}")
            return True
        
        logger.warning("Admin login failed: invalid credentials")
        return False
    
    async def logout(self, request: Request) -> bool:
        """Handle logout."""
        request.session.clear()
        logger.info("Admin logout")
        return True
    
    async def authenticate(self, request: Request) -> Optional[Response]:
        """
        Check if user is authenticated.
        
        Returns None if authenticated, redirect response if not.
        """
        if request.session.get("admin_logged_in"):
            return None
        
        # Not authenticated - SQLAdmin will redirect to login
        return None


def get_admin_role(request: Request) -> Optional[str]:
    """Get current admin role from session. Returns None if not set (deny by default)."""
    return request.session.get("admin_role")


def get_admin_tenant_id(request: Request) -> Optional[str]:
    """Get current admin tenant_id from session (for tenant_admin role)."""
    role = get_admin_role(request)
    if role == "tenant_admin":
        return request.session.get("admin_tenant_id")
    return None
