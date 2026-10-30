"""Northstar POS: single-computer checkout and inventory app.

Offline-first portfolio build. Card payments happen on a separate terminal;
this app records only the operator-confirmed outcome and terminal reference.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tkinter as tk
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from uuid import uuid4

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "NorthstarPOS"
DB_PATH = APP_DIR / "northstar.sqlite3"
RECEIPTS_DIR = APP_DIR / "receipts"
PBKDF2_ROUNDS = 310_000


def password_hash(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return salt, digest


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def initialize(self):
        with closing(self.connect()) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              salt BLOB NOT NULL, password_hash BLOB NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('owner','cashier')), active INTEGER NOT NULL DEFAULT 1,
              failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until TEXT
            );
            CREATE TABLE IF NOT EXISTS products (
              id TEXT PRIMARY KEY, sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
              name TEXT NOT NULL, price_kobo INTEGER NOT NULL CHECK(price_kobo >= 0),
              stock INTEGER NOT NULL CHECK(stock >= 0), active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO settings(key,value) VALUES ('tax_basis_points','0');
            CREATE TABLE IF NOT EXISTS sales (
              id TEXT PRIMARY KEY, receipt_no TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              cashier_id TEXT NOT NULL REFERENCES users(id), tender TEXT NOT NULL CHECK(tender IN ('cash','external_terminal')),
              terminal_reference TEXT, subtotal_kobo INTEGER NOT NULL, tax_kobo INTEGER NOT NULL,
              total_kobo INTEGER NOT NULL, cash_received_kobo INTEGER, change_kobo INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sale_items (
              id INTEGER PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES sales(id),
              product_id TEXT NOT NULL REFERENCES products(id), sku TEXT NOT NULL,
              product_name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
              unit_price_kobo INTEGER NOT NULL, line_total_kobo INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, user_id TEXT,
              action TEXT NOT NULL, details TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_sales_created_at ON sales(created_at);
            CREATE INDEX IF NOT EXISTS ix_sale_items_sale_id ON sale_items(sale_id);
            """)

    def user_count(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]

    def add_user(self, username: str, password: str, role: str):
        username = username.strip()
        if not username or len(username) > 48 or len(password) < 12:
            raise ValueError("Use a username and a password with at least 12 characters.")
        salt, digest = password_hash(password)
        with closing(self.connect()) as db:
            db.execute("INSERT INTO users(id,username,salt,password_hash,role) VALUES(?,?,?,?,?)",
                       (str(uuid4()), username, salt, digest, role))

    def authenticate(self, username: str, password: str):
        with closing(self.connect()) as db:
            user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)).fetchone()
            if not user:
                # Spend a comparable amount of CPU to make username probing less useful.
                hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"northstar-unknown-user", PBKDF2_ROUNDS)
                return None
            now=datetime.now(timezone.utc)
            if user["locked_until"] and user["locked_until"]>now.isoformat(timespec="seconds"):
                return None
            test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), user["salt"], PBKDF2_ROUNDS)
            if hmac.compare_digest(test, user["password_hash"]):
                db.execute("UPDATE users SET failed_attempts=0,locked_until=NULL WHERE id=?", (user["id"],))
                return dict(user)
            failures=user["failed_attempts"]+1
            locked=(now+timedelta(minutes=5)).isoformat(timespec="seconds") if failures>=10 else None
            db.execute("UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?", (0 if locked else failures,locked,user["id"]))
            return None

    def products(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT * FROM products WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()

    def add_product(self, sku: str, name: str, price_kobo: int, stock: int, user_id: str):
        if not sku.strip() or not name.strip() or price_kobo < 0 or stock < 0:
            raise ValueError("Enter a SKU, product name, non-negative price, and non-negative stock.")
        with closing(self.connect()) as db:
            db.execute("INSERT INTO products(id,sku,name,price_kobo,stock,created_at) VALUES(?,?,?,?,?,?)",
                       (str(uuid4()), sku.strip(), name.strip(), price_kobo, stock, utc_now()))
            db.execute("INSERT INTO audit_log(created_at,user_id,action,details) VALUES(?,?,?,?)",
                       (utc_now(), user_id, "product_created", f"SKU {sku.strip()}; opening stock {stock}"))

    def receive_stock(self, product_id: str, quantity: int, user_id: str):
        if quantity < 1:
            raise ValueError("Stock received must be at least one whole unit.")
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            product = db.execute("SELECT sku,name FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
            if not product:
                raise ValueError("Product no longer exists.")
            db.execute("UPDATE products SET stock=stock+? WHERE id=?", (quantity, product_id))
            db.execute("INSERT INTO audit_log(created_at,user_id,action,details) VALUES(?,?,?,?)",
                       (utc_now(), user_id, "stock_received", f"SKU {product['sku']}; quantity {quantity}"))
            db.commit()

    def set_tax_basis_points(self, value: int):
        if not 0 <= value <= 10000:
            raise ValueError("Tax rate must be between 0.00% and 100.00%.")
        with closing(self.connect()) as db:
            db.execute("INSERT INTO settings(key,value) VALUES('tax_basis_points',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(value),))

    def tax_basis_points(self):
        with closing(self.connect()) as db:
            return int(db.execute("SELECT value FROM settings WHERE key='tax_basis_points'").fetchone()[0])

    def complete_sale(self, user: dict, cart: dict[str, int], tender: str,
                      cash_received_kobo: int | None = None, terminal_reference: str | None = None):
        if tender not in ("cash", "external_terminal") or not cart:
            raise ValueError("Choose sale items and a supported tender type.")
        terminal_reference=(terminal_reference or "").strip()
        if tender == "external_terminal":
            if not terminal_reference:
                raise ValueError("Enter the reference shown by the separate payment terminal.")
            if len(terminal_reference)>64 or not re.fullmatch(r"[A-Za-z0-9._/-]+",terminal_reference):
                raise ValueError("Use only the short terminal transaction reference; do not enter card or account details.")
            if re.fullmatch(r"\d{12,19}",terminal_reference):
                raise ValueError("That looks like a payment card number. Enter the terminal's non-card transaction reference instead.")
        sale_id, receipt_no, created = str(uuid4()), "NS-" + datetime.now().strftime("%Y%m%d-") + secrets.token_hex(3).upper(), utc_now()
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = []
            subtotal = 0
            for product_id, quantity in cart.items():
                if not isinstance(quantity, int) or quantity < 1:
                    raise ValueError("Every item quantity must be a positive whole number.")
                p = db.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
                if not p or p["stock"] < quantity:
                    raise ValueError(f"Insufficient stock for {p['name'] if p else 'a selected item'}. Refresh inventory and retry.")
                line = p["price_kobo"] * quantity
                subtotal += line
                rows.append((p, quantity, line))
            tax = (subtotal * self.tax_basis_points() + 5000) // 10000
            total = subtotal + tax
            received = cash_received_kobo if tender == "cash" else None
            if tender == "cash" and (received is None or received < total):
                raise ValueError("Cash received must cover the sale total.")
            db.execute("INSERT INTO sales(id,receipt_no,created_at,cashier_id,tender,terminal_reference,subtotal_kobo,tax_kobo,total_kobo,cash_received_kobo,change_kobo) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (sale_id, receipt_no, created, user["id"], tender, (terminal_reference or "").strip() or None, subtotal, tax, total, received, (received-total) if received is not None else 0))
            for p, quantity, line in rows:
                updated = db.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=?", (quantity, p["id"], quantity))
                if updated.rowcount != 1:
                    raise ValueError(f"Stock changed while checking out {p['name']}; refresh and retry.")
                db.execute("INSERT INTO sale_items(sale_id,product_id,sku,product_name,quantity,unit_price_kobo,line_total_kobo) VALUES(?,?,?,?,?,?,?)",
                           (sale_id, p["id"], p["sku"], p["name"], quantity, p["price_kobo"], line))
            db.execute("INSERT INTO audit_log(created_at,user_id,action,details) VALUES(?,?,?,?)",
                       (created, user["id"], "sale_completed", f"{receipt_no}; total kobo {total}; tender {tender}"))
            db.commit()
            return {"id": sale_id, "receipt_no": receipt_no, "created_at": created, "items": rows,
                    "subtotal": subtotal, "tax": tax, "total": total, "received": received,
                    "change": (received-total) if received is not None else 0, "tender": tender,
                    "terminal_reference": (terminal_reference or "").strip() or None}

    def sales_today(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT COUNT(*) count, COALESCE(SUM(total_kobo),0) total FROM sales WHERE date(created_at)=date('now')").fetchone()

    def backup(self):
        target_dir = APP_DIR / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / ("northstar-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".sqlite3")
        with closing(self.connect()) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
        return target


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def naira(kobo: int) -> str:
    sign = "-" if kobo < 0 else ""
    return f"{sign}₦{abs(kobo)//100:,}.{abs(kobo)%100:02d}"


def parse_money(text: str) -> int:
    amount = text.replace(",", "").strip()
    if not amount or amount.startswith("-"):
        raise ValueError("Enter a non-negative amount in naira, such as 2500.00.")
    try:
        whole, dot, fraction = amount.partition(".")
        if not whole.isdigit() or (dot and (not fraction.isdigit() or len(fraction) > 2)):
            raise ValueError
        return int(whole) * 100 + int((fraction + "00")[:2])
    except ValueError:
        raise ValueError("Enter a non-negative amount in naira with up to two decimal places.") from None


class PosApp:
    def __init__(self, root: tk.Tk, store: Store, user: dict):
        self.root, self.store, self.user = root, store, user
        self.cart: dict[str, int] = {}
        root.title("Northstar POS · Local checkout")
        root.geometry("1120x720")
        root.minsize(900, 600)
        self.build()
        self.refresh()

    def build(self):
        style = ttk.Style()
        try: style.theme_use("vista")
        except tk.TclError: pass
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        header = ttk.Frame(self.root, padding=(18, 13)); header.pack(fill="x")
        ttk.Label(header, text="NORTHSTAR POS", font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Label(header, text=f"{self.user['username']} · {self.user['role']} · local-only", foreground="#56735e").pack(side="right")
        main = ttk.Frame(self.root, padding=(18, 0, 18, 14)); main.pack(fill="both", expand=True)
        left = ttk.Frame(main); left.pack(side="left", fill="both", expand=True)
        right = ttk.LabelFrame(main, text="Current sale", padding=12); right.pack(side="right", fill="y", padx=(14, 0))
        top = ttk.Frame(left); top.pack(fill="x", pady=(5, 8))
        self.summary = ttk.Label(top, text="No sales yet", font=("Segoe UI", 10, "bold")); self.summary.pack(side="left")
        if self.user["role"]=="owner":ttk.Button(top, text="＋ Add product", command=self.add_product).pack(side="right")
        ttk.Button(top, text="Inventory backup", command=self.backup).pack(side="right", padx=6)
        columns=("sku","name","price","stock")
        self.products=ttk.Treeview(left,columns=columns,show="headings",selectmode="browse")
        for col,title,width,anchor in [("sku","SKU",110,"w"),("name","PRODUCT",300,"w"),("price","PRICE",110,"e"),("stock","IN STOCK",85,"e")]:
            self.products.heading(col,text=title);self.products.column(col,width=width,anchor=anchor)
        self.products.pack(fill="both",expand=True)
        self.products.bind("<Double-1>",lambda _e:self.add_selected())
        actions=ttk.Frame(left);actions.pack(anchor="w",pady=8)
        ttk.Button(actions,text="Add selected item to sale",command=self.add_selected).pack(side="left")
        if self.user["role"]=="owner":ttk.Button(actions,text="Receive stock",command=self.receive_stock).pack(side="left",padx=7)
        ttk.Label(right,text="Double-click a product or select it below.",wraplength=240).pack(anchor="w",pady=(0,8))
        self.cart_view=ttk.Treeview(right,columns=("qty","line"),show="tree headings",height=12)
        self.cart_view.heading("#0",text="ITEM");self.cart_view.heading("qty",text="QTY");self.cart_view.heading("line",text="TOTAL")
        self.cart_view.column("#0",width=170);self.cart_view.column("qty",width=44,anchor="e");self.cart_view.column("line",width=93,anchor="e")
        self.cart_view.pack(fill="x")
        tools=ttk.Frame(right);tools.pack(fill="x",pady=6)
        ttk.Button(tools,text="− Remove one",command=lambda:self.adjust(-1)).pack(side="left")
        ttk.Button(tools,text="＋ Add one",command=lambda:self.adjust(1)).pack(side="left",padx=5)
        self.totals=ttk.Label(right,text="Subtotal  ₦0.00\nTax  ₦0.00\nTOTAL  ₦0.00",justify="left",font=("Segoe UI",11,"bold"));self.totals.pack(fill="x",pady=(12,8))
        ttk.Label(right,text="Tender",font=("Segoe UI",9,"bold")).pack(anchor="w")
        self.tender=tk.StringVar(value="Cash")
        self.tender_box=ttk.Combobox(right,textvariable=self.tender,values=("Cash","External terminal confirmed"),state="readonly",width=28);self.tender_box.pack(fill="x",pady=4);self.tender_box.bind("<<ComboboxSelected>>",lambda _e:self.toggle_tender())
        ttk.Label(right,text="Cash received (naira)").pack(anchor="w",pady=(4,0))
        self.cash=ttk.Entry(right);self.cash.pack(fill="x",pady=3)
        ttk.Label(right,text="Terminal reference (no card data)").pack(anchor="w",pady=(4,0))
        self.reference=ttk.Entry(right);self.reference.pack(fill="x",pady=3)
        self.checkout=ttk.Button(right,text="Complete sale",command=self.checkout_sale);self.checkout.pack(fill="x",pady=(9,3))
        if self.user["role"]=="owner":
            bottom=ttk.Frame(self.root,padding=(18,0,18,12));bottom.pack(fill="x")
            ttk.Button(bottom,text="Configure tax rate",command=self.configure_tax).pack(side="left")
            ttk.Button(bottom,text="Add cashier account",command=self.add_cashier).pack(side="left",padx=6)
            ttk.Label(bottom,text="Payments occur outside this app. Do not enter card numbers or security codes.",foreground="#835d2b").pack(side="right")
        else:
            ttk.Label(self.root,text="Payments occur outside this app. Do not enter card numbers or security codes.",foreground="#835d2b",padding=(18,0,18,12)).pack(anchor="e")
        self.toggle_tender()

    def toggle_tender(self):
        is_cash=self.tender.get()=="Cash"
        self.cash.configure(state="normal" if is_cash else "disabled")
        self.reference.configure(state="disabled" if is_cash else "normal")

    def refresh(self):
        self.products.delete(*self.products.get_children())
        for p in self.store.products():self.products.insert("","end",iid=p["id"],values=(p["sku"],p["name"],naira(p["price_kobo"]),p["stock"]))
        sales=self.store.sales_today();self.summary.configure(text=f"Today · {sales['count']} sales · {naira(sales['total'])}")
        for node in self.cart_view.get_children():self.cart_view.delete(node)
        subtotal=0
        products={p["id"]:p for p in self.store.products()}
        for product_id,qty in self.cart.items():
            p=products.get(product_id)
            if p:
                line=p["price_kobo"]*qty;subtotal+=line;self.cart_view.insert("","end",iid=product_id,text=p["name"],values=(qty,naira(line)))
        tax=(subtotal*self.store.tax_basis_points()+5000)//10000
        self.current_total=subtotal+tax
        self.totals.configure(text=f"Subtotal  {naira(subtotal)}\nTax  {naira(tax)} ({self.store.tax_basis_points()/100:.2f}%)\nTOTAL  {naira(self.current_total)}")
        self.checkout.configure(state="normal" if self.cart else "disabled")

    def add_selected(self):
        selection=self.products.selection()
        if not selection:return
        product_id=selection[0]
        p=next((p for p in self.store.products() if p["id"]==product_id),None)
        if p and self.cart.get(product_id,0)>=p["stock"]:
            messagebox.showwarning("Stock limit","There is not enough stock for that quantity.");return
        self.cart[product_id]=self.cart.get(product_id,0)+1;self.refresh()

    def adjust(self,delta):
        selected=self.cart_view.selection()
        if not selected:return
        key=selected[0];qty=self.cart.get(key,0)+delta
        if qty<=0:self.cart.pop(key,None)
        else:
            p=next((p for p in self.store.products() if p["id"]==key),None)
            if p and qty>p["stock"]:
                messagebox.showwarning("Stock limit","There is not enough stock for that quantity.");return
            self.cart[key]=qty
        self.refresh()

    def add_product(self):
        if self.user["role"]!="owner":return
        win=tk.Toplevel(self.root);win.title("Add inventory item");win.resizable(False,False);win.transient(self.root);win.grab_set()
        frame=ttk.Frame(win,padding=18);frame.pack()
        fields=[]
        for label in ("SKU","Product name","Price in naira","Opening stock (whole units)"):
            ttk.Label(frame,text=label).pack(anchor="w",pady=(7,0));entry=ttk.Entry(frame,width=34);entry.pack(fill="x");fields.append(entry)
        def save():
            try:
                sku,name=fields[0].get(),fields[1].get();price=parse_money(fields[2].get());stock=int(fields[3].get())
                self.store.add_product(sku,name,price,stock,self.user["id"])
            except (ValueError,sqlite3.IntegrityError) as err:messagebox.showerror("Could not add product",str(err),parent=win);return
            win.destroy();self.refresh()
        ttk.Button(frame,text="Save product",command=save).pack(fill="x",pady=(14,0))

    def receive_stock(self):
        if self.user["role"]!="owner":return
        selection=self.products.selection()
        if not selection:messagebox.showinfo("Receive stock","Select a product first.",parent=self.root);return
        product_id=selection[0]
        row=next((p for p in self.store.products() if p["id"]==product_id),None)
        if not row:return
        quantity=simpledialog.askinteger("Receive stock",f"Units received for {row['name']}:",minvalue=1,parent=self.root)
        if quantity is None:return
        try:self.store.receive_stock(product_id,quantity,self.user["id"])
        except (ValueError,sqlite3.Error) as err:messagebox.showerror("Could not receive stock",str(err),parent=self.root);return
        self.refresh()

    def add_cashier(self):
        if self.user["role"]!="owner":return
        username=simpledialog.askstring("Add cashier","Cashier username:",parent=self.root)
        if username is None:return
        password=simpledialog.askstring("Cashier password","Set a unique password (12+ characters):",show="•",parent=self.root)
        if password is None:return
        confirm=simpledialog.askstring("Confirm password","Enter the password again:",show="•",parent=self.root)
        if password!=confirm:messagebox.showerror("Passwords differ","The passwords did not match.",parent=self.root);return
        try:self.store.add_user(username,password,"cashier")
        except (ValueError,sqlite3.IntegrityError) as err:messagebox.showerror("Could not add cashier",str(err),parent=self.root);return
        messagebox.showinfo("Cashier account","Cashier account created.",parent=self.root)

    def configure_tax(self):
        current=self.store.tax_basis_points()
        value=simpledialog.askfloat("Configure tax","Tax rate in percent (0 to 100). Enter the rate confirmed for your business:",initialvalue=current/100,minvalue=0,maxvalue=100,parent=self.root)
        if value is None:return
        try:self.store.set_tax_basis_points(round(value*100))
        except ValueError as err:messagebox.showerror("Invalid tax rate",str(err));return
        self.refresh()

    def checkout_sale(self):
        terminal=self.tender.get().startswith("External")
        try:
            received=None if terminal else parse_money(self.cash.get())
            sale=self.store.complete_sale(self.user,self.cart,"external_terminal" if terminal else "cash",received,self.reference.get() if terminal else None)
        except (ValueError,sqlite3.Error) as err:messagebox.showerror("Sale not completed",str(err),parent=self.root);return
        self.cart.clear();self.cash.delete(0,"end");self.reference.delete(0,"end");self.refresh()
        try:
            self.write_receipt(sale)
            receipt_message="A receipt text file was saved locally."
        except OSError as err:
            receipt_message=f"The sale is recorded, but the receipt file could not be written: {err}"
        messagebox.showinfo("Sale complete",f"Receipt {sale['receipt_no']} recorded.\nChange due: {naira(sale['change']) if not terminal else '—'}\n\n{receipt_message}")

    def write_receipt(self,sale):
        RECEIPTS_DIR.mkdir(parents=True,exist_ok=True)
        lines=["NORTHSTAR POS · SALES RECEIPT",f"Receipt: {sale['receipt_no']}",f"Time (UTC): {sale['created_at']}",f"Operator: {self.user['username']}","", "ITEM                         QTY       AMOUNT"]
        for p,q,line in sale["items"]:lines.append(f"{p['name'][:27]:27} {q:>3} {naira(line):>14}")
        lines.extend(["",f"Subtotal: {naira(sale['subtotal'])}",f"Tax: {naira(sale['tax'])}",f"TOTAL: {naira(sale['total'])}",f"Tender: {'External terminal' if sale['tender']=='external_terminal' else 'Cash'}"])
        if sale["terminal_reference"]:lines.append(f"Terminal reference: {sale['terminal_reference']}")
        if sale["received"] is not None:lines.extend([f"Cash received: {naira(sale['received'])}",f"Change: {naira(sale['change'])}"])
        lines.extend(["", "Thank you.", "Payment card data is not stored by this application."])
        (RECEIPTS_DIR/f"{sale['receipt_no']}.txt").write_text("\n".join(lines),encoding="utf-8")

    def backup(self):
        try:target=self.store.backup()
        except sqlite3.Error as err:messagebox.showerror("Backup failed",str(err),parent=self.root);return
        messagebox.showinfo("Backup saved",f"Database backup saved to:\n{target}",parent=self.root)


def setup_owner(root: tk.Tk, store: Store):
    if store.user_count():return True
    messagebox.showinfo("First-run setup","Create the owner account. Use a unique password with at least 12 characters. There is no default password.",parent=root)
    username=simpledialog.askstring("Owner account","Owner username:",parent=root)
    if username is None:return False
    password=simpledialog.askstring("Owner password","Create a password (12+ characters):",show="•",parent=root)
    if password is None:return False
    confirm=simpledialog.askstring("Confirm password","Enter the password again:",show="•",parent=root)
    if password!=confirm:
        messagebox.showerror("Passwords differ","The passwords did not match. Restart the app to try again.",parent=root);return False
    try:store.add_user(username,password,"owner")
    except (ValueError,sqlite3.IntegrityError) as err:messagebox.showerror("Setup failed",str(err),parent=root);return False
    messagebox.showinfo("Owner created","Account ready. Sign in with your new credentials.",parent=root);return True


def login(root: tk.Tk, store: Store):
    frame=ttk.Frame(root,padding=26);frame.pack(expand=True)
    ttk.Label(frame,text="NORTHSTAR POS",font=("Segoe UI",19,"bold")).pack(pady=(0,16))
    ttk.Label(frame,text="Username").pack(anchor="w");username=ttk.Entry(frame,width=32);username.pack(pady=(2,9));username.focus_set()
    ttk.Label(frame,text="Password").pack(anchor="w");password=ttk.Entry(frame,width=32,show="•");password.pack(pady=(2,12))
    result={"user":None}
    def submit(_event=None):
        user=store.authenticate(username.get(),password.get())
        if not user:messagebox.showerror("Sign-in failed","Username or password is incorrect.",parent=root);password.delete(0,"end");return
        result["user"]=user;root.quit()
    ttk.Button(frame,text="Sign in",command=submit).pack(fill="x");password.bind("<Return>",submit)
    root.mainloop();frame.destroy();root.quit();root.configure(menu="")
    return result["user"]


def main():
    if sys.platform != "win32":
        print("Northstar POS desktop build is intended for Windows.");return 2
    APP_DIR.mkdir(parents=True,exist_ok=True)
    root=tk.Tk();root.title("Northstar POS setup");root.geometry("420x300")
    store=Store()
    if not setup_owner(root,store):root.destroy();return 1
    user=login(root,store)
    if not user:root.destroy();return 1
    for child in root.winfo_children():child.destroy()
    PosApp(root,store,user);root.mainloop();return 0


if __name__=="__main__":raise SystemExit(main())

