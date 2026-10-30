# EnterprisePOS C++ UI preview

This is a standalone native Windows UI preview written in the same Win32/GDI C++17 style as the LoginShield desktop program. It includes checkout, inventory, and sales-history screens with matching dark panels and controls.

## Build

- Open `EnterprisePOS.cbp` in Code::Blocks and choose **Build → Build and Run**, or run `build_windows.bat` from a MinGW-w64/Code::Blocks command prompt.
- No third-party library is required.

## Important boundary

This C++ screen uses sample data held in memory. Checkout only demonstrates the visual flow: it does not save a sale, connect to SQL/SQLite, record a payment, or print a receipt. For real local sales, use the Windows operating build in `../desktop/` (`northstar_pos.py`), which stores sales and inventory in SQLite. This C++ UI is a design preview and must not be used to record shop transactions.
