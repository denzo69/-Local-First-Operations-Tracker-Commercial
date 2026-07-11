# Security

The current MVP has no login session or authenticated current user. Forms that ask for seller/admin users are operational placeholders, not security controls.

Current role checks are business-rule validation only. They help keep workflows coherent, but they do not prove who is using the browser.

Do not expose the app directly to the public internet.

Recommended operating model for the MVP:

- run on a trusted company-owned Windows computer
- use only a trusted LAN or Tailscale
- avoid public port forwarding
- keep regular SQLite backups
- restrict machine access at the operating-system level

Planned hardening:

- secure session-based authentication
- current-user dependency
- CSRF protection for state-changing forms
- route-level permission enforcement
- removal of arbitrary seller/admin form selection from sensitive operations
