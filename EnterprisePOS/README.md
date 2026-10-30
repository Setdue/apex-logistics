# EnterprisePOS — Northstar POS demo

A responsive, local-first point-of-sale and inventory prototype for a small retailer. Built with plain HTML, CSS, and JavaScript so it can be explored without installing packages or creating an account.

## Features

- Search and filter a seeded product catalogue.
- Build a cart with quantity and stock-limit checks.
- Calculate a clearly labelled 7.5% demo VAT and order total in Nigerian naira.
- Complete a simulated sale, reduce stock, and keep a recent-sales history.
- See low-stock warnings and reset the seeded demo data.
- Save state in this browser using `localStorage`.

## Run it

Open `index.html` in a modern browser. No server or dependencies are needed. Use **Reset demo data** to restore the original catalogue. Data is stored only in the current browser profile.

## Learning notes

1. `index.html` describes the interface and the IDs that JavaScript updates.
2. `styles.css` uses CSS Grid and media queries to adapt the layout for mobile.
3. `app.js` owns the product, cart, and sales state; `render()` turns that state into the screen.
4. The checkout handler validates stock, updates inventory, records a demo sale, and saves the state.

## Scope and limitations

This is a portfolio prototype, not production accounting software. It has no sign-in, server, database, receipt printer, or payment integration. The VAT rate is an example for the demo and must not be treated as tax advice. Browser storage can be cleared by the user and is not a backup.

## Next steps

- Replace browser storage with an ASP.NET Core API and SQL Server.
- Add automated tests for stock validation, tax calculation, and sale totals.
- Add role-based access, audit history, and a proper tax configuration.

## License

MIT — see `LICENSE`.


## Windows single-computer build

This repository also contains an offline Windows desktop build in [desktop/](desktop/README.md). It records stock and sales in a local SQLite database, supports cash checkout and externally confirmed terminal payments, and saves receipt text files. It does not collect card data or verify external terminal payments. Configure your actual inventory and business tax settings before use; see its setup guide and limitations.
