# UI Screenshots

Current JEronAI Operations screenshots for reviewing the browser and mobile layouts.

These screenshots are included so the project is easier to understand from GitHub without running the application locally. They show the current Dashboard v2 layout and the unified product and inventory workspace.

## Browser Dashboard

The browser layout uses a persistent left navigation with collapsible sections and a dense operations dashboard. The dashboard prioritizes quick actions, work queues, upcoming work orders, recent activity, sales and invoicing, and daily closing state.

![Browser dashboard](screenshots/dashboard-desktop.png)

## Mobile Dashboard

The mobile layout keeps the same operational information but stacks actions and dashboard panels into a single readable column for phone use over LAN or Tailscale.

![Mobile dashboard](screenshots/dashboard-mobile.png)

## Products Workspace

The Products section now acts as the single product and inventory workspace. Product master data, warehouses, shelf locations, goods receipts, stock balances, inventory transactions, valuation, reconciliation, and suppliers are reachable from one place instead of being scattered across separate top-level modules.

![Products workspace](screenshots/products-workspace-desktop.png)

## Product Detail

The product detail page brings together the product overview, pricing, stock balances, recent goods receipts, inventory transactions, and valuation context for one product.

![Product detail](screenshots/product-detail-desktop.png)

## Stock Balances

The stock balance view focuses on operational inventory by product, warehouse, and shelf location. It remains a read-only view over the inventory ledger and derived balance caches.

![Stock balances](screenshots/products-stock-balances-desktop.png)

## Mobile Products Workspace

On mobile, the same Products workspace stacks into large tap targets and keeps receiving, stock history, valuation, and product management within the Products section.

![Mobile products workspace](screenshots/products-workspace-mobile.png)
