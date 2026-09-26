import asyncio
import os
from datetime import datetime
from io import BytesIO

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PAYMENT_CARD = os.getenv("PAYMENT_CARD", "")
PAYMENT_NAME = os.getenv("PAYMENT_NAME", "")

DB = "shop.db"
LOW_STOCK_LIMIT = 3

dp = Dispatcher()


class AddProduct(StatesGroup):
    category = State()
    name = State()
    price = State()
    cost = State()
    stock = State()
    photo = State()


class AddCategory(StatesGroup):
    name = State()


class EditProduct(StatesGroup):
    product_id = State()
    field = State()
    value = State()


class Checkout(StatesGroup):
    name = State()
    phone = State()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID and ADMIN_ID != 0


async def db_init():
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            cost REAL NOT NULL DEFAULT 0,
            stock INTEGER NOT NULL DEFAULT 0,
            photo_id TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(category_id) REFERENCES categories(id)
        );
        CREATE TABLE IF NOT EXISTS carts(
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            customer_name TEXT,
            phone TEXT,
            total REAL NOT NULL,
            total_cost REAL NOT NULL,
            status TEXT NOT NULL,
            payment TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            cost REAL NOT NULL,
            quantity INTEGER NOT NULL
        );
        """)
        await db.commit()


def main_menu(user_id):
    rows = [
        [InlineKeyboardButton(text="🛍 Каталог", callback_data="catalog")],
        [InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")],
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton(text="🆘 Помощь", callback_data="help")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 Добро пожаловать в магазин!\n\nВыберите действие:",
        reply_markup=main_menu(message.from_user.id)
    )


@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery):
    await call.message.edit_text(
        "Главное меню:",
        reply_markup=main_menu(call.from_user.id)
    )
    await call.answer()


@dp.callback_query(F.data == "catalog")
async def catalog(call: CallbackQuery):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT id,name FROM categories ORDER BY name")
        cats = await cur.fetchall()
    buttons = [
        [InlineKeyboardButton(text=f"📂 {name}", callback_data=f"cat:{cid}")]
        for cid, name in cats
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    await call.message.edit_text(
        "Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("cat:"))
async def category(call: CallbackQuery):
    cid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,name,price,stock FROM products "
            "WHERE category_id=? AND active=1 ORDER BY name", (cid,)
        )
        products = await cur.fetchall()
    buttons = []
    text = "📦 Товары:\n\n"
    for pid, name, price, stock in products:
        text += f"• {name} — {price:.2f} ₽ | остаток: {stock}\n"
        buttons.append([
            InlineKeyboardButton(text=name[:35], callback_data=f"product:{pid}")
        ])
    if not products:
        text += "Пока товаров нет."
    buttons.append([InlineKeyboardButton(text="⬅️ Категории", callback_data="catalog")])
    await call.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("product:"))
async def product(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT name,price,cost,stock,photo_id FROM products WHERE id=? AND active=1",
            (pid,)
        )
        p = await cur.fetchone()
    if not p:
        await call.answer("Товар не найден", show_alert=True)
        return
    name, price, cost, stock, photo = p
    text = f"🛍 <b>{name}</b>\n\nЦена: {price:.2f} ₽\nВ наличии: {stock}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ В корзину", callback_data=f"add:{pid}")],
        [InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog")]
    ])
    if photo:
        try:
            await call.message.delete()
            await call.message.answer_photo(photo, caption=text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("add:"))
async def add_to_cart(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT name,stock FROM products WHERE id=? AND active=1", (pid,))
        p = await cur.fetchone()
        if not p:
            await call.answer("Товар не найден", show_alert=True)
            return
        name, stock = p
        if stock <= 0:
            await call.answer("Товара нет в наличии", show_alert=True)
            return
        cur = await db.execute(
            "SELECT quantity FROM carts WHERE user_id=? AND product_id=?",
            (call.from_user.id, pid)
        )
        row = await cur.fetchone()
        new_qty = (row[0] if row else 0) + 1
        if new_qty > stock:
            await call.answer("Больше этого количества нет на складе", show_alert=True)
            return
        await db.execute(
            "INSERT INTO carts(user_id,product_id,quantity) VALUES(?,?,?) "
            "ON CONFLICT(user_id,product_id) DO UPDATE SET quantity=excluded.quantity",
            (call.from_user.id, pid, new_qty)
        )
        await db.commit()
    await call.answer(f"Добавлено: {name}")


async def cart_text(user_id):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT c.product_id,p.name,p.price,c.quantity,p.stock
            FROM carts c JOIN products p ON p.id=c.product_id
            WHERE c.user_id=? AND p.active=1
        """, (user_id,))
        rows = await cur.fetchall()
    if not rows:
        return "🛒 Корзина пуста.", []
    total = 0
    text = "🛒 <b>Корзина</b>\n\n"
    buttons = []
    for pid, name, price, qty, stock in rows:
        s = price * qty
        total += s
        text += f"• {name} × {qty} = {s:.2f} ₽\n"
        buttons.append([
            InlineKeyboardButton(text=f"➖ {name[:18]}", callback_data=f"minus:{pid}"),
            InlineKeyboardButton(text="➕", callback_data=f"add:{pid}"),
            InlineKeyboardButton(text="🗑", callback_data=f"remove:{pid}")
        ])
    text += f"\n<b>Итого: {total:.2f} ₽</b>"
    buttons.append([InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    return text, buttons


@dp.callback_query(F.data == "cart")
async def show_cart(call: CallbackQuery):
    text, buttons = await cart_text(call.from_user.id)
    await call.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons or [
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data.startswith("minus:"))
async def minus(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT quantity FROM carts WHERE user_id=? AND product_id=?",
            (call.from_user.id, pid)
        )
        row = await cur.fetchone()
        if row:
            if row[0] <= 1:
                await db.execute("DELETE FROM carts WHERE user_id=? AND product_id=?",
                                 (call.from_user.id, pid))
            else:
                await db.execute(
                    "UPDATE carts SET quantity=quantity-1 WHERE user_id=? AND product_id=?",
                    (call.from_user.id, pid)
                )
            await db.commit()
    await show_cart(call)


@dp.callback_query(F.data.startswith("remove:"))
async def remove(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM carts WHERE user_id=? AND product_id=?",
                         (call.from_user.id, pid))
        await db.commit()
    await show_cart(call)


@dp.callback_query(F.data == "checkout")
async def checkout_start(call: CallbackQuery, state: FSMContext):
    text, _ = await cart_text(call.from_user.id)
    if "пуста" in text:
        await call.answer("Корзина пуста", show_alert=True)
        return
    await state.set_state(Checkout.name)
    await call.message.answer("Введите ваше имя:")
    await call.answer()


@dp.message(Checkout.name)
async def checkout_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text[:100])
    await state.set_state(Checkout.phone)
    await message.answer("Введите номер телефона или напишите «нет»:")


@dp.message(Checkout.phone)
async def checkout_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    phone = message.text[:50]
    uid = message.from_user.id
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT c.product_id,p.name,p.price,p.cost,c.quantity,p.stock
            FROM carts c JOIN products p ON p.id=c.product_id
            WHERE c.user_id=? AND p.active=1
        """, (uid,))
        rows = await cur.fetchall()
        if not rows:
            await state.clear()
            await message.answer("Корзина пуста.", reply_markup=main_menu(uid))
            return
        total = 0
        total_cost = 0
        for pid, name, price, cost, qty, stock in rows:
            if qty > stock:
                await state.clear()
                await message.answer(f"Недостаточно товара: {name}", reply_markup=main_menu(uid))
                return
            total += price * qty
            total_cost += cost * qty
        await db.execute(
            "INSERT INTO orders(user_id,customer_name,phone,total,total_cost,status,payment,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (uid, data["name"], phone, total, total_cost, "waiting_payment",
             "not_selected", datetime.now().isoformat(timespec="seconds"))
        )
        order_id = (await (await db.execute("SELECT last_insert_rowid()")).fetchone())[0]
        for pid, name, price, cost, qty, stock in rows:
            await db.execute(
                "INSERT INTO order_items(order_id,product_id,name,price,cost,quantity)"
                " VALUES(?,?,?,?,?,?)",
                (order_id, pid, name, price, cost, qty)
            )
        await db.execute("DELETE FROM carts WHERE user_id=?", (uid,))
        await db.commit()
    await state.clear()
    await message.answer(
        f"✅ Заказ №{order_id} создан.\nСумма: {total:.2f} ₽\n\n"
        "Выберите способ оплаты:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перевод", callback_data=f"pay_card:{order_id}")],
            [InlineKeyboardButton(text="💵 Наличными", callback_data=f"pay_cash:{order_id}")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="home")]
        ])
    )


@dp.callback_query(F.data.startswith("pay_card:"))
async def pay_card(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE orders SET payment='card' WHERE id=? AND user_id=?", (oid, call.from_user.id))
        await db.commit()
        cur = await db.execute("SELECT total FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
    details = f"💳 Перевод по реквизитам\nСумма: {row[0]:.2f} ₽"
    if PAYMENT_CARD:
        details += f"\nКарта: {PAYMENT_CARD}"
    if PAYMENT_NAME:
        details += f"\nПолучатель: {PAYMENT_NAME}"
    details += "\n\nПосле оплаты нажмите «Я оплатил»."
    await call.message.edit_text(
        details,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{oid}")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data.startswith("pay_cash:"))
async def pay_cash(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE orders SET payment='cash' WHERE id=? AND user_id=?", (oid, call.from_user.id))
        await db.commit()
    await call.message.edit_text(
        f"💵 Заказ №{oid} оформлен с оплатой наличными.\n"
        "Ожидайте подтверждения администратора.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="home")]
        ])
    )
    await notify_admin_order(oid)
    await call.answer()


@dp.callback_query(F.data.startswith("paid:"))
async def paid(call: CallbackQuery):
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE orders SET status='payment_check' WHERE id=? AND user_id=?", (oid, call.from_user.id))
        await db.commit()
    await call.message.edit_text(
        f"🕐 Оплата по заказу №{oid} отправлена на проверку.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="home")]
        ])
    )
    await notify_admin_order(oid)
    await call.answer()


async def notify_admin_order(oid):
    if not ADMIN_ID:
        return
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT user_id,customer_name,phone,total,total_cost,status,payment
            FROM orders WHERE id=?
        """, (oid,))
        o = await cur.fetchone()
        cur = await db.execute(
            "SELECT name,quantity,price FROM order_items WHERE order_id=?", (oid,)
        )
        items = await cur.fetchall()
    if not o:
        return
    uid, name, phone, total, cost, status, payment = o
    text = f"🆕 <b>Заказ №{oid}</b>\n\n👤 {name}\n📞 {phone}\n"
    text += f"💰 {total:.2f} ₽\n💳 {payment}\n📌 {status}\n\n"
    text += "\n".join(f"• {n} × {q} — {p:.2f} ₽" for n, q, p in items)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve:{oid}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{oid}")]
    ])
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


@dp.callback_query(F.data.startswith("approve:"))
async def approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT user_id,status FROM orders WHERE id=?", (oid,))
        order = await cur.fetchone()
        if not order:
            await call.answer("Заказ не найден", show_alert=True)
            return
        uid, status = order
        if status == "paid":
            await call.answer("Уже подтверждено")
            return
        cur = await db.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?", (oid,))
        items = await cur.fetchall()
        for pid, qty in items:
            cur = await db.execute("SELECT stock,name FROM products WHERE id=?", (pid,))
            p = await cur.fetchone()
            if not p or p[0] < qty:
                await call.answer("Недостаточно товара на складе", show_alert=True)
                return
        for pid, qty in items:
            await db.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, pid))
        await db.execute("UPDATE orders SET status='paid' WHERE id=?", (oid,))
        await db.commit()
    await call.message.edit_text(f"✅ Заказ №{oid} подтверждён.")
    try:
        await bot.send_message(uid, f"✅ Заказ №{oid} подтверждён. Спасибо за покупку!")
    except Exception:
        pass
    await call.answer()


@dp.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT user_id FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
        await db.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
        await db.commit()
    await call.message.edit_text(f"❌ Заказ №{oid} отклонён.")
    if row:
        try:
            await bot.send_message(row[0], f"❌ Заказ №{oid} отклонён.")
        except Exception:
            pass
    await call.answer()


@dp.callback_query(F.data == "my_orders")
async def my_orders(call: CallbackQuery):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT id,total,status,payment,created_at
            FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 20
        """, (call.from_user.id,))
        rows = await cur.fetchall()
    if not rows:
        text = "📦 У вас пока нет заказов."
    else:
        text = "📦 <b>Мои заказы</b>\n\n"
        for oid, total, status, payment, dt in rows:
            text += f"№{oid} — {total:.2f} ₽ — {status} — {dt}\n"
    await call.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "help")
async def help_menu(call: CallbackQuery):
    await call.message.edit_text(
        "🆘 <b>Помощь</b>\n\n"
        "Если возник вопрос по заказу, напишите администратору.\n"
        "Ваш заказ можно посмотреть в разделе «Мои заказы».",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ])
    )
    await call.answer()


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить категорию", callback_data="a_add_cat")],
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="a_add_product")],
        [InlineKeyboardButton(text="✏️ Редактировать товар", callback_data="a_edit")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data="a_delete")],
        [InlineKeyboardButton(text="📦 Товары и остатки", callback_data="a_products")],
        [InlineKeyboardButton(text="📋 Заказы", callback_data="a_orders")],
        [InlineKeyboardButton(text="📊 Финансы", callback_data="a_finance")],
        [InlineKeyboardButton(text="📈 Продажи", callback_data="a_sales")],
        [InlineKeyboardButton(text="📄 Excel", callback_data="a_excel")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="home")]
    ])


@dp.callback_query(F.data == "admin")
async def admin(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.message.edit_text("⚙️ Админ-панель:", reply_markup=admin_menu())
    await call.answer()


@dp.callback_query(F.data == "a_add_cat")
async def a_add_cat(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(AddCategory.name)
    await call.message.answer("Введите название категории:")
    await call.answer()


@dp.message(AddCategory.name)
async def add_cat(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        async with aiosqlite.connect(DB) as db:
            await db.execute("INSERT INTO categories(name) VALUES(?)", (message.text[:100],))
            await db.commit()
        await message.answer("✅ Категория добавлена.", reply_markup=admin_menu())
    except Exception:
        await message.answer("Такая категория уже существует.")
    await state.clear()


@dp.callback_query(F.data == "a_add_product")
async def a_add_product(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT id,name FROM categories ORDER BY name")
        cats = await cur.fetchall()
    buttons = [[InlineKeyboardButton(text=n, callback_data=f"newcat:{i}")] for i,n in cats]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")])
    await call.message.edit_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@dp.callback_query(F.data.startswith("newcat:"))
async def newcat(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.update_data(category=int(call.data.split(":")[1]))
    await state.set_state(AddProduct.name)
    await call.message.answer("Название товара:")
    await call.answer()


@dp.message(AddProduct.name)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text[:150])
    await state.set_state(AddProduct.price)
    await message.answer("Цена, например 1490:")


@dp.message(AddProduct.price)
async def add_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Введите число.")
        return
    await state.update_data(price=price)
    await state.set_state(AddProduct.cost)
    await message.answer("Себестоимость:")


@dp.message(AddProduct.cost)
async def add_cost(message: Message, state: FSMContext):
    try:
        cost = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Введите число.")
        return
    await state.update_data(cost=cost)
    await state.set_state(AddProduct.stock)
    await message.answer("Количество на складе:")


@dp.message(AddProduct.stock)
async def add_stock(message: Message, state: FSMContext):
    try:
        stock = int(message.text)
    except ValueError:
        await message.answer("Введите целое число.")
        return
    await state.update_data(stock=stock)
    await state.set_state(AddProduct.photo)
    await message.answer("Отправьте фото товара или напишите «нет»:")


@dp.message(AddProduct.photo)
async def add_photo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    photo = message.photo[-1].file_id if message.photo else None
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO products(category_id,name,price,cost,stock,photo_id) VALUES(?,?,?,?,?,?)",
            (data["category"], data["name"], data["price"], data["cost"], data["stock"], photo)
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ Товар добавлен.", reply_markup=admin_menu())


async def product_admin_list():
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT p.id,p.name,p.price,p.cost,p.stock,c.name
            FROM products p JOIN categories c ON c.id=p.category_id
            WHERE p.active=1 ORDER BY p.id DESC
        """)
        return await cur.fetchall()


@dp.callback_query(F.data == "a_products")
async def a_products(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    rows = await product_admin_list()
    text = "📦 <b>Товары</b>\n\n"
    if not rows:
        text += "Товаров нет."
    for pid,name,price,cost,stock,cat in rows:
        mark = " ⚠️" if stock <= LOW_STOCK_LIMIT else ""
        text += f"#{pid} {name} | {cat} | {price:.0f} ₽ | остаток {stock}{mark}\n"
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
    ]))
    await call.answer()


@dp.callback_query(F.data == "a_orders")
async def a_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT id,customer_name,total,status,payment,created_at
            FROM orders ORDER BY id DESC LIMIT 30
        """)
        rows = await cur.fetchall()
    text = "📋 <b>Последние заказы</b>\n\n"
    for oid,name,total,status,pay,dt in rows:
        text += f"№{oid} | {name} | {total:.0f} ₽ | {status} | {pay} | {dt}\n"
    await call.message.edit_text(text or "Заказов нет.", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
        ]))
    await call.answer()


@dp.callback_query(F.data == "a_finance")
async def a_finance(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT COALESCE(SUM(total),0), COALESCE(SUM(total_cost),0), COUNT(*)
            FROM orders WHERE status='paid'
        """)
        revenue,cost,count = await cur.fetchone()
    profit = revenue - cost
    text = (
        "💰 <b>Финансы</b>\n\n"
        f"Выручка: {revenue:.2f} ₽\n"
        f"Себестоимость: {cost:.2f} ₽\n"
        f"Чистая прибыль: {profit:.2f} ₽\n"
        f"Оплаченных заказов: {count}"
    )
    await call.message.edit_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
        ]))
    await call.answer()


@dp.callback_query(F.data == "a_sales")
async def a_sales(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT name, SUM(quantity) qty, SUM(price*quantity) revenue
            FROM order_items oi JOIN orders o ON o.id=oi.order_id
            WHERE o.status='paid'
            GROUP BY product_id,name ORDER BY qty DESC LIMIT 20
        """)
        rows = await cur.fetchall()
    text = "📈 <b>Продажи</b>\n\n"
    if not rows:
        text += "Продаж пока нет."
    for name,qty,revenue in rows:
        text += f"• {name}: {qty} шт. — {revenue:.0f} ₽\n"
    await call.message.edit_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
        ]))
    await call.answer()


@dp.callback_query(F.data == "a_excel")
async def a_excel(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    try:
        from openpyxl import Workbook
    except ImportError:
        await call.answer("Нужно установить openpyxl", show_alert=True)
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["ID","Дата","Клиент","Телефон","Сумма","Себестоимость","Прибыль","Статус","Оплата"])
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
            SELECT id,created_at,customer_name,phone,total,total_cost,status,payment
            FROM orders ORDER BY id
        """)
        rows = await cur.fetchall()
    for oid,dt,name,phone,total,cost,status,pay in rows:
        ws.append([oid,dt,name,phone,total,cost,total-cost,status,pay])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    await call.message.answer_document(
        BufferedInputFile(buf.read(), filename="shop_report.xlsx")
    )
    await call.answer()


@dp.callback_query(F.data == "a_edit")
async def a_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    rows = await product_admin_list()
    buttons = [
        [InlineKeyboardButton(text=f"#{pid} {name[:28]}", callback_data=f"edit:{pid}")]
        for pid,name,*_ in rows
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")])
    await call.message.edit_text("Выберите товар:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@dp.callback_query(F.data.startswith("edit:"))
async def edit_product(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    pid = int(call.data.split(":")[1])
    await call.message.edit_text(
        "Что изменить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Название", callback_data=f"ef:name:{pid}")],
            [InlineKeyboardButton(text="Цена", callback_data=f"ef:price:{pid}")],
            [InlineKeyboardButton(text="Себестоимость", callback_data=f"ef:cost:{pid}")],
            [InlineKeyboardButton(text="Остаток", callback_data=f"ef:stock:{pid}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="a_edit")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data.startswith("ef:"))
async def edit_field(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    _, field, pid = call.data.split(":")
    await state.update_data(product_id=int(pid), field=field)
    await state.set_state(EditProduct.value)
    await call.message.answer(f"Введите новое значение для поля «{field}»:")
    await call.answer()


@dp.message(EditProduct.value)
async def save_edit(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    field = data["field"]
    value = message.text
    if field in ("price","cost"):
        try: value = float(value.replace(",", "."))
        except ValueError:
            await message.answer("Введите число."); return
    elif field == "stock":
        try: value = int(value)
        except ValueError:
            await message.answer("Введите целое число."); return
    else:
        value = value[:150]
    async with aiosqlite.connect(DB) as db:
        await db.execute(f"UPDATE products SET {field}=? WHERE id=?", (value, data["product_id"]))
        await db.commit()
    await state.clear()
    await message.answer("✅ Товар изменён.", reply_markup=admin_menu())


@dp.callback_query(F.data == "a_delete")
async def a_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    rows = await product_admin_list()
    buttons = [
        [InlineKeyboardButton(text=f"🗑 #{pid} {name[:28]}", callback_data=f"del:{pid}")]
        for pid,name,*_ in rows
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")])
    await call.message.edit_text("Выберите товар для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@dp.callback_query(F.data.startswith("del:"))
async def delete_product(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    pid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        await db.commit()
    await call.answer("Товар удалён")
    await admin(call)


async def main():
    global bot
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN")
    await db_init()
    bot = Bot(BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
