# Security Policy

## Supported use

JEronAI Operations is designed for trusted private environments such as a company-owned computer, a trusted local network, or Tailscale. It is not intended to be exposed directly to the public internet.

## Reporting a vulnerability

Please do not publish suspected vulnerabilities in a public GitHub issue.

Report security concerns privately to the repository owner and include:

- a clear description of the issue
- affected version or commit
- reproduction steps
- expected and actual behavior
- potential impact
- any suggested mitigation

Please allow reasonable time for investigation and remediation before public disclosure.

## Deployment requirements

Before real use:

- configure a unique high-entropy `SECRET_KEY`
- do not use known placeholder secrets
- keep the application on a trusted LAN or Tailscale network
- use HTTPS through a trusted reverse proxy when browser traffic crosses networks you do not fully control
- keep Python dependencies and the host operating system updated
- maintain tested backups outside the application process when stronger recovery guarantees are required

Production startup rejects known placeholder signing secrets. Docker Compose also requires `SECRET_KEY` to be supplied explicitly.

## Scope and limitations

The current authentication and session design is intended for the documented local-first deployment model. It has not been hardened as a public multi-tenant internet service.
