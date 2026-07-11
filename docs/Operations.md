# Operations

## Local Use

The application is intended to run on one company-owned Windows computer. Other computers, tablets, and phones can use it through a browser on the local network or through Tailscale.

Start the local development server:

```powershell
.\run.bat
```

Health check:

```text
http://127.0.0.1:8000/health
```

## LAN And Tailscale Access

Use the LAN script when another device should access the app:

```powershell
.\run-lan.bat
```

Then open the server computer's LAN or Tailscale address in a browser, for example:

```text
http://100.x.x.x:8002
```

Only use this on trusted private networks or Tailscale. Do not port-forward the development server to the public internet.

## Sales, Shifts, Refunds, And Daily Closing

Work Orders, Sales, Payments, and Refunds are separate business objects. A Sale may link to a Work Order, but a Work Order is not treated as the payment record.

Daily closing rules:

- All shifts for the business date must be closed before the day can be closed.
- Closing creates a stored immutable snapshot with a version number.
- A closed business date blocks new shifts, sales, refunds, cash movements, and shift closing for that date.
- Only reopening the Daily Closing unlocks that date.
- Re-closing after reopen creates a new snapshot version and preserves older snapshot rows.
- Refunds cannot exceed the original sale total cumulatively.
- Refunds are recorded on the current open refund shift and the refunding seller, not on the original sale shift.
- The original sale remains on its original sale date and seller. Later refunds reduce the refund day and refunding seller totals.
- Refund VAT is stored with the refund. Single-VAT sales are supported; multi-VAT refunds require future line allocation.
- Snapshot version history is available from the Daily Closing detail page.

## Print Snapshots

Opening the printable receipt / work order route creates one stored snapshot for that document type. Later edits to the live work order do not rewrite the stored snapshot. Reopening the same printable route reuses the existing document number and snapshot.
