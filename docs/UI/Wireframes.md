# UI Wireframes

The UI should be browser-based, responsive, and comfortable on Windows desktop, tablet, and phone.

## Main navigation

```text
Local-First Operations Tracker
--------------------------------------------------
Dashboard | Customers | Jobs | Products | Backups | Settings
```

## Dashboard

```text
--------------------------------------------------
Dashboard
--------------------------------------------------
[ + New Job ] [ + New Customer ]

Critical overview
--------------------------------------------------
Overdue                  2
Due today                4
Due tomorrow             5
Needs attention          3
Ready for pickup         7

Jobs needing attention
--------------------------------------------------
! Customer        Pickup date      Status       Actions
! Matti           Tomorrow         Washing      [Packed] [Ready]
! Liisa           Tomorrow         Received     [In progress]

Ready for pickup
--------------------------------------------------
Customer          Ready since      Actions
Pekka             Today            [Picked up] [Print]
```

## Customer list

```text
--------------------------------------------------
Customers
--------------------------------------------------
Search: [________________]
[ + New Customer ]

Name              Phone          Email          Actions
Matti Meikäläinen 040...         matti@...      [Open] [Edit]
```

## New job

```text
--------------------------------------------------
New Job
--------------------------------------------------
Customer:          [ Select existing customer ] [ + New ]
Title:             [___________________________]
Description:       [___________________________]
Arrival date:      [ 2026-07-09 ]
Pickup date:       [ 2026-07-10 ]
Status:            [ Received ]
Priority:          [ Normal ]
Notes:             [___________________________]

Products / services
--------------------------------------------------
[ + Add row ]

Product           Qty       Unit price       Total
Työtakki pesu     12        4.50             54.00

[ Save job ] [ Save and print receipt ]
```

## Job detail

```text
--------------------------------------------------
Job #2026-000001
--------------------------------------------------
Customer: Matti Meikäläinen
Phone: 040...
Arrival: 2026-07-09
Pickup: 2026-07-10
Status: Washing

[ Mark packed ] [ Mark ready ] [ Mark picked up ] [ Print receipt ]

Items
--------------------------------------------------
Työtakki pesu      12 x 4.50      54.00
Housut pesu         8 x 3.80      30.40

Total: 84.40
```

## Receipt preview

```text
--------------------------------------------------
Receipt preview
--------------------------------------------------
Receipt number: 2026-000001
Printed at: 2026-07-09 07:05

[ editable receipt text area / preview ]

[ Print ] [ Save print log ] [ Back ]
```

## Backup status

```text
--------------------------------------------------
Backups
--------------------------------------------------
Status: OK
Last local backup: 07:00
Last remote copy: not configured

[ Create backup now ]

Available backups
--------------------------------------------------
2026-07-09 07:00   app.sqlite.zip   [Restore]
2026-07-09 06:45   app.sqlite.zip   [Restore]
```

## Mobile dashboard idea

```text
Dashboard

[ + Job ]

Overdue: 2
Today: 4
Tomorrow: 5
Ready: 7

Needs attention
----------------
Matti
Pickup tomorrow
Status: Washing
[ Packed ]

Liisa
Pickup tomorrow
Status: Received
[ In progress ]
```
