# Northstar POS desktop build

This is a single-computer Windows application for recording real inventory and completed sales. It uses Python 3, Tkinter, and SQLite from the Python standard library; there are no third-party Python packages.

## Start

1. Install Python 3 for Windows with Tcl/Tk support.
2. Open PowerShell in this folder and run `python northstar_pos.py`.
3. On first run, create an owner username and a unique password of at least 12 characters. The password is stored as a salted PBKDF2 hash; there is no default login.
4. Use **Add product** to enter the shop's own SKU, price in naira, and opening stock. Tax starts at 0%; the owner can configure it after confirming the rate that applies to the business.
5. Add items to a sale, choose cash or externally confirmed terminal payment, and complete checkout.

## Payment boundary

For cash, enter the cash received and the app calculates change. For card or transfer payments, use the shop's separate bank/payment terminal, confirm that payment succeeded there, then select **External terminal confirmed** and save the terminal reference. This app never collects or stores card numbers, CVV, PINs, or bank credentials, and it cannot itself verify an external payment.

## Data and recovery

The database is stored under `%APPDATA%\NorthstarPOS\northstar.sqlite3`. Receipts are text files under `%APPDATA%\NorthstarPOS\receipts`. Use **Inventory backup** to create a consistent SQLite backup under `%APPDATA%\NorthstarPOS\backups`. Keep separate backups on a protected drive; the local copy can be lost if the PC fails.

## Scope

This is a local, single-terminal implementation, not a certified commercial POS. It has password sign-in, owner and cashier accounts, parameterized SQLite operations, atomic stock decrement and sale recording, configurable tax, and receipts. Owners can add cashier accounts and receive stock. It does not support multiple terminals, cloud sync, refunds, supplier purchasing, fiscal integrations, or automated card settlement. Before using it for business, configure real inventory and tax, test restore from backup, and review local business/accounting requirements.
