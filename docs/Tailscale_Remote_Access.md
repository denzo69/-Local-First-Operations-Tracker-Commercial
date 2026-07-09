# Tailscale Remote Access Guide

## Purpose

Tailscale can be used to access a local Local-First Operations Tracker server securely from outside the local network without opening router ports to the public internet.

This is the recommended remote access method for development and small private deployments.

## Basic idea

```text
Phone / laptop outside home
        |
        | Tailscale private network
        v
Windows server PC running the app
        |
        v
Local-First Operations Tracker
```

## Requirements

- Tailscale installed on the Windows server PC
- Tailscale installed on the phone or remote device
- both devices logged into the same Tailscale account / tailnet
- application started in LAN mode using `run-lan.bat`

## Start the app for Tailscale access

On the Windows server PC:

```powershell
git pull
.\run-lan.bat
```

`run-lan.bat` starts the app with:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

This makes the app reachable through local network interfaces, including the Tailscale network interface.

## Find the server's Tailscale IP

On the Windows server PC:

```powershell
tailscale ip -4
```

Example result:

```text
100.101.102.103
```

Then open the app from the phone or remote device:

```text
http://100.101.102.103:8000
```

## Optional: use MagicDNS

If MagicDNS is enabled in Tailscale, the server may also be reachable with a device name.

Example:

```text
http://server-device-name:8000
```

## Security notes

- Do not expose port 8000 directly to the public internet.
- Use Tailscale or another VPN for remote access during development.
- Later production deployments should add login, HTTPS, user roles, and audit logging.
- Keep the app available only to trusted devices in the tailnet.

## Troubleshooting

### The app works on the server but not through Tailscale

Check that the app was started with `run-lan.bat`, not `run.bat`.

`run.bat` binds only to:

```text
127.0.0.1
```

`run-lan.bat` binds to:

```text
0.0.0.0
```

### Windows Firewall blocks access

Allow Python/Uvicorn on private networks when Windows asks.

### Phone cannot connect

Check:

- phone is connected to Tailscale
- server PC is connected to Tailscale
- both devices are in the same tailnet
- app is running
- correct Tailscale IP and port are used

## Recommended development URL format

```text
http://TAILSCALE_IP:8000
```

Example:

```text
http://100.101.102.103:8000
```
