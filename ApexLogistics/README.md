# Apex Logistics — Dispatch desk

A responsive delivery-operations dashboard prototype using plain HTML, CSS, and JavaScript. It gives a small dispatch team a clear view of sample shipment activity without requiring a backend or account.

## Features

- Active, delivered, and attention-needed shipment summaries.
- Search shipments and filter by delivery status.
- Advance an in-transit shipment to delivered in the demo.
- Add a shipment through a validated form.
- Responsive route-board illustration and dispatch table.
- Persist demo changes in browser `localStorage`.

## Run it

Open `index.html` in a modern browser. No dependencies or server are required. The seeded routes and recipients are fictional sample records. Clearing browser storage resets the demo to its sample data.

## Learning notes

- The array in `app.js` is the current client-side data model.
- `render()` derives the dashboard counts and filtered shipment table from that model.
- Event listeners handle search, filtering, status changes, and the validated form.
- `escapeHtml()` keeps user-entered text from being interpreted as markup when rendered.

## Scope and limitations

This is a visual learning prototype. It does not connect to drivers, GPS, maps, messaging, or a dispatch server; actions change only local browser data. The on-time percentage is a simple demo metric based on delivered and attention-needed shipments, not an operational KPI. Contact details are fictional or masked.

## Next steps

- Add a Python/FastAPI service and a SQL database with shipment and status-history tables.
- Add authentication, server-side validation, and an audit trail.
- Add real route data only with a suitable map provider and authorized location consent.

## License

MIT — see `LICENSE`.
